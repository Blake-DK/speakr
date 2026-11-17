"""Saved documents of a project: Markdown text kept with the project.

Carried over from the standalone app the studio replaces, behaviour kept:

- a document belongs to its project; everyone who sees the project
  (src/studio/access.py) reads its documents, makes new ones and changes any
  of them. Deleting: the document's maker or the project's owner;
- an answer of a chat can be saved as a document; it takes the chat's kind
  (a plain chat, or the workflow it came from);
- the project's documents go with every chat answer as project knowledge,
  the most recently changed first, within the 16,000-character budget of
  src/studio/workflows.py, unless the person sending switched one off in the
  chat's context bar;
- each downloads as Markdown, Word, PDF or PowerPoint slides
  (src/studio/export.py).

Added: two people may have one document open at once. A save names the
version it was opened at (the document's ``updated_at``); if the document
changed since, the save is refused (409), the message names who changed it,
and nothing is written. The check and the write are one conditional UPDATE.
"""

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import defer

from src.database import db
from src.studio import access, workflows

TITLE_MAX = 200
BODY_MAX = 300_000
OTHER = 'other'


class DocumentError(Exception):
    """A refused document action; ``key`` names the studio string for it."""

    def __init__(self, key, status=400, **params):
        super().__init__(key)
        self.key = key
        self.status = status
        self.params = params


def kinds():
    return (OTHER, 'chat') + tuple(workflows.WORKFLOWS)


def _iso(value):
    return value.isoformat() + 'Z' if value else None


def version_of(document):
    """The version a save of this document must name: its ``updated_at``, as text."""
    return document.updated_at.isoformat() if document.updated_at else ''


def _parse_version(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().removesuffix('Z'))
    except ValueError:
        return None


def _usernames(ids):
    from src.models import User
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.username for u in User.query.filter(User.id.in_(ids)).all()}


# --- finding ----------------------------------------------------------------

def document_for(document_id, user):
    """(document, project, role) when the user may see the document's project, else (None, None, None)."""
    from src.studio.models import StudioDocument
    document = db.session.get(StudioDocument, document_id) if isinstance(document_id, int) else None
    if document is None:
        return None, None, None
    project, role = access.project_for(document.project_id, user)
    if project is None:
        return None, None, None
    return document, project, role


def may_delete(document, project, role, user):
    """The document's maker, or the project's owner."""
    return access.may_delete_item(project, role, user, document.created_by)


def to_dict(document, project, role, user, names=None, length=None, with_body=False):
    names = names if names is not None else _usernames([document.created_by, document.updated_by])
    workflow = workflows.WORKFLOWS.get(document.kind)
    data = {
        'id': document.id,
        'project_id': document.project_id,
        'title': document.title,
        'kind': document.kind,
        'kind_title': workflow['title'] if workflow else None,
        'created_by': document.created_by,
        'created_by_name': names.get(document.created_by),
        'updated_by': document.updated_by,
        'updated_by_name': names.get(document.updated_by),
        'created_at': _iso(document.created_at),
        'updated_at': _iso(document.updated_at),
        'version': version_of(document),
        'length': length if length is not None else len(document.body or ''),
        'can_delete': may_delete(document, project, role, user),
    }
    if with_body:
        data['body'] = document.body or ''
    return data


def list_documents(project, role, user):
    """The project's documents, the most recently changed first, without their text."""
    from src.studio.models import StudioDocument
    rows = db.session.query(StudioDocument, func.length(StudioDocument.body)).options(
        defer(StudioDocument.body)).filter(StudioDocument.project_id == project.id).order_by(
        StudioDocument.updated_at.desc(), StudioDocument.id.desc()).all()
    names = _usernames([d.created_by for d, _ in rows] + [d.updated_by for d, _ in rows])
    return [to_dict(d, project, role, user, names, length=length or 0) for d, length in rows]


# --- making, changing, deleting ---------------------------------------------

def _clean_title(value):
    if not isinstance(value, str) or not value.strip():
        raise DocumentError('studio.documents.errors.titleEmpty')
    title = ' '.join(value.split())
    if len(title) > TITLE_MAX:
        raise DocumentError('studio.documents.errors.titleTooLong', max=TITLE_MAX)
    return title


def _clean_body(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise DocumentError('studio.documents.errors.bodyInvalid')
    if len(value) > BODY_MAX:
        raise DocumentError('studio.documents.errors.bodyTooLong', max=BODY_MAX)
    return value


def answer_text(content):
    """A stored chat answer as a document's text: without the "(stopped)" mark at its end."""
    from src.studio.chats import STOPPED_SUFFIX
    text = content or ''
    if text.endswith(STOPPED_SUFFIX):
        text = text[:-len(STOPPED_SUFFIX)]
    return text


def _from_message(project, message_id):
    """(body, kind, chat title) of an answer in one of this project's chats; DocumentError otherwise."""
    from src.studio.models import StudioChat, StudioMessage
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        raise DocumentError('studio.documents.errors.answerMissing', 404)
    message = db.session.get(StudioMessage, message_id)
    chat = db.session.get(StudioChat, message.chat_id) if message is not None else None
    if chat is None or chat.project_id != project.id:
        raise DocumentError('studio.documents.errors.answerMissing', 404)
    if message.role != 'assistant':
        raise DocumentError('studio.documents.errors.notAnAnswer')
    return answer_text(message.content), chat.kind, chat.title


def create(project, user, data):
    """A new document, typed in or saved from a chat answer (``message_id``). Commits."""
    from src.studio.models import StudioDocument
    data = data if isinstance(data, dict) else {}
    if data.get('message_id') is not None:
        body, kind, chat_title = _from_message(project, data.get('message_id'))
        title = data.get('title')
        if not isinstance(title, str) or not title.strip():
            title = chat_title or ''
        title = _clean_title(title)
        body = _clean_body(body)
        if not body.strip():
            raise DocumentError('studio.documents.errors.bodyEmpty')
    else:
        title = _clean_title(data.get('title'))
        body = _clean_body(data.get('body'))
        kind = data.get('kind') or OTHER
    if kind not in kinds():
        raise DocumentError('studio.documents.errors.kind')
    now = datetime.utcnow()
    document = StudioDocument(project_id=project.id, created_by=user.id, updated_by=user.id, title=title,
                              kind=kind, body=body, created_at=now, updated_at=now)
    db.session.add(document)
    db.session.commit()
    return document


def update(document, user, data):
    """Change the title and text of a document opened at ``data["version"]``. Commits.

    Refused with 409 when the document changed since that version.
    """
    from src.studio.models import StudioDocument
    data = data if isinstance(data, dict) else {}
    opened = _parse_version(data.get('version'))
    if opened is None:
        raise DocumentError('studio.documents.errors.version')
    values = {}
    if 'title' in data:
        values['title'] = _clean_title(data.get('title'))
    if 'body' in data:
        values['body'] = _clean_body(data.get('body'))
    if not values:
        raise DocumentError('studio.documents.errors.nothing')
    # Each save is a new version, even when the clock has not moved on.
    now = max(datetime.utcnow(), opened + timedelta(microseconds=1))
    values.update({'updated_by': user.id, 'updated_at': now})
    written = StudioDocument.query.filter(StudioDocument.id == document.id,
                                          StudioDocument.updated_at == opened).update(
        values, synchronize_session=False)
    if written != 1:
        db.session.rollback()
        db.session.refresh(document)
        name = _usernames([document.updated_by]).get(document.updated_by)
        if name:
            raise DocumentError('studio.documents.errors.changedBy', 409, name=name)
        raise DocumentError('studio.documents.errors.changed', 409)
    db.session.commit()
    db.session.refresh(document)
    return document


def delete(document):
    """Delete the document. Commits."""
    db.session.delete(document)
    db.session.commit()


def delete_for_projects(project_ids):
    """The documents of these projects (no commit), for the projects' deletion."""
    from src.studio.models import StudioDocument
    if project_ids:
        StudioDocument.query.filter(StudioDocument.project_id.in_(project_ids)).delete(synchronize_session=False)


# --- what goes to the model -------------------------------------------------

def knowledge(project, excluded_ids=()):
    """The project's documents for the system prompt, most recently changed first: [{"title", "kind", "body"}]."""
    from src.studio.models import StudioDocument
    excluded = {i for i in excluded_ids if isinstance(i, int) and not isinstance(i, bool)}
    rows = StudioDocument.query.filter_by(project_id=project.id).order_by(
        StudioDocument.updated_at.desc(), StudioDocument.id.desc()).all()
    return [{'title': d.title, 'kind': d.kind, 'body': d.body or ''} for d in rows if d.id not in excluded]
