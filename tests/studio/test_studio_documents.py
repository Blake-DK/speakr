"""Saved documents: making, changing and deleting them, who may, the version check, saving a chat answer,
the project knowledge in the chat's system prompt, the downloads, and what goes with a project or an account.
The chat's provider is faked: no real call is made."""

import io
from datetime import datetime

import pytest

from src.app import db
from src.models import User
from src.studio import chats, documents, providers, workflows
from src.studio.models import StudioChat, StudioDocument, StudioMessage, StudioProject, StudioSetting
from tests.studio.support import folders_on, sharing_on, world  # noqa: F401


@pytest.fixture(autouse=True)
def _admins_as_owners_off(monkeypatch):
    """These tests describe an administrator without the owner's rights: STUDIO_ADMINS_AS_OWNERS off.
    tests/studio/test_admin_owner.py covers it on."""
    monkeypatch.setenv('STUDIO_ADMINS_AS_OWNERS', 'false')



class FakeProvider:
    def __init__(self):
        self.calls = []
        self.events = [{'type': 'text', 'text': 'Noted.'}]

    def stream_chat(self, system, messages, use_web_search=False, model=None):
        self.calls.append({'system': system, 'messages': messages})
        yield from self.events


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setenv('STUDIO_ANTHROPIC_API_KEY', 'test-key-not-real')
    monkeypatch.setattr(providers, 'module', lambda name: provider)
    yield provider
    StudioSetting.query.delete()
    db.session.commit()


@pytest.fixture
def owned(world):
    owner = world.user()
    return world, owner, world.project(owner, 'Doc film', logline='A heist on the moon')


def _make(world, user_id, project_id, **body):
    response = world.client(user_id).post(f'/api/studio/projects/{project_id}/documents',
                                          json=body or {'title': 'Synopsis', 'body': 'One night.'})
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def _chat_with_answer(project_id, owner, content, kind='chat', title='Tagline ideas'):
    chat = StudioChat(project_id=project_id, created_by=owner, title=title, kind=kind, provider='claude')
    db.session.add(chat)
    db.session.flush()
    db.session.add(StudioMessage(chat_id=chat.id, role='user', content='Give me taglines', sender_id=owner))
    answer = StudioMessage(chat_id=chat.id, role='assistant', content=content, provider='claude')
    db.session.add(answer)
    db.session.commit()
    return chat.id, answer.id


# --- making, reading, changing, deleting --------------------------------------------

def test_make_list_read_change_and_delete(owned):
    world, owner, project_id = owned
    client = world.client(owner)
    made = _make(world, owner, project_id, title='  The   tagline ', body='# One night\nNo way out.')
    assert made['title'] == 'The tagline' and made['kind'] == 'other' and made['body'] == '# One night\nNo way out.'
    assert made['created_by'] == owner and made['can_delete'] is True and made['version']
    listed = client.get(f'/api/studio/projects/{project_id}/documents').get_json()['items']
    assert [d['id'] for d in listed] == [made['id']]
    assert 'body' not in listed[0] and listed[0]['length'] == len('# One night\nNo way out.')
    changed = client.put(f"/api/studio/documents/{made['id']}",
                         json={'title': 'Tagline', 'body': 'New text', 'version': made['version']})
    assert changed.status_code == 200, changed.get_data(as_text=True)
    assert changed.get_json()['body'] == 'New text' and changed.get_json()['version'] != made['version']
    assert client.get(f"/api/studio/documents/{made['id']}").get_json()['title'] == 'Tagline'
    assert client.delete(f"/api/studio/documents/{made['id']}").status_code == 200
    assert db.session.get(StudioDocument, made['id']) is None


def test_the_list_is_the_most_recently_changed_first(owned):
    world, owner, project_id = owned
    first = _make(world, owner, project_id, title='First', body='a')
    second = _make(world, owner, project_id, title='Second', body='b')
    client = world.client(owner)
    assert [d['title'] for d in client.get(f'/api/studio/projects/{project_id}/documents').get_json()['items']] == [
        'Second', 'First']
    client.put(f"/api/studio/documents/{first['id']}", json={'body': 'a2', 'version': first['version']})
    assert [d['title'] for d in client.get(f'/api/studio/projects/{project_id}/documents').get_json()['items']] == [
        'First', 'Second']
    assert second['id']


def test_what_a_document_may_hold(owned):
    world, owner, project_id = owned
    client = world.client(owner)
    url = f'/api/studio/projects/{project_id}/documents'
    assert client.post(url, json={'title': '   ', 'body': 'x'}).status_code == 400
    assert client.post(url, json={'title': 'x' * 201, 'body': 'x'}).status_code == 400
    assert client.post(url, json={'title': 'Long', 'body': 'x' * (documents.BODY_MAX + 1)}).status_code == 400
    assert client.post(url, json={'title': 'Odd', 'body': 'x', 'kind': 'poem'}).status_code == 400
    assert client.post(url, json={'title': 'Odd', 'body': 12}).status_code == 400
    assert client.post(url, json={'title': 'Pitch', 'body': 'x', 'kind': 'pitchdeck'}).status_code == 201
    assert client.post(url, json={'title': 'Full', 'body': 'x' * documents.BODY_MAX}).status_code == 201
    made = _make(world, owner, project_id)
    put = f"/api/studio/documents/{made['id']}"
    assert client.put(put, json={'body': 'no version'}).status_code == 400
    assert client.put(put, json={'version': made['version']}).status_code == 400
    assert StudioDocument.query.filter_by(project_id=project_id).count() == 3


def test_people_on_the_list_and_admins_write_and_change_documents_and_delete_only_their_own(
        owned, sharing_on, folders_on):
    world, owner, project_id = owned
    member, admin = world.user(), world.user(admin=True)
    world.share(owner, project_id, member)
    owners = _make(world, owner, project_id, title='Owner notes', body='o')
    members = _make(world, member, project_id, title='Member notes', body='m')
    admins = _make(world, admin, project_id, title='Admin notes', body='a')
    m = world.client(member)
    assert m.put(f"/api/studio/documents/{owners['id']}",
                 json={'body': 'changed by the member', 'version': owners['version']}).status_code == 200
    got = world.client(owner).get(f"/api/studio/documents/{owners['id']}").get_json()
    assert got['updated_by'] == member and got['body'] == 'changed by the member'
    assert got['can_delete'] is True
    assert m.get(f"/api/studio/documents/{owners['id']}").get_json()['can_delete'] is False
    assert m.delete(f"/api/studio/documents/{owners['id']}").status_code == 403
    assert world.client(admin).delete(f"/api/studio/documents/{members['id']}").status_code == 403
    assert m.delete(f"/api/studio/documents/{members['id']}").status_code == 200
    assert world.client(owner).delete(f"/api/studio/documents/{admins['id']}").status_code == 200
    assert db.session.get(StudioDocument, owners['id']) is not None


def test_a_stranger_gets_404_and_nothing_changes(owned):
    world, owner, project_id = owned
    made = _make(world, owner, project_id)
    stranger = world.client(world.user())
    assert stranger.get(f'/api/studio/projects/{project_id}/documents').status_code == 404
    assert stranger.get(f"/api/studio/documents/{made['id']}").status_code == 404
    assert stranger.get(f"/api/studio/documents/{made['id']}/download?format=docx").status_code == 404
    assert stranger.put(f"/api/studio/documents/{made['id']}",
                        json={'body': 'x', 'version': made['version']}).status_code == 404
    assert stranger.delete(f"/api/studio/documents/{made['id']}").status_code == 404
    assert stranger.get('/api/studio/documents/987654321').get_json() == stranger.get(
        f"/api/studio/documents/{made['id']}").get_json()
    assert db.session.get(StudioDocument, made['id']).body == 'One night.'


# --- two people at once --------------------------------------------------------------

def test_a_save_over_a_newer_version_is_refused_and_names_who_changed_it(owned, sharing_on, folders_on):
    world, owner, project_id = owned
    member = world.user()
    world.share(owner, project_id, member)
    made = _make(world, owner, project_id, title='Budget', body='£100k')
    opened_by_owner = world.client(owner).get(f"/api/studio/documents/{made['id']}").get_json()
    opened_by_member = world.client(member).get(f"/api/studio/documents/{made['id']}").get_json()
    assert world.client(member).put(f"/api/studio/documents/{made['id']}", json={
        'body': '£120k', 'version': opened_by_member['version']}).status_code == 200
    late = world.client(owner).put(f"/api/studio/documents/{made['id']}", json={
        'body': '£90k', 'version': opened_by_owner['version']})
    assert late.status_code == 409
    member_name = db.session.get(User, member).username
    assert member_name in late.get_json()['error'] and late.get_json()['code'] == 'changedBy'
    db.session.expire_all()
    assert db.session.get(StudioDocument, made['id']).body == '£120k'
    newer = world.client(owner).get(f"/api/studio/documents/{made['id']}").get_json()
    assert world.client(owner).put(f"/api/studio/documents/{made['id']}", json={
        'body': '£90k', 'version': newer['version']}).status_code == 200


def test_two_saves_of_the_same_version_cannot_both_win(owned):
    world, owner, project_id = owned
    made = _make(world, owner, project_id)
    document = db.session.get(StudioDocument, made['id'])
    user = db.session.get(User, owner)
    documents.update(document, user, {'body': 'first', 'version': made['version']})
    with pytest.raises(documents.DocumentError) as refused:
        documents.update(document, user, {'body': 'second', 'version': made['version']})
    assert refused.value.status == 409
    db.session.expire_all()
    assert db.session.get(StudioDocument, made['id']).body == 'first'


# --- saving a chat answer ------------------------------------------------------------

def test_an_answer_is_saved_without_its_stopped_mark_and_with_the_chats_kind(owned):
    world, owner, project_id = owned
    _chat_id, answer = _chat_with_answer(project_id, owner, '## Slide one\n- Hook' + chats.STOPPED_SUFFIX,
                                         kind='pitchdeck', title='Pitch deck')
    made = _make(world, owner, project_id, message_id=answer)
    assert made['body'] == '## Slide one\n- Hook'
    assert made['kind'] == 'pitchdeck' and made['kind_title'] == workflows.WORKFLOWS['pitchdeck']['title']
    assert made['title'] == 'Pitch deck'
    named = _make(world, owner, project_id, message_id=answer, title='Deck v1')
    assert named['title'] == 'Deck v1'


def test_only_an_answer_of_this_projects_chats_can_be_saved(owned):
    world, owner, project_id = owned
    other_project = world.project(owner, 'Other film')
    _chat, foreign_answer = _chat_with_answer(other_project, owner, 'Elsewhere')
    chat_id, _answer = _chat_with_answer(project_id, owner, 'Here')
    question = StudioMessage.query.filter_by(chat_id=chat_id, role='user').one().id
    client = world.client(owner)
    url = f'/api/studio/projects/{project_id}/documents'
    assert client.post(url, json={'message_id': foreign_answer}).status_code == 404
    assert client.post(url, json={'message_id': question}).status_code == 400
    assert client.post(url, json={'message_id': 987654321}).status_code == 404
    assert client.post(url, json={'message_id': 'x'}).status_code == 404
    assert StudioDocument.query.filter_by(project_id=project_id).count() == 0


# --- project knowledge ---------------------------------------------------------------

def test_documents_go_to_the_model_as_project_knowledge_and_can_be_switched_off(owned, fake):
    world, owner, project_id = owned
    old = _make(world, owner, project_id, title='Old tagline', body='Darkness falls.')
    new = _make(world, owner, project_id, title='Fresh synopsis', body='One night. No way out.')
    client = world.client(owner)
    chat = client.post(f'/api/studio/projects/{project_id}/chats', json={'kind': 'chat'}).get_json()
    client.post(f"/api/studio/chats/{chat['id']}/stream", json={'message': 'Use our synopsis'}).get_data()
    system = fake.calls[-1]['system']
    assert 'PROJECT KNOWLEDGE' in system and 'One night. No way out.' in system and 'Darkness falls.' in system
    assert system.index('Fresh synopsis') < system.index('Old tagline')
    client.post(f"/api/studio/chats/{chat['id']}/stream",
                json={'message': 'Without the old one', 'excluded_doc_ids': [old['id']]}).get_data()
    assert 'Darkness falls.' not in fake.calls[-1]['system'] and 'One night.' in fake.calls[-1]['system']
    client.post(f"/api/studio/chats/{chat['id']}/regenerate",
                json={'excluded_doc_ids': [old['id'], new['id']]}).get_data()
    assert 'PROJECT KNOWLEDGE' not in fake.calls[-1]['system']


def test_the_knowledge_budget_as_in_the_standalone_app(owned):
    """The standalone app's knowledge test, ported: newest first, the budget-overflowing one cut and
    marked, a document past the budget listed by title only."""
    world, owner, project_id = owned
    project = db.session.get(StudioProject, project_id)
    assert documents.knowledge(project) == []
    assert workflows.knowledge_section([]) == ""
    for title, body, when in (('Old tagline', 'x' * workflows.MAX_KNOWLEDGE_PROMPT_CHARS, datetime(2026, 1, 1)),
                              ('Fresh synopsis', 'One night. No way out.', datetime(2026, 7, 7)),
                              ('Ancient notes', 'secret-body', datetime(2025, 1, 1))):
        db.session.add(StudioDocument(project_id=project_id, created_by=owner, title=title, kind='other',
                                      body=body, created_at=when, updated_at=when))
    db.session.commit()
    section = workflows.knowledge_section(documents.knowledge(project))
    assert "PROJECT KNOWLEDGE" in section
    assert "One night. No way out." in section, "newest doc content included in full"
    assert "…[truncated]" in section, "budget-overflowing doc must be marked truncated"
    assert section.index("Fresh synopsis") < section.index("Old tagline"), "newest first"
    assert "Ancient notes" in section and "secret-body" not in section, "past-budget docs are title-only"


# --- downloads -------------------------------------------------------------------------

BODY = """Intro line before any heading.

## The Hook
- One night. One town. **No way out.**
- Elevated horror, contained budget

## The Ask
Some plain paragraph line.
- £250k production budget
  - £50k already soft-committed
"""


def test_the_markdown_download_is_the_text_as_saved(owned):
    world, owner, project_id = owned
    made = _make(world, owner, project_id, title='Pitch Deck', body=BODY)
    response = world.client(owner).get(f"/api/studio/documents/{made['id']}/download?format=md")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == BODY
    assert response.headers['Content-Type'].startswith('text/markdown')
    assert response.headers['Content-Disposition'] == 'attachment; filename="pitch-deck.md"'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert world.client(owner).get(f"/api/studio/documents/{made['id']}/download").get_data(as_text=True) == BODY


def test_the_word_download_opens_and_holds_the_title_and_the_text(owned):
    from docx import Document
    world, owner, project_id = owned
    made = _make(world, owner, project_id, title='Pitch Deck', body=BODY + '\n![still](http://192.0.2.1/a.png)\n')
    response = world.client(owner).get(f"/api/studio/documents/{made['id']}/download?format=docx")
    assert response.status_code == 200
    assert response.headers['Content-Disposition'] == 'attachment; filename="pitch-deck.docx"'
    doc = Document(io.BytesIO(response.get_data()))
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert doc.paragraphs[0].text == 'Pitch Deck'
    assert 'The Hook' in text and '£250k production budget' in text and 'still' in text
    assert '192.0.2.1' not in text


def test_the_slides_download_has_one_slide_per_heading(owned):
    pptx = pytest.importorskip('pptx')
    world, owner, project_id = owned
    made = _make(world, owner, project_id, title='My Film', body=BODY)
    response = world.client(owner).get(f"/api/studio/documents/{made['id']}/download?format=pptx")
    assert response.status_code == 200
    assert response.headers['Content-Type'] == (
        'application/vnd.openxmlformats-officedocument.presentationml.presentation')
    prs = pptx.Presentation(io.BytesIO(response.get_data()))
    assert [s.shapes.title.text for s in prs.slides] == ['My Film', 'The Hook', 'The Ask']


def test_the_pdf_download(owned):
    pytest.importorskip('weasyprint')
    world, owner, project_id = owned
    made = _make(world, owner, project_id, title='Pitch Deck', body=BODY)
    response = world.client(owner).get(f"/api/studio/documents/{made['id']}/download?format=pdf")
    assert response.status_code == 200
    assert response.get_data()[:5] == b'%PDF-'
    assert response.headers['Content-Type'] == 'application/pdf'
    assert response.headers['Content-Disposition'] == 'attachment; filename="pitch-deck.pdf"'


def test_an_unknown_format_is_refused_and_a_title_in_another_script_keeps_its_letters(owned):
    world, owner, project_id = owned
    made = _make(world, owner, project_id, title='Плакат «Ночь»', body='x')
    client = world.client(owner)
    assert client.get(f"/api/studio/documents/{made['id']}/download?format=exe").status_code == 400
    response = client.get(f"/api/studio/documents/{made['id']}/download?format=md")
    disposition = response.headers['Content-Disposition']
    assert 'filename="document.md"' in disposition
    assert "filename*=UTF-8''%D0%BF%D0%BB%D0%B0%D0%BA%D0%B0%D1%82-%D0%BD%D0%BE%D1%87%D1%8C.md" in disposition


# --- what goes with a project or an account -------------------------------------------

def test_documents_go_with_their_project(owned):
    world, owner, project_id = owned
    made = _make(world, owner, project_id)
    assert world.client(owner).delete(f'/api/studio/projects/{project_id}').status_code == 200
    assert db.session.get(StudioDocument, made['id']) is None


def test_documents_stay_when_their_maker_account_is_deleted_and_go_with_the_owners(owned, sharing_on, folders_on):
    world, owner, project_id = owned
    member = world.user()
    world.share(owner, project_id, member)
    members = _make(world, member, project_id, title='Member notes', body='m')
    db.session.delete(db.session.get(User, member))
    db.session.commit()
    kept = db.session.get(StudioDocument, members['id'])
    assert kept is not None and kept.created_by is None and kept.updated_by is None
    db.session.delete(db.session.get(User, owner))
    db.session.commit()
    assert db.session.get(StudioDocument, members['id']) is None
