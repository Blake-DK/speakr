"""Chats with a language model, inside a project.

Carried over from the standalone app the studio replaces, behaviour kept:

- a chat belongs to its project; everyone who sees the project (owner,
  People list, site admin; src/studio/access.py) sees its chats and may write
  in them. Renaming and deleting: the chat's maker or the project's owner;
- a plain chat takes its title from its first message (60 characters); a
  workflow chat is named after the workflow;
- sending streams the answer; regenerating replaces the last answer; editing
  the last message (one's own) removes everything after it, and the page then
  regenerates;
- web sources are added under the answer as a list of links (the cited
  pages; when the answer cites none, the pages the search found, marked so);
- a provider error is kept as a message of role ``error``: shown after a
  reload, never sent to a provider, removed by regenerate and edit;
- the project's saved documents (src/studio/documents.py) and its voice
  references go with every answer, except those the person switched off in
  the context bar (``excluded_doc_ids``, ``excluded_voice_ids``);
- Stop, or a lost connection, keeps what was written so far, marked
  "(stopped)".

Streaming (``stream_response``): one Server-Sent Events response per answer,
from a generator that holds its own app context, as Speakr's own chat route
does, so the request's database session is not held open while the model
writes. Nothing is written to the database while the answer streams apart
from a short heartbeat. The answer is saved when the stream ends, however it
ends: normally, on a provider error, on an unexpected error, or when gunicorn
closes the generator because the browser went away (Stop aborts the request).

One answer at a time per chat: ``busy_until`` and ``busy_token`` on the chat
are taken with one conditional UPDATE before anything is written, extended
by the stream every 20 seconds, and given back when it ends. A second send,
regenerate or edit meanwhile is refused (409). A worker that died mid-answer
stops extending, and the chat is free again after two minutes.
"""

import json
import logging
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import exists, or_

from src.database import db
from src.studio import access, attachments, providers, workflows

logger = logging.getLogger(__name__)

TITLE_MAX = 60
MESSAGE_MAX = 100_000
LOCK_SECONDS = 120
HEARTBEAT_SECONDS = 20
SNIPPET_LENGTH = 80
STOPPED_SUFFIX = "\n\n*(stopped)*"
SEARCH_MAX = 200


class ChatError(Exception):
    """A refused chat action; ``key`` names the studio string for it."""

    def __init__(self, key, status=400, **params):
        super().__init__(key)
        self.key = key
        self.status = status
        self.params = params


def _iso(value):
    return value.isoformat() + 'Z' if value else None


def kinds():
    return ('chat',) + tuple(workflows.WORKFLOWS)


# --- finding ----------------------------------------------------------------

def chat_for(chat_id, user):
    """(chat, project, role) when the user may see the chat's project, else (None, None, None)."""
    from src.studio.models import StudioChat
    chat = db.session.get(StudioChat, chat_id) if isinstance(chat_id, int) else None
    if chat is None:
        return None, None, None
    project, role = access.project_for(chat.project_id, user)
    if project is None:
        return None, None, None
    return chat, project, role


def may_manage(chat, role, user):
    """Rename or delete: the chat's maker or the project's owner (or an administrator acting as the owner)."""
    if role == access.ADMIN:
        from src.studio.models import StudioProject
        project = db.session.get(StudioProject, chat.project_id)
        if project is not None and access.acts_as_owner(project, role, user):
            return True
    return role == access.OWNER or (chat.created_by is not None and chat.created_by == user.id)


def is_busy(chat, now=None):
    now = now or datetime.utcnow()
    return chat.busy_until is not None and chat.busy_until > now


def _usernames(ids):
    from src.models import User
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.username for u in User.query.filter(User.id.in_(ids)).all()}


def to_dict(chat, role, user, names=None, snippet=None):
    names = names if names is not None else _usernames([chat.created_by])
    workflow = workflows.WORKFLOWS.get(chat.kind)
    return {
        'id': chat.id,
        'project_id': chat.project_id,
        'title': chat.title,
        'kind': chat.kind,
        'workflow_title': workflow['title'] if workflow else None,
        'provider': chat.provider,
        'created_by': chat.created_by,
        'created_by_name': names.get(chat.created_by),
        'created_at': _iso(chat.created_at),
        'updated_at': _iso(chat.updated_at),
        'busy': is_busy(chat),
        'can_manage': may_manage(chat, role, user),
        'match_snippet': snippet,
    }


def _snippet(content, q):
    """About 80 characters of the first match, centred on it."""
    idx = content.lower().find(q.lower())
    if idx == -1:
        idx = 0
    start = max(0, idx - 30)
    end = min(len(content), start + SNIPPET_LENGTH)
    return ("…" if start > 0 else "") + content[start:end] + ("…" if end < len(content) else "")


def _like(q):
    escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def list_chats(project, role, user, q=''):
    """The project's chats, newest first; with q, those whose title or a message contains it, with a snippet."""
    from src.studio.models import StudioChat, StudioMessage
    q = (q or '').strip()[:SEARCH_MAX]
    query = StudioChat.query.filter(StudioChat.project_id == project.id)
    if q:
        like = _like(q)
        query = query.filter(or_(
            StudioChat.title.ilike(like, escape='\\'),
            exists().where(StudioMessage.chat_id == StudioChat.id).where(StudioMessage.content.ilike(like, escape='\\')),
        ))
    chats = query.order_by(StudioChat.created_at.desc(), StudioChat.id.desc()).all()
    names = _usernames([c.created_by for c in chats])
    result = []
    for chat in chats:
        snippet = None
        if q:
            first = StudioMessage.query.filter(StudioMessage.chat_id == chat.id,
                                               StudioMessage.content.ilike(_like(q), escape='\\')).order_by(
                StudioMessage.id).first()
            snippet = _snippet(first.content, q) if first is not None else None
        result.append(to_dict(chat, role, user, names, snippet))
    return result


def _refs(message):
    try:
        refs = json.loads(message.attachments or '[]')
    except (TypeError, ValueError):
        return []
    return [r for r in refs if isinstance(r, dict)] if isinstance(refs, list) else []


def message_dicts(chat, user):
    """The chat's messages for the page, oldest first, with who sent them."""
    from src.studio.models import StudioMessage
    rows = StudioMessage.query.filter_by(chat_id=chat.id).order_by(StudioMessage.id).all()
    names = _usernames([m.sender_id for m in rows])
    last_user = next((m for m in reversed(rows) if m.role == 'user'), None)
    editable = last_user is not None and last_user.sender_id == user.id and not is_busy(chat)
    return [{
        'id': m.id,
        'role': m.role,
        'content': m.content,
        'attachments': [{'type': r.get('type'), 'id': r.get('id'), 'name': r.get('name')} for r in _refs(m)],
        'sender_id': m.sender_id,
        'sender_name': names.get(m.sender_id),
        'is_mine': m.sender_id is not None and m.sender_id == user.id,
        'provider': m.provider,
        'model': m.model,
        'created_at': _iso(m.created_at),
        'can_edit': editable and m.id == last_user.id,
    } for m in rows]


# --- making, renaming, deleting ---------------------------------------------

def create(project, user, data):
    """A new chat (plain, or a workflow's). Returns (chat, opening message or None, web search on?). Commits."""
    from src.studio.models import StudioChat
    data = data if isinstance(data, dict) else {}
    kind = data.get('kind') or 'chat'
    if kind not in kinds():
        raise ChatError('studio.chat.errors.unknownWorkflow')
    provider = data.get('provider') or 'claude'
    if provider not in providers.PROVIDERS:
        raise ChatError('studio.chat.errors.unknownProvider')
    opening, web_search, title = None, False, ''
    if kind != 'chat':
        workflow = workflows.WORKFLOWS[kind]
        opening = workflows.render_opening_message(kind, data.get('fields'))
        web_search = workflow['web_search']
        title = workflow['title']
    now = datetime.utcnow()
    chat = StudioChat(project_id=project.id, created_by=user.id, title=title, kind=kind, provider=provider,
                      created_at=now, updated_at=now)
    db.session.add(chat)
    db.session.commit()
    return chat, opening, web_search


def rename(chat, title):
    if not isinstance(title, str) or not title.strip():
        raise ChatError('studio.chat.errors.titleEmpty')
    chat.title = ' '.join(title.split())[:TITLE_MAX]
    db.session.commit()


def delete(chat):
    """Delete the chat and its messages. Commits."""
    from src.studio.models import StudioMessage
    if is_busy(chat):
        raise ChatError('studio.chat.errors.busy', 409)
    StudioMessage.query.filter_by(chat_id=chat.id).delete(synchronize_session=False)
    db.session.delete(chat)
    db.session.commit()


def delete_for_projects(project_ids):
    """The chats and messages of these projects (no commit), for the projects' deletion."""
    from src.studio.models import StudioChat, StudioMessage
    if not project_ids:
        return
    chat_ids = [cid for (cid,) in db.session.query(StudioChat.id).filter(StudioChat.project_id.in_(project_ids))]
    if chat_ids:
        StudioMessage.query.filter(StudioMessage.chat_id.in_(chat_ids)).delete(synchronize_session=False)
        StudioChat.query.filter(StudioChat.id.in_(chat_ids)).delete(synchronize_session=False)


# --- the one-answer-at-a-time hold ------------------------------------------

def acquire(chat_id):
    """Take the chat for one answer: a token, or None while another answer is being written. Commits."""
    from src.studio.models import StudioChat
    now = datetime.utcnow()
    token = secrets.token_hex(16)
    taken = StudioChat.query.filter(
        StudioChat.id == chat_id,
        or_(StudioChat.busy_until.is_(None), StudioChat.busy_until < now),
    ).update({'busy_until': now + timedelta(seconds=LOCK_SECONDS), 'busy_token': token},
             synchronize_session=False)
    db.session.commit()
    return token if taken == 1 else None


def extend(chat_id, token):
    from src.studio.models import StudioChat
    StudioChat.query.filter_by(id=chat_id, busy_token=token).update(
        {'busy_until': datetime.utcnow() + timedelta(seconds=LOCK_SECONDS)}, synchronize_session=False)
    db.session.commit()


def release(chat_id, token):
    from src.studio.models import StudioChat
    StudioChat.query.filter_by(id=chat_id, busy_token=token).update(
        {'busy_until': None, 'busy_token': None}, synchronize_session=False)


# --- editing ----------------------------------------------------------------

def edit_last(message, chat, user, content):
    """Change the chat's last user message (one's own) and remove everything after it. Commits."""
    from src.studio.models import StudioMessage
    if message.role != 'user':
        raise ChatError('studio.chat.errors.editOwnOnly')
    if message.sender_id is None or message.sender_id != user.id:
        raise ChatError('studio.chat.errors.editOwnOnly', 403)
    last_user = StudioMessage.query.filter_by(chat_id=chat.id, role='user').order_by(StudioMessage.id.desc()).first()
    if last_user is None or last_user.id != message.id:
        raise ChatError('studio.chat.errors.editLastOnly')
    content = content.strip() if isinstance(content, str) else ''
    if not content:
        raise ChatError('studio.chat.errors.empty')
    if len(content) > MESSAGE_MAX:
        raise ChatError('studio.chat.errors.tooLong', max=MESSAGE_MAX)
    token = acquire(chat.id)
    if token is None:
        raise ChatError('studio.chat.errors.busy', 409)
    try:
        message.content = content
        StudioMessage.query.filter(StudioMessage.chat_id == chat.id, StudioMessage.id > message.id).delete(
            synchronize_session=False)
        chat.updated_at = datetime.utcnow()
        release(chat.id, token)
        db.session.commit()
    except Exception:
        db.session.rollback()
        release(chat.id, token)
        db.session.commit()
        raise


# --- what goes to the provider ----------------------------------------------

def voice_examples(project, user, excluded_ids=()):
    """The project's voice references the person may open, oldest first: [{"name", "content"}]."""
    from src.app import has_recording_access
    from src.models import Recording
    from src.studio.models import StudioVoiceReference
    excluded = {i for i in excluded_ids if isinstance(i, int)}
    rows = StudioVoiceReference.query.filter_by(project_id=project.id).order_by(
        StudioVoiceReference.created_at, StudioVoiceReference.id).all()
    examples = []
    for row in rows:
        if row.id in excluded:
            continue
        if row.recording_id:
            rec = db.session.get(Recording, row.recording_id)
            if rec is None or not has_recording_access(rec, user):
                continue
            content = attachments.transcript_text(rec, timestamps=False)
        else:
            content = row.text
        if content:
            examples.append({'name': row.title, 'content': content})
    return examples


def build_system(chat, project, user, excluded_voice_ids=(), excluded_doc_ids=()):
    """The system prompt: the project, the workflow, its saved documents and its voice references."""
    from src.studio import documents
    return workflows.build_system(project, chat.kind,
                                  documents=documents.knowledge(project, excluded_doc_ids),
                                  voice=voice_examples(project, user, excluded_voice_ids))


def build_history(chat, project, user):
    """The chat as the provider gets it: error rows left out, attachments read with this person's access.

    All attachments of one request share attachments.MAX_ATTACH_BYTES; the
    newest are kept first and older ones become a note.
    """
    from src.studio.models import StudioMessage
    rows = StudioMessage.query.filter(StudioMessage.chat_id == chat.id, StudioMessage.role != 'error').order_by(
        StudioMessage.id).all()
    budget = attachments.MAX_ATTACH_BYTES
    loaded = {}
    for row in reversed(rows):
        if row.role != 'user':
            continue
        items = []
        for ref in _refs(row):
            weight = attachments.cost(ref)
            if weight > budget:
                items.append(attachments._note(ref, 'budget'))
                continue
            item = attachments.load(ref, project, user)
            if item is not None:
                budget -= weight
                items.append(item)
        loaded[row.id] = items
    history = [{'role': row.role, 'text': row.content, 'attachments': loaded.get(row.id, [])}
               for row in rows if row.role in ('user', 'assistant')]
    db.session.commit()  # end the reads before the answer streams
    return history


def _check_provider(name):
    if name not in providers.PROVIDERS:
        raise ChatError('studio.chat.errors.unknownProvider')
    if not providers.available(name):
        raise ChatError('studio.chat.errors.providerUnavailable', provider=providers.LABELS[name])


def _int_list(value):
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool)]


def start_send(chat, project, user, data):
    """Store the person's message and return the streaming answer. Raises ChatError or AttachmentError."""
    from src.studio.models import StudioMessage
    data = data if isinstance(data, dict) else {}
    provider = data.get('provider') or chat.provider or 'claude'
    _check_provider(provider)
    message = data.get('message')
    message = message.strip() if isinstance(message, str) else ''
    if not message:
        raise ChatError('studio.chat.errors.empty')
    if len(message) > MESSAGE_MAX:
        raise ChatError('studio.chat.errors.tooLong', max=MESSAGE_MAX)
    refs = attachments.choose(project, user, data.get('attachments'))
    token = acquire(chat.id)
    if token is None:
        raise ChatError('studio.chat.errors.busy', 409)
    try:
        now = datetime.utcnow()
        db.session.add(StudioMessage(chat_id=chat.id, role='user', content=message, attachments=json.dumps(refs),
                                     sender_id=user.id, created_at=now))
        if chat.title in ('', None):
            chat.title = ' '.join(message.split())[:TITLE_MAX]
        chat.provider = provider
        chat.updated_at = now
        db.session.flush()
        system = build_system(chat, project, user, _int_list(data.get('excluded_voice_ids')),
                              _int_list(data.get('excluded_doc_ids')))
        history = build_history(chat, project, user)  # commits the message
    except Exception:
        db.session.rollback()
        release(chat.id, token)
        db.session.commit()
        raise
    return stream_response(chat.id, token, provider, providers.model(provider), system, history,
                           bool(data.get('web_search')))


def start_regenerate(chat, project, user, data):
    """Remove the last answer (and any error after the last message) and stream a new one."""
    from src.studio.models import StudioMessage
    data = data if isinstance(data, dict) else {}
    provider = data.get('provider') or chat.provider or 'claude'
    _check_provider(provider)
    last_user = StudioMessage.query.filter_by(chat_id=chat.id, role='user').order_by(StudioMessage.id.desc()).first()
    if last_user is None:
        raise ChatError('studio.chat.errors.nothingToRegenerate')
    token = acquire(chat.id)
    if token is None:
        raise ChatError('studio.chat.errors.busy', 409)
    try:
        StudioMessage.query.filter(StudioMessage.chat_id == chat.id, StudioMessage.id > last_user.id).delete(
            synchronize_session=False)
        chat.provider = provider
        chat.updated_at = datetime.utcnow()
        db.session.flush()
        system = build_system(chat, project, user, _int_list(data.get('excluded_voice_ids')),
                              _int_list(data.get('excluded_doc_ids')))
        history = build_history(chat, project, user)
    except Exception:
        db.session.rollback()
        release(chat.id, token)
        db.session.commit()
        raise
    return stream_response(chat.id, token, provider, providers.model(provider), system, history,
                           bool(data.get('web_search')))


# --- streaming --------------------------------------------------------------

def _safe_url(url):
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url.lower().startswith(('http://', 'https://')) or len(url) > 2000:
        return None
    for ch, enc in ((' ', '%20'), ('(', '%28'), (')', '%29'), ('<', '%3C'), ('>', '%3E'), ('\n', ''), ('\r', '')):
        url = url.replace(ch, enc)
    return url


def _safe_title(title, url):
    text = ' '.join(str(title or url).split())[:200]
    return text.replace('[', '(').replace(']', ')') or url


SOURCES_HEADINGS = {'cited': 'Sources', 'searched': 'Web pages the search found'}


def sources_markdown(sources, kind='cited'):
    """'**Sources**' (or, when the answer cites nothing, the pages the search found) and a list of links.

    http and https links only; '' when none is left.
    """
    lines = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        url = _safe_url(source.get('url'))
        if url:
            lines.append(f"- [{_safe_title(source.get('title'), url)}]({url})")
    heading = SOURCES_HEADINGS.get(kind, SOURCES_HEADINGS['cited'])
    return (f"\n\n**{heading}**\n" + "\n".join(lines)) if lines else ""


def _sse(event):
    return f"data: {json.dumps(event)}\n\n"


def save_answer(chat_id, token, provider, model, parts, sources, error_text, stopped, usage, sources_kind='cited'):
    """Store the answer (and an error row) and give the chat back. Commits."""
    from src.studio.models import StudioChat, StudioMessage
    try:
        chat = db.session.get(StudioChat, chat_id)
        if chat is not None:
            now = datetime.utcnow()
            text = "".join(parts) + sources_markdown(sources, sources_kind)
            if stopped:
                text += STOPPED_SUFFIX
            if text.strip():
                db.session.add(StudioMessage(chat_id=chat_id, role='assistant', content=text, provider=provider,
                                             model=model, usage=json.dumps(usage) if usage else None,
                                             created_at=now))
            if error_text:
                db.session.add(StudioMessage(chat_id=chat_id, role='error', content=error_text, provider=provider,
                                             model=model, created_at=now))
            chat.updated_at = now
        release(chat_id, token)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Studio chat %s: the answer could not be saved", chat_id)
        try:
            release(chat_id, token)
            db.session.commit()
        except Exception:
            db.session.rollback()


def stream_response(chat_id, token, provider, model, system, history, web_search):
    """The answer as Server-Sent Events. The chat is held (``token``) until the generator ends."""
    from flask import Response, current_app
    app = current_app._get_current_object()
    stream_chat = providers.module(provider).stream_chat

    def generate():
        with app.app_context():
            parts, sources, usage = [], [], {}
            sources_kind = 'cited'
            answer_model = model
            error_text = None
            stopped = False
            failed = False
            beat = time.monotonic()
            events = stream_chat(system, history, use_web_search=web_search, model=model)
            try:
                yield ": start\n\n"
                for event in events:
                    if time.monotonic() - beat >= HEARTBEAT_SECONDS:
                        beat = time.monotonic()
                        try:
                            extend(chat_id, token)
                        except Exception:
                            db.session.rollback()
                            logger.warning("Studio chat %s: the hold could not be extended", chat_id)
                    kind = event.get('type')
                    if kind == 'text':
                        parts.append(event['text'])
                        yield _sse({'type': 'text', 'text': event['text']})
                    elif kind == 'sources':
                        sources = event.get('sources') or []
                        sources_kind = event.get('kind') or 'cited'
                        md = sources_markdown(sources, sources_kind)
                        if md:
                            yield _sse({'type': 'sources', 'markdown': md})
                    elif kind == 'error':
                        error_text = event.get('message') or 'The model did not answer.'
                        yield _sse({'type': 'error', 'message': error_text})
                    elif kind == 'usage':
                        usage = event.get('usage') or {}
                        answer_model = event.get('model') or model
                    elif kind == 'keepalive':
                        yield ": keepalive\n\n"
            except GeneratorExit:
                # The browser went away (Stop, a closed tab, a lost connection):
                # keep what was written so far.
                stopped = True
                raise
            except Exception as e:
                logger.exception("Studio chat %s: unexpected error while streaming", chat_id)
                error_text = f"Unexpected error: {type(e).__name__}"
                failed = True
            finally:
                close = getattr(events, 'close', None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                save_answer(chat_id, token, provider, answer_model, parts, sources, error_text, stopped, usage,
                            sources_kind)
            if failed:
                yield _sse({'type': 'error', 'message': error_text})
            yield _sse({'type': 'done'})

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response
