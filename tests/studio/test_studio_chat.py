"""Project chats: access, sending and streaming, errors, regenerate, edit, Stop, the one-answer hold,
attachments, the system prompt, search and the workflows. The providers are faked: no real call is made."""

import json
import re
from datetime import datetime, timedelta

import pytest

from src.app import db
from src.models import Recording, User
from src.studio import chats, providers, workflows
from src.studio.models import StudioChat, StudioMaterial, StudioMessage, StudioSetting, StudioVoiceReference
from tests.studio.support import file_part, folders_on, sharing_on, world  # noqa: F401

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


class FakeProvider:
    """Stands in for a provider module: records what it was given and yields scripted events."""

    def __init__(self, events=None):
        self.events = events if events is not None else [{'type': 'text', 'text': 'Hello'},
                                                         {'type': 'text', 'text': ' there.'},
                                                         {'type': 'usage', 'usage': {'output_tokens': 3},
                                                          'model': 'fake-model'}]
        self.calls = []
        self.closed = False

    def stream_chat(self, system, messages, use_web_search=False, model=None):
        self.calls.append({'system': system, 'messages': messages, 'web_search': use_web_search, 'model': model})
        try:
            for event in self.events:
                yield event
        finally:
            self.closed = True


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setenv('STUDIO_ANTHROPIC_API_KEY', 'test-key-not-real')
    monkeypatch.setattr(providers, 'module', lambda name: provider)
    yield provider
    StudioSetting.query.delete()
    db.session.commit()


@pytest.fixture
def owned(world, fake):
    owner = world.user()
    return world, owner, world.project(owner, 'Chat film', logline='A heist on the moon', notes='Keep it light')


def _new_chat(world, user_id, project_id, **body):
    response = world.client(user_id).post(f'/api/studio/projects/{project_id}/chats', json=body or {'kind': 'chat'})
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def _send(world, user_id, chat_id, message='Write me a tagline', **body):
    """Send and read the whole answer, as a browser does (an unread stream is closed, as by Stop)."""
    response = world.client(user_id).post(f'/api/studio/chats/{chat_id}/stream', json={'message': message, **body})
    response.get_data()
    return response


def _events(response):
    text = response.get_data(as_text=True)
    return [json.loads(line[6:]) for line in text.split('\n') if line.startswith('data: ')]


def _messages(world, user_id, chat_id):
    response = world.client(user_id).get(f'/api/studio/chats/{chat_id}')
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['messages']


# --- access -----------------------------------------------------------------

def test_a_stranger_gets_404_on_every_chat_route(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'])
    message_id = _messages(world, owner, chat['id'])[0]['id']
    stranger = world.client(world.user())
    calls = [
        ('get', f'/api/studio/projects/{project_id}/chats', None),
        ('post', f'/api/studio/projects/{project_id}/chats', {'kind': 'chat'}),
        ('get', f'/api/studio/projects/{project_id}/attachables', None),
        ('get', f'/api/studio/chats/{chat["id"]}', None),
        ('put', f'/api/studio/chats/{chat["id"]}', {'title': 'Mine now'}),
        ('delete', f'/api/studio/chats/{chat["id"]}', None),
        ('post', f'/api/studio/chats/{chat["id"]}/stream', {'message': 'hi'}),
        ('post', f'/api/studio/chats/{chat["id"]}/regenerate', {}),
        ('put', f'/api/studio/messages/{message_id}', {'content': 'changed'}),
    ]
    for method, url, body in calls:
        response = getattr(stranger, method)(url, json=body) if body is not None else getattr(stranger, method)(url)
        assert response.status_code == 404, (method, url, response.status_code)
    assert StudioChat.query.filter_by(project_id=project_id).count() == 1
    assert len(_messages(world, owner, chat['id'])) == 2


def test_signed_out_gets_no_chat(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    response = world.client().get(f'/api/studio/chats/{chat["id"]}')
    assert response.status_code in (302, 401)


def test_people_on_the_list_and_admins_see_and_write_in_chats(owned, sharing_on, folders_on):
    world, owner, project_id = owned
    member, admin = world.user(), world.user(admin=True)
    world.share(owner, project_id, member)
    chat = _new_chat(world, owner, project_id)
    assert _send(world, member, chat['id'], 'From the member').status_code == 200
    assert _send(world, admin, chat['id'], 'From the admin').status_code == 200
    rows = _messages(world, owner, chat['id'])
    senders = [(m['role'], m['sender_id']) for m in rows if m['role'] == 'user']
    assert senders == [('user', member), ('user', admin)]
    listed = world.client(member).get(f'/api/studio/projects/{project_id}/chats').get_json()['items']
    assert [c['id'] for c in listed] == [chat['id']]


def test_rename_and_delete_belong_to_the_maker_or_the_owner(owned, sharing_on, folders_on):
    world, owner, project_id = owned
    member, other = world.user(), world.user()
    world.share(owner, project_id, member, other)
    chat = _new_chat(world, member, project_id)
    assert world.client(other).put(f'/api/studio/chats/{chat["id"]}', json={'title': 'Nope'}).status_code == 403
    assert world.client(other).delete(f'/api/studio/chats/{chat["id"]}').status_code == 403
    renamed = world.client(member).put(f'/api/studio/chats/{chat["id"]}', json={'title': '  A   new  title '})
    assert renamed.status_code == 200 and renamed.get_json()['title'] == 'A new title'
    assert world.client(member).put(f'/api/studio/chats/{chat["id"]}', json={'title': '  '}).status_code == 400
    owner_chat = _new_chat(world, other, project_id)
    assert world.client(owner).delete(f'/api/studio/chats/{owner_chat["id"]}').status_code == 200
    assert db.session.get(StudioChat, owner_chat['id']) is None


# --- sending ----------------------------------------------------------------

def test_send_streams_the_answer_and_saves_both_messages(owned, fake):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    response = _send(world, owner, chat['id'], 'A tagline for a heist film, please, something with real punch to it')
    assert response.status_code == 200
    assert response.mimetype == 'text/event-stream'
    assert response.headers['Cache-Control'] == 'no-cache' and response.headers['X-Accel-Buffering'] == 'no'
    events = _events(response)
    assert [e['type'] for e in events] == ['text', 'text', 'done']
    rows = _messages(world, owner, chat['id'])
    assert [(m['role'], m['content']) for m in rows] == [
        ('user', 'A tagline for a heist film, please, something with real punch to it'),
        ('assistant', 'Hello there.')]
    stored = StudioMessage.query.filter_by(chat_id=chat['id'], role='assistant').one()
    assert stored.model == 'fake-model' and json.loads(stored.usage) == {'output_tokens': 3}
    got = db.session.get(StudioChat, chat['id'])
    assert got.title == 'A tagline for a heist film, please, something with real punc'
    assert len(got.title) == chats.TITLE_MAX
    assert got.busy_until is None and got.busy_token is None
    assert fake.calls[0]['messages'] == [{'role': 'user', 'text': rows[0]['content'], 'attachments': []}]


def test_the_title_comes_from_the_first_message_only(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'], 'First')
    _send(world, owner, chat['id'], 'Second')
    assert db.session.get(StudioChat, chat['id']).title == 'First'


def test_an_empty_or_huge_message_is_refused(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    assert _send(world, owner, chat['id'], '   ').status_code == 400
    assert _send(world, owner, chat['id'], 'x' * (chats.MESSAGE_MAX + 1)).status_code == 400
    assert StudioMessage.query.filter_by(chat_id=chat['id']).count() == 0
    assert db.session.get(StudioChat, chat['id']).busy_until is None


def test_a_provider_without_a_key_or_switched_off_is_refused(owned, monkeypatch):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    response = _send(world, owner, chat['id'], provider='openai')
    assert response.status_code == 400 and 'GPT' in response.get_json()['error']
    providers.set_setting('claude_enabled', '0')
    db.session.commit()
    assert _send(world, owner, chat['id']).status_code == 400
    monkeypatch.delenv('STUDIO_ANTHROPIC_API_KEY')
    providers.set_setting('claude_enabled', '1')
    db.session.commit()
    assert _send(world, owner, chat['id']).status_code == 400
    assert StudioMessage.query.filter_by(chat_id=chat['id']).count() == 0


def test_web_sources_are_added_as_links_http_and_https_only(owned, fake):
    world, owner, project_id = owned
    fake.events = [{'type': 'text', 'text': 'Answer.'},
                   {'type': 'sources', 'sources': [
                       {'title': 'A [good] source', 'url': 'https://example.com/a page(1)'},
                       {'title': 'Script', 'url': 'javascript:alert(1)'},
                       {'title': None, 'url': 'http://example.org/b'}]}]
    chat = _new_chat(world, owner, project_id)
    events = _events(_send(world, owner, chat['id'], web_search=True))
    assert fake.calls[0]['web_search'] is True
    sources = next(e for e in events if e['type'] == 'sources')['markdown']
    answer = _messages(world, owner, chat['id'])[-1]['content']
    assert answer == 'Answer.' + sources
    assert '[A (good) source](https://example.com/a%20page%281%29)' in answer
    assert '[http://example.org/b](http://example.org/b)' in answer
    assert 'javascript' not in answer


# --- errors -----------------------------------------------------------------

def test_a_provider_error_is_kept_shown_after_a_reload_and_never_sent_on(owned, fake):
    world, owner, project_id = owned
    fake.events = [{'type': 'error', 'message': 'The Claude API key was refused.'}]
    chat = _new_chat(world, owner, project_id)
    events = _events(_send(world, owner, chat['id'], 'hi'))
    assert [e['type'] for e in events] == ['error', 'done']
    rows = _messages(world, owner, chat['id'])
    assert [(m['role'], m['content']) for m in rows] == [('user', 'hi'), ('error', 'The Claude API key was refused.')]

    fake.events = [{'type': 'text', 'text': 'Fine now.'}]
    _send(world, owner, chat['id'], 'again')
    sent = fake.calls[-1]['messages']
    assert [m['role'] for m in sent] == ['user', 'user']
    assert all('refused' not in m['text'] for m in sent)


def test_regenerate_removes_the_error_and_the_old_answer(owned, fake):
    world, owner, project_id = owned
    fake.events = [{'type': 'error', 'message': 'Rate-limited.'}]
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'], 'hi')
    fake.events = [{'type': 'error', 'message': 'Rate-limited again.'}]
    response = world.client(owner).post(f'/api/studio/chats/{chat["id"]}/regenerate', json={})
    assert response.status_code == 200
    response.get_data()
    rows = _messages(world, owner, chat['id'])
    assert [(m['role'], m['content']) for m in rows] == [('user', 'hi'), ('error', 'Rate-limited again.')]

    fake.events = [{'type': 'text', 'text': 'Version two.'}]
    _events(world.client(owner).post(f'/api/studio/chats/{chat["id"]}/regenerate', json={}))
    rows = _messages(world, owner, chat['id'])
    assert [(m['role'], m['content']) for m in rows] == [('user', 'hi'), ('assistant', 'Version two.')]
    assert fake.calls[-1]['messages'] == [{'role': 'user', 'text': 'hi', 'attachments': []}]


def test_regenerate_needs_a_message(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    assert world.client(owner).post(f'/api/studio/chats/{chat["id"]}/regenerate', json={}).status_code == 400


def test_an_unexpected_error_still_saves_what_was_written(owned, fake):
    world, owner, project_id = owned

    def broken(system, messages, use_web_search=False, model=None):
        yield {'type': 'text', 'text': 'Half an'}
        raise RuntimeError('boom')

    fake.stream_chat = broken
    chat = _new_chat(world, owner, project_id)
    events = _events(_send(world, owner, chat['id'], 'hi'))
    assert [e['type'] for e in events] == ['text', 'error', 'done']
    assert events[1]['message'] == 'Unexpected error: RuntimeError'
    rows = _messages(world, owner, chat['id'])
    assert [(m['role'], m['content']) for m in rows] == [
        ('user', 'hi'), ('assistant', 'Half an'), ('error', 'Unexpected error: RuntimeError')]
    assert db.session.get(StudioChat, chat['id']).busy_until is None


# --- edit -------------------------------------------------------------------

def test_editing_the_last_own_message_removes_what_follows(owned, fake, sharing_on, folders_on):
    world, owner, project_id = owned
    member = world.user()
    world.share(owner, project_id, member)
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'], 'first')
    _send(world, owner, chat['id'], 'second')
    rows = _messages(world, owner, chat['id'])
    first_user, last_user = rows[0], rows[2]
    assert [m['can_edit'] for m in rows] == [False, False, True, False]

    assert world.client(owner).put(f'/api/studio/messages/{first_user["id"]}',
                                   json={'content': 'x'}).status_code == 400
    assert world.client(member).put(f'/api/studio/messages/{last_user["id"]}',
                                    json={'content': 'x'}).status_code == 403
    assert world.client(owner).put(f'/api/studio/messages/{last_user["id"]}',
                                   json={'content': '  '}).status_code == 400
    response = world.client(owner).put(f'/api/studio/messages/{last_user["id"]}', json={'content': 'second, better'})
    assert response.status_code == 200
    assert [(m['role'], m['content']) for m in response.get_json()['messages']] == [
        ('user', 'first'), ('assistant', 'Hello there.'), ('user', 'second, better')]
    _events(world.client(owner).post(f'/api/studio/chats/{chat["id"]}/regenerate', json={}))
    assert fake.calls[-1]['messages'][-1]['text'] == 'second, better'
    assert len(_messages(world, owner, chat['id'])) == 4


# --- Stop and the one-answer hold -------------------------------------------

def test_stop_keeps_the_partial_answer_marked_stopped(owned, fake):
    world, owner, project_id = owned
    fake.events = [{'type': 'text', 'text': 'Once upon'}, {'type': 'text', 'text': ' a time'},
                   {'type': 'text', 'text': ' never sent'}]
    chat = _new_chat(world, owner, project_id)
    response = world.client(owner).post(f'/api/studio/chats/{chat["id"]}/stream', json={'message': 'tell me'},
                                        buffered=False)
    received = ''
    for chunk in response.response:
        received += chunk.decode() if isinstance(chunk, bytes) else chunk
        if 'a time' in received:
            break
    response.close()  # what gunicorn does when the browser goes away
    assert 'never sent' not in received
    db.session.expire_all()
    rows = _messages(world, owner, chat['id'])
    assert [(m['role'], m['content']) for m in rows] == [('user', 'tell me'),
                                                         ('assistant', 'Once upon a time' + chats.STOPPED_SUFFIX)]
    assert fake.closed
    assert db.session.get(StudioChat, chat['id']).busy_until is None


def test_a_second_send_while_an_answer_is_written_is_refused(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    token = chats.acquire(chat['id'])
    assert token
    response = _send(world, owner, chat['id'], 'meanwhile')
    assert response.status_code == 409
    assert 'being written' in response.get_json()['error']
    assert world.client(owner).post(f'/api/studio/chats/{chat["id"]}/regenerate', json={}).status_code in (400, 409)
    assert world.client(owner).delete(f'/api/studio/chats/{chat["id"]}').status_code == 409
    assert StudioMessage.query.filter_by(chat_id=chat['id']).count() == 0
    chats.release(chat['id'], token)
    db.session.commit()
    assert _send(world, owner, chat['id'], 'now').status_code == 200


def test_a_hold_left_by_a_dead_worker_runs_out(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    row = db.session.get(StudioChat, chat['id'])
    row.busy_until = datetime.utcnow() - timedelta(seconds=1)
    row.busy_token = 'gone'
    db.session.commit()
    assert _send(world, owner, chat['id'], 'later').status_code == 200
    db.session.expire_all()
    assert db.session.get(StudioChat, chat['id']).busy_token is None


def test_only_one_of_two_takers_gets_the_hold(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    first, second = chats.acquire(chat['id']), chats.acquire(chat['id'])
    assert first and second is None
    chats.release(chat['id'], 'not-the-token')
    db.session.commit()
    assert chats.acquire(chat['id']) is None
    chats.release(chat['id'], first)
    db.session.commit()


# --- attachments --------------------------------------------------------------

def _upload(world, user_id, project_id, content, name):
    response = world.client(user_id).post(f'/api/studio/projects/{project_id}/materials',
                                          data={'files': [file_part(content, name)]},
                                          content_type='multipart/form-data')
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()['stored'][0]['id']


def _transcribed(world, owner_id, folder_id, title='Kick-off call', text='We open on the moon.'):
    rec_id = world.recording(owner_id, folder_id=folder_id, title=title)
    rec = db.session.get(Recording, rec_id)
    rec.transcription = json.dumps([{'speaker': 'SPEAKER_00', 'sentence': text, 'start_time': 61.0,
                                     'end_time': 64.0}])
    db.session.commit()
    return rec_id


def _folder(world, owner, project_id):
    return world.client(owner).get(f'/api/studio/projects/{project_id}').get_json()['folder_id']


def test_materials_and_transcripts_go_to_the_model(owned, fake):
    world, owner, project_id = owned
    pdf_id = _upload(world, owner, project_id, PDF, 'deck.pdf')
    png_id = _upload(world, owner, project_id, PNG, 'still.png')
    rec_id = _transcribed(world, owner, _folder(world, owner, project_id))
    attachables = world.client(owner).get(f'/api/studio/projects/{project_id}/attachables').get_json()
    assert {m['id'] for m in attachables['materials']} == {pdf_id, png_id}
    assert [(r['id'], r['cut']) for r in attachables['recordings']] == [(rec_id, False)]

    chat = _new_chat(world, owner, project_id)
    response = _send(world, owner, chat['id'], 'Use these', attachments=[
        {'type': 'material', 'id': pdf_id}, {'type': 'material', 'id': png_id}, {'type': 'recording', 'id': rec_id},
        {'type': 'material', 'id': pdf_id}])
    assert response.status_code == 200, response.get_data(as_text=True)
    sent = fake.calls[0]['messages'][0]['attachments']
    assert [(a['kind'], a['name']) for a in sent] == [('pdf', 'deck.pdf'), ('image', 'still.png'),
                                                      ('text', 'Kick-off call')]
    assert sent[0]['data'] == PDF and sent[1]['content_type'] == 'image/png'
    assert sent[2]['text'] == 'Transcript of the recording "Kick-off call":\n[00:01:01] We open on the moon.'
    stored = _messages(world, owner, chat['id'])[0]['attachments']
    assert [a['name'] for a in stored] == ['deck.pdf', 'still.png', 'Kick-off call']


def test_attachments_are_checked(owned, monkeypatch):
    world, owner, project_id = owned
    other_project = world.project(owner, 'Another film')
    elsewhere = _upload(world, owner, other_project, PDF, 'other.pdf')
    pending = world.recording(owner, folder_id=_folder(world, owner, project_id), status='PROCESSING')
    chat = _new_chat(world, owner, project_id)
    for bad, status in (([{'type': 'material', 'id': elsewhere}], 404),
                        ([{'type': 'recording', 'id': pending}], 404),
                        ([{'type': 'file', 'id': 1}], 400),
                        ([{'type': 'material', 'id': 'one'}], 400),
                        ('all of them', 400)):
        response = _send(world, owner, chat['id'], attachments=bad)
        assert response.status_code == status, (bad, response.get_data(as_text=True))
    from src.studio import attachments
    monkeypatch.setattr(attachments, 'MAX_ATTACH_BYTES', 10)
    mine = _upload(world, owner, project_id, PDF, 'big.pdf')
    response = _send(world, owner, chat['id'], attachments=[{'type': 'material', 'id': mine}])
    assert response.status_code == 400 and 'Too much' in response.get_json()['error']
    monkeypatch.setattr(attachments, 'MAX_ATTACHMENTS_PER_MESSAGE', 1)
    response = _send(world, owner, chat['id'], attachments=[{'type': 'material', 'id': mine},
                                                            {'type': 'material', 'id': elsewhere}])
    assert response.status_code == 400
    assert StudioMessage.query.filter_by(chat_id=chat['id']).count() == 0


def test_a_replayed_attachment_is_read_with_the_access_of_the_person_sending(owned, fake, sharing_on, folders_on):
    world, owner, project_id = owned
    member, admin = world.user(), world.user(admin=True)
    world.share(owner, project_id, member)
    rec_id = _transcribed(world, member, _folder(world, owner, project_id), title='Member call',
                          text='Only for the group.')
    chat = _new_chat(world, member, project_id)
    assert _send(world, member, chat['id'], 'Discuss', attachments=[{'type': 'recording', 'id': rec_id}]
                 ).status_code == 200
    assert 'Only for the group.' in fake.calls[-1]['messages'][0]['attachments'][0]['text']
    # The site admin sees the project but may not open the member's recording.
    assert _send(world, admin, chat['id'], 'And me?').status_code == 200
    replayed = fake.calls[-1]['messages'][0]['attachments']
    assert replayed[0]['kind'] == 'text'
    assert 'Only for the group.' not in replayed[0]['text']
    assert 'no longer available' in replayed[0]['text']
    # Nor attach it.
    assert _send(world, admin, chat['id'], 'Try', attachments=[{'type': 'recording', 'id': rec_id}]
                 ).status_code == 404


def test_a_deleted_material_becomes_a_note(owned, fake):
    world, owner, project_id = owned
    pdf_id = _upload(world, owner, project_id, PDF, 'gone.pdf')
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'], 'one', attachments=[{'type': 'material', 'id': pdf_id}])
    assert world.client(owner).delete(f'/api/studio/materials/{pdf_id}').status_code == 200
    _send(world, owner, chat['id'], 'two')
    first = fake.calls[-1]['messages'][0]['attachments'][0]
    assert first['kind'] == 'text' and '"gone.pdf" is no longer available' in first['text']


def test_older_attachments_give_way_to_newer_ones_within_the_request_budget(owned, fake, monkeypatch):
    world, owner, project_id = owned
    from src.studio import attachments
    old_id = _upload(world, owner, project_id, PDF, 'old.pdf')
    new_id = _upload(world, owner, project_id, PDF, 'new.pdf')
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'], 'one', attachments=[{'type': 'material', 'id': old_id}])
    monkeypatch.setattr(attachments, 'MAX_ATTACH_BYTES', len(PDF) + 1)
    _send(world, owner, chat['id'], 'two', attachments=[{'type': 'material', 'id': new_id}])
    sent = fake.calls[-1]['messages']
    assert sent[2]['attachments'][0]['kind'] == 'pdf'
    assert 'left out of this request' in sent[0]['attachments'][0]['text']


def test_a_long_transcript_is_cut_with_a_line_that_says_so(owned, fake, monkeypatch):
    world, owner, project_id = owned
    from src.studio import attachments
    monkeypatch.setattr(attachments, 'MAX_TRANSCRIPT_CHARS', 12)
    rec_id = _transcribed(world, owner, _folder(world, owner, project_id), text='A' * 40)
    listed = world.client(owner).get(f'/api/studio/projects/{project_id}/attachables').get_json()['recordings']
    assert listed[0]['cut'] is True
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'], 'go', attachments=[{'type': 'recording', 'id': rec_id}])
    text = fake.calls[-1]['messages'][0]['attachments'][0]['text']
    assert 'Transcript cut here: the first 12 of its' in text


# --- the system prompt ----------------------------------------------------------

def test_the_system_prompt_has_the_project_the_workflow_and_the_voice(owned, fake, sharing_on, folders_on):
    world, owner, project_id = owned
    member = world.user()
    folder = _folder(world, owner, project_id)
    own_rec = _transcribed(world, owner, folder, title='Owner call', text='Punchy and warm.')
    world.share(owner, project_id, member)  # the folder's recordings are shared with the member now
    assert world.client(owner).post(f'/api/studio/projects/{project_id}/voice',
                                    json={'recording_id': own_rec}).status_code == 201
    pasted = world.client(owner).post(f'/api/studio/projects/{project_id}/voice',
                                      json={'title': 'Old trailer', 'text': 'Dry. Clipped. Funny.'}).get_json()['id']
    chat = _new_chat(world, member, project_id, kind='investor', fields={'ask': '£1m'})
    assert chat['opening_message'].startswith('Prepare investor materials')
    _send(world, member, chat['id'], chat['opening_message'])
    system = fake.calls[-1]['system']
    assert system.startswith('You are a marketing and pitch assistant for independent filmmakers.')
    assert 'Project: Chat film' in system and 'A heist on the moon' in system and 'Keep it light' in system
    assert workflows.WORKFLOWS['investor']['system'] in system
    assert 'VOICE & TONE REFERENCE' in system
    assert 'Punchy and warm.' in system and 'Dry. Clipped. Funny.' in system
    assert 'PROJECT KNOWLEDGE' not in system

    _send(world, member, chat['id'], 'Less voice', excluded_voice_ids=[pasted])
    assert 'Dry. Clipped. Funny.' not in fake.calls[-1]['system']
    assert 'Punchy and warm.' in fake.calls[-1]['system']


def test_voice_from_a_recording_the_sender_cannot_open_is_left_out(owned, fake, sharing_on, folders_on):
    world, owner, project_id = owned
    admin = world.user(admin=True)
    rec = _transcribed(world, owner, _folder(world, owner, project_id), text='Private tone.')
    world.client(owner).post(f'/api/studio/projects/{project_id}/voice', json={'recording_id': rec})
    chat = _new_chat(world, admin, project_id)
    _send(world, admin, chat['id'], 'hello')
    assert 'Private tone.' not in fake.calls[-1]['system']


def test_the_base_prompt_names_no_product_and_holds_no_address():
    texts = [workflows.BASE_SYSTEM, workflows.VOICE_SYSTEM, workflows.KNOWLEDGE_SYSTEM]
    for w in workflows.WORKFLOWS.values():
        texts += [w['title'], w['description'], w['system'], w['template']] + [
            f['label'] + ' ' + f['placeholder'] for f in w['fields']]
    import os
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(here, 'static', 'locales-studio', 'en.json'), encoding='utf-8') as fh:
        texts.append(fh.read())
    assert not re.match(r'You are [A-Z][\w-]*,', workflows.BASE_SYSTEM)
    for text in texts:
        assert not re.search(r'[\w.-]+@[\w-]+\.\w+', text), text[:80]
        assert not re.search(r'\b[\w-]+\.(com|net|org|io|dk|uk|co)\b', text), text[:80]
        assert not re.search(r'https?://', text), text[:80]


# --- workflows ----------------------------------------------------------------

def test_every_workflow_opening_message_renders(owned):
    world, owner, project_id = owned
    assert len(workflows.WORKFLOWS) == 8
    assert set(workflows.WORKFLOWS) == {'marketing', 'campaign', 'investor', 'planning', 'coverage', 'pitchdeck',
                                        'epk', 'comparison'}
    for kind, workflow in workflows.WORKFLOWS.items():
        filled = {f['id']: f'value for {f["id"]}' for f in workflow['fields']}
        message = workflows.render_opening_message(kind, filled)
        assert all(f'value for {f["id"]}' in message for f in workflow['fields'])
        assert '{' not in message and '}' not in message
        empty = workflows.render_opening_message(kind, {})
        assert '(no preference — use your judgement)' in empty
        chat = _new_chat(world, owner, project_id, kind=kind, fields=filled)
        assert chat['title'] == workflow['title'] and chat['opening_message'] == message
        assert chat['web_search'] is (kind == 'comparison')


def test_an_unknown_workflow_is_refused_and_the_list_has_no_prompts(owned):
    world, owner, project_id = owned
    assert world.client(owner).post(f'/api/studio/projects/{project_id}/chats',
                                    json={'kind': 'nope'}).status_code == 400
    config = world.client(owner).get('/api/studio/chat-config').get_json()
    assert len(config['workflows']) == 8
    assert all('system' not in w and 'template' not in w for w in config['workflows'])
    assert config['providers']['claude']['available'] is True
    assert 'test-key-not-real' not in json.dumps(config)


# --- search -------------------------------------------------------------------

def test_search_finds_titles_and_messages_with_a_snippet(owned, fake):
    world, owner, project_id = owned
    a = _new_chat(world, owner, project_id)
    _send(world, owner, a['id'], 'Something about the lunar rover chase scene in act two')
    b = _new_chat(world, owner, project_id)
    _send(world, owner, b['id'], 'Budget questions')
    client = world.client(owner)
    found = client.get(f'/api/studio/projects/{project_id}/chats?q=ROVER').get_json()['items']
    assert [c['id'] for c in found] == [a['id']]
    assert 'rover' in found[0]['match_snippet']
    assert client.get(f'/api/studio/projects/{project_id}/chats?q=budget').get_json()['items'][0]['id'] == b['id']
    assert client.get(f'/api/studio/projects/{project_id}/chats?q=%25').get_json()['items'] == []
    assert client.get(f'/api/studio/projects/{project_id}/chats?q=_').get_json()['items'] == []
    everything = client.get(f'/api/studio/projects/{project_id}/chats').get_json()['items']
    assert [c['id'] for c in everything] == [b['id'], a['id']]


# --- deleting -------------------------------------------------------------------

def test_chats_go_with_their_project(owned):
    world, owner, project_id = owned
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'])
    assert world.client(owner).delete(f'/api/studio/projects/{project_id}').status_code == 200
    assert db.session.get(StudioChat, chat['id']) is None
    assert StudioMessage.query.filter_by(chat_id=chat['id']).count() == 0


def test_messages_stay_when_their_sender_account_is_deleted(owned, sharing_on, folders_on):
    world, owner, project_id = owned
    member = world.user()
    world.share(owner, project_id, member)
    chat = _new_chat(world, member, project_id)
    _send(world, member, chat['id'], 'Remember me')
    db.session.delete(db.session.get(User, member))
    db.session.commit()
    rows = _messages(world, owner, chat['id'])
    assert rows[0]['content'] == 'Remember me' and rows[0]['sender_id'] is None
    assert db.session.get(StudioChat, chat['id']).created_by is None


# --- settings ---------------------------------------------------------------------

def test_the_chat_settings_are_for_administrators_only(owned):
    world, owner, project_id = owned
    user = world.client(owner)
    assert user.get('/studio/settings').status_code == 404
    assert user.get('/api/studio/settings').status_code == 404
    assert user.put('/api/studio/settings', json={'claude_model': 'x'}).status_code == 404
    assert user.post('/api/studio/settings/test', json={'provider': 'claude'}).status_code == 404
    assert user.get('/api/studio/settings/models').status_code == 404


def test_an_administrator_picks_the_model_and_switches_a_provider_off(owned, fake):
    world, owner, project_id = owned
    admin = world.client(world.user(admin=True))
    assert admin.get('/studio/settings').status_code == 200
    body = admin.get('/api/studio/settings').get_json()
    assert body['providers']['claude']['model'] == 'claude-opus-5-5'
    assert body['providers']['claude']['has_key'] is True and body['providers']['openai']['has_key'] is False
    assert 'test-key-not-real' not in json.dumps(body)
    assert admin.put('/api/studio/settings', json={'claude_model': 'bad model; drop'}).status_code == 400
    changed = admin.put('/api/studio/settings', json={'claude_model': 'claude-sonnet-5-5', 'openai_enabled': False})
    assert changed.status_code == 200
    assert changed.get_json()['providers']['claude']['model'] == 'claude-sonnet-5-5'
    assert changed.get_json()['providers']['openai']['enabled'] is False
    chat = _new_chat(world, owner, project_id)
    _send(world, owner, chat['id'])
    assert fake.calls[-1]['model'] == 'claude-sonnet-5-5'
    assert admin.put('/api/studio/settings', json={'claude_model': ''}).get_json()[
        'providers']['claude']['model'] == 'claude-opus-5-5'
    assert admin.post('/api/studio/settings/test', json={'provider': 'openai'}).get_json()['ok'] is False


# --- voice references, shared with importers --------------------------------------

def test_voice_references_are_made_by_the_shared_functions(owned):
    from src.studio import projects, voice
    world, owner, project_id = owned
    from src.studio.models import StudioProject
    project = db.session.get(StudioProject, project_id)
    user = db.session.get(User, owner)
    rec = _transcribed(world, owner, _folder(world, owner, project_id), title='Pitch call')
    made = voice.create_from_recording(project, user, rec)
    db.session.commit()
    assert made.title == 'Pitch call' and made.recording_id == rec
    with pytest.raises(projects.ProjectError):
        voice.create_from_recording(project, user, rec)
    db.session.rollback()
    text = voice.create_from_text(project, user, '1\n00:00:01,000 --> 00:00:02,000\nHello there\n', 'Subs')
    db.session.commit()
    assert text.text == 'Hello there' and text.title == 'Subs'
    with pytest.raises(projects.ProjectError):
        voice.create_from_text(project, user, '1\n00:00:01,000 --> 00:00:02,000\n')
    db.session.rollback()
    assert StudioVoiceReference.query.filter_by(project_id=project_id).count() == 2
    assert StudioMaterial.query.filter_by(project_id=project_id).count() == 0
