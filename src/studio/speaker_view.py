"""No placeholder speaker in front of a person or a model.

This site's transcriber does not tell voices apart, so it labels every segment
of every recording with the same diarization label (SPEAKER_00). Speakr keeps
the timed segments only when they carry a speaker, so the label stays in the
stored transcript; it is left out where the transcript is shown or read:

- the rule: a transcript whose segments have exactly one distinct speaker, and
  that speaker is a generic label (upstream's rule, src/utils/speaker_labels.py),
  has its label hidden. Two labels (a transcriber that tells voices apart) or any
  real name: the labels show. Naming a speaker writes the name into the segments
  (src/services/speaker.py), so a named speaker ends the rule;
- STUDIO_HIDE_PLACEHOLDER_SPEAKERS (default on) switches it off with "false";
- stored data never changes: every function here returns a copy, and the pages
  still load and save the stored JSON.

The marked seams in upstream's files call these functions (STUDIO.md, "Where it
lives"); static/js/studio/speaker-view.js is the same rule for the pages.
"""

import os
import re

SETTING = 'STUDIO_HIDE_PLACEHOLDER_SPEAKERS'
_OFF = ('0', 'false', 'no', 'off')


def enabled():
    return os.environ.get(SETTING, '').strip().lower() not in _OFF


def hide_needs_speakers(user_id):
    """Whether the main page hides its "Needs speakers" filter for this person: while the setting is on
    and none of the recordings their list shows (get_accessible_recording_ids, as the filter itself) holds
    a second diarization label (SPEAKER_01). One EXISTS, nothing parsed. A transcriber that tells voices
    apart brings the filter back by itself; a recording with one real name and one SPEAKER_00, and no
    SPEAKER_01 anywhere, does not (STUDIO.md, "No placeholder speaker")."""
    if not enabled() or not user_id:
        return False
    from src.database import db
    from src.models import Recording
    from src.api.recordings import get_accessible_recording_ids
    ids = get_accessible_recording_ids(user_id)
    if not ids:
        return True
    second = db.session.query(db.exists().where(
        Recording.id.in_(ids), Recording.transcription.like('%SPEAKER\\_01%', escape='\\'))).scalar()
    return not second


def _speakers(segments):
    found = set()
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        name = seg.get('speaker')
        if isinstance(name, str):
            name = name.strip()
        if name:
            found.add(name)
    return found


def hides(segments):
    """True when the label of these segments (a list of dicts with 'speaker') is a placeholder to hide."""
    if not enabled() or not isinstance(segments, list):
        return False
    from src.utils.speaker_labels import is_generic_speaker_label
    speakers = _speakers(segments)
    return len(speakers) == 1 and isinstance(next(iter(speakers)), str) and is_generic_speaker_label(next(iter(speakers)))


def without_placeholder(segments):
    """Copies of the segments without 'speaker' when the rule holds; the segments themselves otherwise."""
    if not hides(segments):
        return segments
    return [{k: v for k, v in seg.items() if k != 'speaker'} if isinstance(seg, dict) else seg for seg in segments]


def blanked(segments):
    """Copies of the segments with 'speaker' set to None when the rule holds (for callers that read
    seg['speaker'] of every segment); the segments themselves otherwise."""
    if not hides(segments):
        return segments
    return [{**seg, 'speaker': None} if isinstance(seg, dict) else seg for seg in segments]


# {{speaker}} or {{speaker|upper}} with what only frames it: brackets, <v …>, ** or __,
# a following colon or dash, and the spaces after it.
_SPEAKER_TOKEN = re.compile(
    r'(?:<v\s*)?(?:\*\*|__)?\[?\{\{\s*speaker(?:\|upper)?\s*\}\}\]?(?:\s*[:\-–—])?(?:\*\*|__)?(?:\s*>)?[ \t]*')


def template_without_speaker(template_format):
    """A transcript template with its speaker placeholder (and what frames it) left out."""
    if not template_format:
        return template_format
    stripped = _SPEAKER_TOKEN.sub('', template_format)
    # A line that held only the speaker (Screenplay: "        {{speaker|upper}}\n") goes with it.
    return re.sub(r'(?m)^[ \t]*\n', '', stripped) if stripped != template_format else template_format


def template_for(segments, template_format):
    """The template to render these segments with: without the speaker when the rule holds."""
    return template_without_speaker(template_format) if hides(segments) else template_format
