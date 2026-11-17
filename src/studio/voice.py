"""Voice references: examples of how a project should sound.

A voice reference is either a recording of the project (its transcript, read
by the chat when it runs, src/studio/chats.py) or pasted text, such as the
subtitles of a finished film. Pasted text is cleaned with the subtitle
clean-up of the standalone app the studio replaces, so SRT and WebVTT files
can be pasted (or loaded) as they are.
"""

import re

MAX_TEXT_CHARS = 100_000   # as in the standalone app
MAX_TITLE_LENGTH = 200

_TIMESTAMP_LINE = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s*-->")
_TAG = re.compile(r"<[^>]+>")


def clean_transcript(raw: str) -> str:
    """Turn an SRT/VTT/plain transcript into readable prose-ish text:
    drop cue numbers, timestamps, headers and markup; collapse repeats
    (auto-captions often duplicate lines).

    Carried over from the standalone app unchanged."""
    lines = []
    previous = None
    for line in raw.splitlines():
        line = _TAG.sub("", line).strip()
        if not line:
            continue
        if line.upper().startswith(("WEBVTT", "NOTE ", "STYLE", "REGION")):
            continue
        if _TIMESTAMP_LINE.match(line):
            continue
        if line.isdigit():  # SRT cue counters
            continue
        if line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def clean_title(value, fallback):
    title = ' '.join(value.split()) if isinstance(value, str) else ''
    return (title or fallback)[:MAX_TITLE_LENGTH]


def create_from_recording(project, user, recording_id, title=None):
    """A voice reference made from a recording of the project the user may open. Added, not committed.

    Raises projects.ProjectError: the recording is not in the project or the
    user may not open it, or it is a voice reference of the project already.
    Shared by the route and by importers, so both make the same row.
    """
    from src.database import db
    from src.studio import projects
    from src.studio.i18n import studio_t
    from src.studio.models import StudioVoiceReference
    source = projects.recording_for_voice(project, user, recording_id)
    if StudioVoiceReference.query.filter_by(project_id=project.id, recording_id=source.id).first():
        raise projects.ProjectError('studio.voice.errors.already')
    fallback = projects.recording_titles([source.id], user).get(source.id) or studio_t('studio.voice.untitledRecording')
    reference = StudioVoiceReference(project_id=project.id, created_by=user.id, recording_id=source.id,
                                     title=clean_title(title, fallback))
    db.session.add(reference)
    return reference


def create_from_text(project, user, text, title=None):
    """A voice reference of pasted text (subtitles are cleaned up). Added, not committed.

    Raises projects.ProjectError when nothing is left after the clean-up.
    """
    from src.database import db
    from src.studio import projects
    from src.studio.i18n import studio_t
    from src.studio.models import StudioVoiceReference
    if not isinstance(text, str):
        raise projects.ProjectError('studio.voice.errors.empty')
    cleaned = clean_transcript(text)[:MAX_TEXT_CHARS]
    if not cleaned:
        raise projects.ProjectError('studio.voice.errors.empty')
    reference = StudioVoiceReference(project_id=project.id, created_by=user.id, text=cleaned,
                                     title=clean_title(title, studio_t('studio.voice.untitledText')))
    db.session.add(reference)
    return reference


def to_dict(reference, recording_title, can_delete, with_text=False):
    data = {
        'id': reference.id,
        'project_id': reference.project_id,
        'title': reference.title,
        'source': 'recording' if reference.recording_id else 'text',
        'recording_id': reference.recording_id,
        'recording_title': recording_title,
        'chars': len(reference.text or ''),
        'created_by': reference.created_by,
        'created_at': reference.created_at.isoformat() + 'Z' if reference.created_at else None,
        'can_delete': can_delete,
    }
    if with_text:
        data['text'] = reference.text or ''
    return data
