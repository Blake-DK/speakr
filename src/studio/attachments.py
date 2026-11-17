"""What a chat message can carry to the model: the project's materials and its recordings' transcripts.

A message stores references only (``{"type": "material" | "recording",
"id": ..., "name": ...}``). Whenever a chat's history goes to a provider, each
reference is read again with the access of the person sending that turn:

- a material: anyone who may see the project may read it; a PDF or an image
  goes as a file, a slide deck as its extracted text;
- a recording: only if Speakr lets that person open it
  (``has_recording_access``); its transcript goes as text, whole when it fits
  and otherwise cut, with a line that says so.

One that is gone, or that the person may not open, becomes a one-line note,
so the model is told something is missing and nothing is leaked.

Limits: at most 20 attachments and 20 MB per message (the files as stored,
transcripts as text), refused with a plain message above that; and the same
20 MB for all attachments of one request, the newest kept first, older ones
becoming notes. The provider's own request limit (32 MB for Claude) is then
never reached by the files.
"""

import logging

logger = logging.getLogger(__name__)

MAX_ATTACHMENTS_PER_MESSAGE = 20
MAX_ATTACH_BYTES = 20 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 150_000
TYPES = ('material', 'recording')


class AttachmentError(Exception):
    """A refused attachment; ``key`` names the studio string for it."""

    def __init__(self, key, status=400, **params):
        super().__init__(key)
        self.key = key
        self.status = status
        self.params = params


# --- transcripts ------------------------------------------------------------

def _segments(raw):
    from src.utils.transcript_render import parse_segments
    try:
        return parse_segments(raw)
    except Exception:
        return None


def transcript_text(recording, timestamps=True):
    """The recording's transcript as plain text, or None when it has none (or holds an error)."""
    from src.utils import is_transcription_error
    raw = recording.transcription or ''
    if not raw.strip() or is_transcription_error(raw):
        return None
    segments = _segments(raw)
    if segments is None:
        return raw.strip() or None
    from src.utils.transcript_render import render_segments
    speakers = {s.get('speaker') for s in segments if isinstance(s, dict)}
    if timestamps:
        # One speaker label throughout (the transcription service labels every
        # segment the same) says nothing; leave it out.
        template = '[{{start_time}}] {{text}}' if len(speakers) <= 1 else '[{{start_time}}] {{speaker}}: {{text}}'
    else:
        template = '{{text}}' if len(speakers) <= 1 else '{{speaker}}: {{text}}'
    text = render_segments(segments, template)
    return text.strip() or None


def recording_title(recording):
    return recording.title or recording.original_filename or f'#{recording.id}'


def transcript_for_model(recording):
    """(text for the model, full length, cut?) of a recording's transcript; (None, 0, False) when it has none."""
    text = transcript_text(recording)
    if text is None:
        return None, 0, False
    full = len(text)
    cut = full > MAX_TRANSCRIPT_CHARS
    if cut:
        text = (text[:MAX_TRANSCRIPT_CHARS]
                + f'\n[Transcript cut here: the first {MAX_TRANSCRIPT_CHARS:,} of its {full:,} characters are '
                  'included.]')
    return f'Transcript of the recording "{recording_title(recording)}":\n{text}', full, cut


# --- choosing ---------------------------------------------------------------

def attachable(project, user):
    """What the person may attach in this project: {"materials": [...], "recordings": [...]}."""
    from src.database import db
    from src.studio import projects
    from src.studio.models import StudioMaterial
    materials = StudioMaterial.query.filter_by(project_id=project.id).order_by(
        StudioMaterial.created_at.desc(), StudioMaterial.id.desc()).all()
    recordings = []
    for rec in projects.visible_recordings(project, user):
        if rec.status != 'COMPLETED':
            continue
        text = transcript_text(rec)
        if text is None:
            continue
        recordings.append({'id': rec.id, 'title': recording_title(rec), 'chars': len(text),
                           'cut': len(text) > MAX_TRANSCRIPT_CHARS})
    db.session.commit()  # end the read
    return {
        'materials': [{'id': m.id, 'name': m.original_name, 'kind': m.kind, 'size': m.size} for m in materials],
        'recordings': recordings,
        'max_bytes': MAX_ATTACH_BYTES,
        'max_count': MAX_ATTACHMENTS_PER_MESSAGE,
        'max_transcript_chars': MAX_TRANSCRIPT_CHARS,
    }


def choose(project, user, items):
    """Check what a message asks to attach; returns the references to store. Raises AttachmentError."""
    from src.studio import projects
    from src.studio.models import StudioMaterial
    if items is None:
        return []
    if not isinstance(items, list):
        raise AttachmentError('studio.chat.errors.attachInvalid')
    wanted = []
    for item in items:
        if not isinstance(item, dict) or item.get('type') not in TYPES:
            raise AttachmentError('studio.chat.errors.attachInvalid')
        item_id = item.get('id')
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            raise AttachmentError('studio.chat.errors.attachInvalid')
        key = (item['type'], item_id)
        if key not in wanted:
            wanted.append(key)
    if len(wanted) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise AttachmentError('studio.chat.errors.attachTooMany', max=MAX_ATTACHMENTS_PER_MESSAGE)
    refs, total = [], 0
    visible = None
    for kind, item_id in wanted:
        if kind == 'material':
            material = StudioMaterial.query.filter_by(id=item_id, project_id=project.id).first()
            if material is None:
                raise AttachmentError('studio.chat.errors.attachMissing', 404)
            total += material.size or 0
            refs.append({'type': 'material', 'id': material.id, 'name': material.original_name})
        else:
            if visible is None:
                visible = {r.id: r for r in projects.visible_recordings(project, user)}
            rec = visible.get(item_id)
            if rec is None or rec.status != 'COMPLETED':
                raise AttachmentError('studio.chat.errors.attachMissing', 404)
            text = transcript_text(rec)
            if text is None:
                raise AttachmentError('studio.chat.errors.attachNoTranscript', name=recording_title(rec))
            total += len(text.encode('utf-8')[:MAX_TRANSCRIPT_CHARS * 4])
            refs.append({'type': 'recording', 'id': rec.id, 'name': recording_title(rec)})
    if total > MAX_ATTACH_BYTES:
        raise AttachmentError('studio.chat.errors.attachTooLarge',
                              size=round(total / (1024 * 1024), 1), max=MAX_ATTACH_BYTES // (1024 * 1024))
    return refs


# --- reading ----------------------------------------------------------------

def _note(ref, why):
    name = ref.get('name') or ''
    if why == 'budget':
        return {'kind': 'text', 'name': name,
                'text': f'[The attachment "{name}" from earlier in this chat is left out of this request, '
                        'to keep it within the size limit.]'}
    return {'kind': 'text', 'name': name,
            'text': f'[The attachment "{name}" is no longer available, so it is not included.]'}


def _read_material(material):
    from src.services.storage import get_storage_service
    with get_storage_service().materialize(material.storage_locator) as stored:
        with open(stored.local_path, 'rb') as fh:
            return fh.read()


def cost(ref):
    """What an attachment weighs in a request (bytes), for the per-request budget."""
    from src.database import db
    from src.models import Recording
    from src.studio.models import StudioMaterial
    if ref.get('type') == 'material':
        material = db.session.get(StudioMaterial, ref.get('id'))
        return (material.size or 0) if material is not None else 0
    rec = db.session.get(Recording, ref.get('id'))
    if rec is None:
        return 0
    return min(len((rec.transcription or '').encode('utf-8')), MAX_TRANSCRIPT_CHARS * 4)


def load(ref, project, user):
    """One stored reference as a provider attachment, read with this person's access; a note otherwise."""
    from src.app import has_recording_access
    from src.database import db
    from src.models import Recording
    from src.studio.models import StudioMaterial
    if not isinstance(ref, dict):
        return None
    if ref.get('type') == 'material':
        material = db.session.get(StudioMaterial, ref.get('id'))
        if material is None or material.project_id != project.id:
            return _note(ref, 'gone')
        if material.kind == 'pptx':
            if not material.text:
                return _note(ref, 'gone')
            return {'kind': 'text', 'name': material.original_name, 'text': material.text}
        try:
            data = _read_material(material)
        except (OSError, ValueError, RuntimeError):
            logger.warning("Studio material %s could not be read for a chat", material.id)
            return _note(ref, 'gone')
        return {'kind': material.kind, 'name': material.original_name, 'content_type': material.content_type,
                'data': data}
    if ref.get('type') == 'recording':
        rec = db.session.get(Recording, ref.get('id'))
        if rec is None or not has_recording_access(rec, user):
            return _note(ref, 'gone')
        text, _full, _cut = transcript_for_model(rec)
        if text is None:
            return _note(ref, 'gone')
        return {'kind': 'text', 'name': recording_title(rec), 'text': text}
    return None
