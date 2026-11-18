"""No placeholder speaker (src/studio/speaker_view.py): the rule, and every seam in
upstream's files that uses it, on a made-up recording. The stored transcript is
never changed. Hermetic and offline."""

import json
import os
import re
import uuid

import pytest

from src.app import app, db
from src.models import Recording, Share, User
from src.studio import speaker_view
from tests.studio.support import World

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(ROOT, 'tests', 'fixtures', 'studio_placeholder_speaker_cases.json'), encoding='utf-8') as _fh:
    CASES = json.load(_fh)['cases']

SENTENCES = ['First made-up line.', 'Second made-up line.', 'Third made-up line.']


def _segments(*speakers):
    return [{'speaker': speaker, 'sentence': SENTENCES[i % 3], 'start_time': i * 2.0, 'end_time': i * 2.0 + 1.5}
            for i, speaker in enumerate(speakers)]


PLACEHOLDER = _segments('SPEAKER_00', 'SPEAKER_00', 'SPEAKER_00')
TWO_VOICES = _segments('SPEAKER_00', 'SPEAKER_01', 'SPEAKER_00')


@pytest.fixture(autouse=True)
def rule_on(monkeypatch):
    monkeypatch.delenv(speaker_view.SETTING, raising=False)


# --- the rule ----------------------------------------------------------------

@pytest.mark.parametrize('case', CASES, ids=[c['why'] for c in CASES])
def test_the_rule(case):
    segments = [{'speaker': s, 'sentence': 'x'} for s in case['speakers']]
    assert speaker_view.hides(segments) is case['hidden']


def test_the_setting_switches_it_off(monkeypatch):
    monkeypatch.setenv(speaker_view.SETTING, 'false')
    assert not speaker_view.hides(PLACEHOLDER)
    assert speaker_view.without_placeholder(PLACEHOLDER) is PLACEHOLDER


def test_copies_never_the_segments_themselves():
    before = json.dumps(PLACEHOLDER)
    copy = speaker_view.without_placeholder(PLACEHOLDER)
    assert all('speaker' not in seg for seg in copy) and [s['sentence'] for s in copy] == [s['sentence'] for s in PLACEHOLDER]
    assert json.dumps(PLACEHOLDER) == before
    assert speaker_view.without_placeholder(TWO_VOICES) is TWO_VOICES


@pytest.mark.parametrize('template, expected', [
    ('[{{speaker}}]: {{text}}', '{{text}}'),
    ('[{{start_time}}] {{speaker}}: {{text}}', '[{{start_time}}] {{text}}'),
    ('• [{{start_time}}] {{speaker}}: {{text}}', '• [{{start_time}}] {{text}}'),
    ('{{index}}    {{speaker|upper}}: {{text}}', '{{index}}    {{text}}'),
    ('                    {{speaker|upper}}\n        {{text}}\n', '        {{text}}\n'),
    ('<v {{speaker}}>{{text}}', '{{text}}'),
    ('**{{speaker}}:** {{text}}', '{{text}}'),
    ('{{index}}\n{{start_time|srt}} --> {{end_time|srt}}\n{{text}}\n', '{{index}}\n{{start_time|srt}} --> {{end_time|srt}}\n{{text}}\n'),
])
def test_a_template_loses_its_speaker_and_what_frames_it(template, expected):
    assert speaker_view.template_without_speaker(template) == expected


# --- the seams -----------------------------------------------------------------

def test_the_text_the_models_read():
    from src.transcription_prompts import format_transcription_for_llm
    text = format_transcription_for_llm(json.dumps(PLACEHOLDER))
    assert text.splitlines() == SENTENCES
    timed = format_transcription_for_llm(json.dumps(PLACEHOLDER), include_timestamps=True)
    assert 'SPEAKER' not in timed and timed.splitlines()[0].endswith('] First made-up line.')
    assert '[SPEAKER_01]: Second made-up line.' in format_transcription_for_llm(json.dumps(TWO_VOICES))


def test_render_segments_with_upstreams_templates():
    from src.utils.transcript_render import BASIC_TEMPLATE_FORMAT, DEFAULT_TIMESTAMP_FORMAT, render_segments
    assert render_segments(PLACEHOLDER, BASIC_TEMPLATE_FORMAT).splitlines() == SENTENCES
    assert 'SPEAKER' not in render_segments(PLACEHOLDER, DEFAULT_TIMESTAMP_FORMAT)
    assert 'Unknown' not in render_segments(PLACEHOLDER, DEFAULT_TIMESTAMP_FORMAT)
    assert render_segments(TWO_VOICES, BASIC_TEMPLATE_FORMAT).splitlines()[1] == '[SPEAKER_01]: Second made-up line.'


def test_the_chats_marked_transcript():
    """segments_of and the API keep the stored label; the chat blanks it (build_chat_messages) and
    then prints no speaker and no "Unknown" in its marked transcript."""
    from src.services.recording_chat import _marked_transcript
    from src.services.transcript_segments import segments_of
    stored = segments_of(json.dumps(PLACEHOLDER))
    assert [s['speaker'] for s in stored] == ['SPEAKER_00'] * 3
    marked = _marked_transcript(speaker_view.blanked(stored))
    assert 'SPEAKER' not in marked and 'Unknown' not in marked and marked.splitlines()[0] == '[S0 00:00] First made-up line.'
    assert speaker_view.blanked(segments_of(json.dumps(TWO_VOICES)))[1]['speaker'] == 'SPEAKER_01'
    assert 'SPEAKER_01' in _marked_transcript(segments_of(json.dumps(TWO_VOICES)))


def test_the_share_pages_transcript():
    from src.api.shares import public_transcript
    shared = public_transcript(json.dumps(PLACEHOLDER))
    assert shared['speakers'] == [] and [row[0] for row in shared['segments']] == [-1, -1, -1]
    assert [row[1] for row in shared['segments']] == [0.0, 2.0, 4.0]
    assert public_transcript(json.dumps(TWO_VOICES))['speakers'] == ['SPEAKER_00', 'SPEAKER_01']


def test_the_studios_attachments():
    from src.studio.attachments import transcript_text

    class Rec:
        transcription = json.dumps(PLACEHOLDER)

    assert 'SPEAKER' not in transcript_text(Rec()) and 'Unknown' not in transcript_text(Rec())
    Rec.transcription = json.dumps([{k: v for k, v in s.items() if k != 'speaker'} for s in PLACEHOLDER])
    assert 'Unknown' not in transcript_text(Rec())  # a transcript without speakers stays without a label
    Rec.transcription = json.dumps(TWO_VOICES)
    assert 'SPEAKER_01: Second made-up line.' in transcript_text(Rec(), timestamps=False)


# --- the routes, on a made-up recording; the stored JSON stays as it is -----------

@pytest.fixture
def world():
    with app.app_context():
        made = World()
        try:
            yield made
        finally:
            db.session.rollback()
            Share.query.filter(Share.user_id.in_(made.users or [-1])).delete(synchronize_session=False)
            db.session.commit()
            made.cleanup()


@pytest.fixture
def placeholder_recording(world):
    owner = world.user(admin=True)
    recording_id = world.recording(owner, title='Made-up placeholder recording')
    recording = db.session.get(Recording, recording_id)
    recording.transcription = json.dumps(PLACEHOLDER)
    db.session.commit()
    return owner, recording_id


def _stored(recording_id):
    db.session.expire_all()
    return db.session.get(Recording, recording_id).transcription


def test_downloads_exports_and_the_share_page(world, placeholder_recording):
    from src.file_exporter import generate_markdown_content
    owner, recording_id = placeholder_recording
    client = world.client(owner)
    for fmt in ('txt', 'md'):
        response = client.get(f'/recording/{recording_id}/download/transcript?format={fmt}')
        assert response.status_code == 200, fmt
        body = response.get_data(as_text=True)
        assert 'SPEAKER' not in body and 'Unknown' not in body and 'First made-up line.' in body, fmt
    with app.test_request_context('/'):
        markdown = generate_markdown_content(db.session.get(Recording, recording_id), db.session.get(User, owner))
    assert 'SPEAKER' not in markdown and 'First made-up line.' in markdown
    share = Share(public_id=uuid.uuid4().hex, recording_id=recording_id, user_id=owner, share_summary=True, share_notes=True)
    db.session.add(share)
    db.session.commit()
    page = world.client().get(f'/share/{share.public_id}').get_data(as_text=True)
    # The transcript the page embeds has no speaker (the page's locale strings name SPEAKER_00 in an error text).
    assert re.search(r'"speakers":\s*\[\]', page) and 'First made-up line.' in page
    assert page.count('SPEAKER_00') == page.count('A diarization label such as SPEAKER_00')
    assert _stored(recording_id) == json.dumps(PLACEHOLDER)


def test_the_page_gets_the_stored_transcript_and_a_save_keeps_every_speaker(world, placeholder_recording):
    """The main page receives the stored JSON (it hides the label itself, for display); the segment editor
    saves the stored segments back, so a save keeps every segment's speaker and the segment count."""
    owner, recording_id = placeholder_recording
    client = world.client(owner)
    payload = client.get(f'/api/recordings/{recording_id}').get_json()
    stored = json.loads(payload.get('transcription') or payload.get('recording', {}).get('transcription'))
    assert [s['speaker'] for s in stored] == ['SPEAKER_00'] * 3
    edited = [dict(seg) for seg in stored]
    edited[1]['sentence'] = 'Second made-up line, edited.'
    response = client.post(f'/recording/{recording_id}/update_transcription', json={'transcription': json.dumps(edited)})
    assert response.status_code == 200, response.get_data(as_text=True)
    after = json.loads(_stored(recording_id))
    assert len(after) == 3 and [s['speaker'] for s in after] == ['SPEAKER_00'] * 3
    assert [s['sentence'] for s in after] == ['First made-up line.', 'Second made-up line, edited.', 'Third made-up line.']


def test_the_chat_names_no_placeholder_to_the_model_or_in_its_citations(world, placeholder_recording):
    from src.services.recording_chat import _speakers, build_chat_messages
    from src.services.transcript_segments import canonical_segments
    owner, recording_id = placeholder_recording
    recording, user = db.session.get(Recording, recording_id), db.session.get(User, owner)
    assert _speakers(canonical_segments(recording)) == []
    for with_sources in (True, False):
        messages, segments = build_chat_messages(recording, user, 'What was said?', with_sources=with_sources)
        text = json.dumps(messages)
        assert 'SPEAKER_00' not in text and 'Unknown]' not in text and 'First made-up line.' in text, with_sources
        if with_sources:
            assert segments and all(seg['speaker'] is None for seg in segments)
    # The API's own view of the recording's speakers is unchanged (integrations read the stored label).
    assert {seg['speaker'] for seg in canonical_segments(recording)} == {'SPEAKER_00'}
    assert _stored(recording_id) == json.dumps(PLACEHOLDER)


def test_the_main_page_gets_the_setting_and_the_stylesheet(world, monkeypatch):
    owner = world.user(admin=True)
    page = world.client(owner).get('/').get_data(as_text=True)
    assert 'window.__STUDIO_HIDE_PLACEHOLDER_SPEAKERS = true;' in page
    assert re.search(r'css/studio/main-page\.css\?v=[^"]+" data-studio-speaker-css', page)
    monkeypatch.setenv(speaker_view.SETTING, 'false')
    page = world.client(owner).get('/').get_data(as_text=True)
    assert 'window.__STUDIO_HIDE_PLACEHOLDER_SPEAKERS = false;' in page


def test_the_needs_speakers_filter_hides_until_a_second_label_turns_up(world, monkeypatch):
    owner = world.user()
    for title in ('one', 'two'):
        recording = db.session.get(Recording, world.recording(owner, title=title))
        recording.transcription = json.dumps(PLACEHOLDER)
    db.session.commit()
    assert speaker_view.hide_needs_speakers(owner) is True
    page = world.client(owner).get('/').get_data(as_text=True)
    assert "classList.add('studio-hide-needs-speakers')" in page
    other = db.session.get(Recording, world.recording(owner, title='two voices'))
    other.transcription = json.dumps(TWO_VOICES)
    db.session.commit()
    assert speaker_view.hide_needs_speakers(owner) is False
    assert "studio-hide-needs-speakers')" not in world.client(owner).get('/').get_data(as_text=True)
    other.transcription = json.dumps(_segments('SPEAKERX01', 'SPEAKER_00'))  # the underscore is matched as itself
    db.session.commit()
    assert speaker_view.hide_needs_speakers(owner) is True
    monkeypatch.setenv(speaker_view.SETTING, 'false')
    assert speaker_view.hide_needs_speakers(owner) is False


@pytest.mark.parametrize('panel', ['desktop-transcription-panel.html', 'mobile-transcript-panel.html'])
def test_the_follow_player_button_shows_for_every_timed_transcript(panel):
    with open(os.path.join(ROOT, 'templates', 'components', 'detail', panel), encoding='utf-8') as handle:
        text = handle.read()
    follow = re.search(r'<button v-if="([^"]+)"[^>]*@click="toggleFollowPlayerMode"', text)
    assert follow and follow.group(1) == 'processedTranscription.isJson'
