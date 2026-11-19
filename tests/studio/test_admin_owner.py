"""An administrator acts as the owner (src/studio/admin_owner.py, STUDIO_ADMINS_AS_OWNERS):
every capability per role (owner, on the People list, administrator, no tie), the setting
on and off, a recording in a project and in none; what stays the owner's; the audit rows.
Hermetic and offline."""

import json

import pytest

from src.app import app, db, has_recording_access
from src.models import AuditEvent, InternalShare, Recording, SharedRecordingState
from src.studio import access, admin_owner
from src.studio.models import StudioProject
from tests.studio.support import World, sharing_on  # noqa: F401

SEGMENTS = [{'speaker': 'SPEAKER_00', 'sentence': 'A made-up line.', 'start_time': 0.0, 'end_time': 1.0},
            {'speaker': 'SPEAKER_01', 'sentence': 'Another made-up line.', 'start_time': 1.0, 'end_time': 2.0}]


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.delenv(admin_owner.SETTING, raising=False)


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setenv(admin_owner.SETTING, 'false')


@pytest.fixture
def world():
    with app.app_context():
        made = World()
        try:
            yield made
        finally:
            db.session.rollback()
            ids = made.users or [-1]
            AuditEvent.query.filter(AuditEvent.actor_user_id.in_(ids)).delete(synchronize_session=False)
            SharedRecordingState.query.filter(SharedRecordingState.user_id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
            made.cleanup()


@pytest.fixture
def people(world, sharing_on):  # noqa: F811
    owner = world.user(prefix='own')
    admin = world.user(admin=True, prefix='adm')
    recipient = world.user(prefix='rcp')
    stranger = world.user(prefix='str')
    loose = world.recording(owner, title='In no project')
    rec = db.session.get(Recording, loose)
    rec.transcription = json.dumps(SEGMENTS)
    db.session.add(InternalShare(recording_id=loose, owner_id=owner, shared_with_user_id=recipient, can_edit=False,
                                 can_reshare=False))
    db.session.commit()
    return {'owner': owner, 'admin': admin, 'recipient': recipient, 'stranger': stranger, 'loose': loose}


def _user(uid):
    from src.models import User
    return db.session.get(User, uid)


def _get(world, who, path):
    return world.client(who).get(path)


# --- the rule ------------------------------------------------------------------

def test_the_rule(world, off):
    admin = _user(world.user(admin=True))
    plain = _user(world.user())
    assert not admin_owner.acts_as_owner(admin, 12345)  # off
    import os
    os.environ.pop(admin_owner.SETTING)
    assert admin_owner.acts_as_owner(admin, 12345)
    assert not admin_owner.acts_as_owner(admin, admin.id)  # their own
    assert not admin_owner.acts_as_owner(admin, None)  # nobody's (#581)
    assert not admin_owner.acts_as_owner(plain, 12345)


# --- recordings ----------------------------------------------------------------

def test_who_opens_a_recording_in_no_project(world, people):
    path = f"/api/recordings/{people['loose']}"
    assert _get(world, people['owner'], path).status_code == 200
    assert _get(world, people['admin'], path).status_code == 200
    assert _get(world, people['recipient'], path).status_code == 200
    assert _get(world, people['stranger'], path).status_code == 404


def test_off_gives_back_todays_behaviour(world, people, off):
    assert _get(world, people['admin'], f"/api/recordings/{people['loose']}").status_code == 404
    rec = db.session.get(Recording, people['loose'])
    assert not has_recording_access(rec, _user(people['admin']))


def test_the_admin_may_edit_as_the_owner_and_the_recipient_may_not(world, people):
    rec = db.session.get(Recording, people['loose'])
    assert has_recording_access(rec, _user(people['admin']), require_edit=True, require_reshare=True)
    assert not has_recording_access(rec, _user(people['recipient']), require_edit=True)
    assert not has_recording_access(rec, _user(people['stranger']))
    response = world.client(people['admin']).post('/save', json={'id': people['loose'], 'title': 'Retitled by the admin'})
    assert response.status_code == 200, response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Recording, people['loose']).title == 'Retitled by the admin'


def test_the_admin_writes_the_recordings_own_notes_and_keeps_their_own_marks(world, people):
    client = world.client(people['admin'])
    assert client.post('/save', json={'id': people['loose'], 'notes': 'Notes by the admin'}).status_code == 200
    db.session.expire_all()
    rec = db.session.get(Recording, people['loose'])
    assert rec.notes == 'Notes by the admin'
    assert rec.get_user_notes(_user(people['owner'])) == 'Notes by the admin'
    before = (rec.is_inbox, rec.is_highlighted)
    assert client.post(f"/recording/{people['loose']}/toggle_highlight").status_code == 200
    db.session.expire_all()
    rec = db.session.get(Recording, people['loose'])
    assert (rec.is_inbox, rec.is_highlighted) == before  # the owner's marks
    state = SharedRecordingState.query.filter_by(recording_id=people['loose'], user_id=people['admin']).one()
    assert state.is_highlighted is True  # the admin's own


def test_the_page_flags_show_what_the_server_allows(world, people):
    detail = _get(world, people['admin'], f"/api/recordings/{people['loose']}").get_json()
    detail = detail.get('recording', detail)
    assert detail['can_edit'] and detail['can_delete'] and detail['studio_admin_as_owner']
    assert detail['share_info']['can_reshare'] and detail['owner_username']
    recipient = _get(world, people['recipient'], f"/api/recordings/{people['loose']}").get_json()
    recipient = recipient.get('recording', recipient)
    assert not recipient.get('studio_admin_as_owner') and recipient['can_delete'] is False


def test_from_others_lists_every_recording_not_ones_own_for_the_admin_only(world, people):
    def ids(who, query):
        body = _get(world, who, f'/api/recordings?per_page=100{query}').get_json()
        return {r['id'] for r in body['recordings']}, {r['id']: r for r in body['recordings']}
    others, rows = ids(people['admin'], '&shared=true')
    assert people['loose'] in others and rows[people['loose']]['studio_admin_as_owner']
    assert people['loose'] not in ids(people['admin'], '')[0]  # the default list is unchanged
    assert people['loose'] not in ids(people['stranger'], '&shared=true')[0]
    assert people['loose'] in ids(people['recipient'], '&shared=true')[0]


def test_delete_by_the_admin_is_the_owners_delete_with_the_admin_as_actor(world, people):
    response = world.client(people['admin']).delete(f"/recording/{people['loose']}")
    assert response.status_code == 200, response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Recording, people['loose']) is None
    from src.services import audit
    assert audit.flush(timeout=15)
    db.session.rollback()
    row = AuditEvent.query.filter_by(event='recording.deleted', recording_id=people['loose']).first()
    assert row is not None and row.actor_user_id == people['admin'] and row.recording_owner_id == people['owner']


def test_speakers_and_public_links_stay_the_owners(world, people):
    client = world.client(people['admin'])
    response = client.post(f"/recording/{people['loose']}/update_speakers", json={'speaker_map': {'SPEAKER_00': 'X'}})
    assert response.status_code == 403
    assert client.post(f"/recording/{people['loose']}/auto_identify_speakers", json={}).status_code == 403
    https = {'X-Forwarded-Proto': 'https'}
    response = client.post(f"/api/recording/{people['loose']}/share", json={'share_summary': True}, headers=https)
    assert response.status_code in (403, 404)
    from src.models import Share
    assert Share.query.filter_by(recording_id=people['loose']).count() == 0


def test_the_admin_shares_inside_the_site_as_the_owner(world, people):
    response = world.client(people['admin']).post(f"/api/recordings/{people['loose']}/share-internal",
                                                  json={'user_ids': [people['stranger']], 'can_edit': True})
    assert response.status_code in (200, 201), response.get_data(as_text=True)
    assert InternalShare.query.filter_by(recording_id=people['loose'], shared_with_user_id=people['stranger']).count() == 1


def test_tags_and_folders_stay_the_owners(world, people):
    from src.api.folders import folder_assign_error
    from src.models import Folder
    admin_folder = Folder(user_id=people['admin'], name='Admin folder')
    db.session.add(admin_folder)
    db.session.commit()
    rec = db.session.get(Recording, people['loose'])
    assert folder_assign_error(rec, admin_folder, _user(people['admin']), 'folder_not_found') is not None


# --- projects ------------------------------------------------------------------

@pytest.fixture
def project(world, people):
    pid = world.project(people['owner'], name='A made-up film')
    return db.session.get(StudioProject, pid)


def test_the_admin_has_every_capability_of_the_owner_and_keeps_the_role(world, people, project):
    admin, stranger = _user(people['admin']), _user(people['stranger'])
    role = access.role_of(project, admin)
    assert role == access.ADMIN
    caps = access.capabilities(project, role, admin)
    assert caps == access.capabilities(project, access.OWNER, _user(people['owner']))
    assert access.may_delete_item(project, role, admin, created_by=people['owner'])
    assert access.role_of(project, stranger) is None


def test_off_the_admin_has_todays_capabilities(world, people, project, off):
    caps = access.capabilities(project, access.ADMIN, _user(people['admin']))
    assert not caps['rename'] and not caps['share'] and not caps['edit_hints'] and not caps['delete']


def test_a_member_gains_nothing(world, people, project):
    member = world.user(prefix='mem')
    world.share(people['owner'], project.id, member)
    role = access.role_of(project, _user(member))
    assert role == access.MEMBER
    caps = access.capabilities(project, role, _user(member))
    assert not caps['rename'] and not caps['share'] and not caps['delete']


def test_a_rename_by_the_admin_leaves_an_audit_row_naming_the_owner(world, people, project):
    response = world.client(people['admin']).put(f'/api/studio/projects/{project.id}', json={'name': 'Renamed by the admin'})
    assert response.status_code == 200, response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(StudioProject, project.id).name == 'Renamed by the admin'
    assert db.session.get(StudioProject, project.id).owner_id == people['owner']
    from src.services import audit
    assert audit.flush(timeout=15)
    db.session.rollback()
    rows = AuditEvent.query.filter_by(event='studio.project.changed', actor_user_id=people['admin']).all()
    assert rows and rows[-1].target_user_id == people['owner']
    assert rows[-1].details['project_id'] == project.id and rows[-1].details['fields'] == ['name']


def test_the_owner_writes_no_studio_rows(world, people, project):
    from src.services import audit
    world.client(people['owner']).put(f'/api/studio/projects/{project.id}', json={'name': 'Renamed by the owner'})
    assert audit.flush(timeout=15)
    db.session.rollback()
    assert AuditEvent.query.filter(AuditEvent.event.like('studio.%'), AuditEvent.actor_user_id == people['owner']).count() == 0


def test_the_project_page_and_about_say_it(world, people, project):
    detail = world.client(people['admin']).get(f'/api/studio/projects/{project.id}').get_json()
    assert detail['admins_as_owners'] is True and detail['role'] == 'admin'
    from flask import render_template
    with app.test_request_context('/'):
        about = render_template('studio/about_licence.html')
    assert 'The administrators of this site can open and change every project and recording.' in about
