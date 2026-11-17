"""STUDIO_ADMINS_SEE_RECORDINGS: site admins open every project's recordings, read-only.

Off by default (Speakr's own rule: a recording opens to its owner and the
people it is shared with). On, every project keeps a Speakr group and group
folder and every site admin is a member, so Speakr shares the folder's
recordings with them; the memberships the rule made are taken back exactly
when someone stops being an admin and when the setting goes off.
"""

import json
import os

import pytest

from src.app import app, db, has_recording_access
from src.models import Folder, Group, GroupMembership, InternalShare, Recording, SharedRecordingState, User
from src.studio import access, admin_access, projects
from src.studio.models import StudioAdminGrant, StudioAdminProject, StudioProject, StudioProjectMember
from tests.studio.support import admins_see, folders_on, sharing_off, sharing_on, world  # noqa: F401


def _project(project_id):
    db.session.expire_all()
    return db.session.get(StudioProject, project_id)


def _shares(recording_id):
    db.session.expire_all()
    return {s.shared_with_user_id: s for s in InternalShare.query.filter_by(recording_id=recording_id)}


def _members(group_id):
    db.session.expire_all()
    return {m.user_id: m.role for m in GroupMembership.query.filter_by(group_id=group_id)}


def _grants(project_id):
    db.session.expire_all()
    return {g.user_id for g in StudioAdminGrant.query.filter_by(project_id=project_id)}


def _opens(user_id, recording_id):
    db.session.expire_all()
    return has_recording_access(db.session.get(Recording, recording_id), db.session.get(User, user_id))


def _startup():
    admin_access.startup()
    db.session.expire_all()


@pytest.fixture
def private(world, sharing_on, folders_on):
    """A private project of a person, with two recordings, and a site admin. The setting is still off."""
    owner, admin = world.user(), world.user(admin=True)
    project_id = world.project(owner, 'Private film')
    folder_id = _project(project_id).folder_id
    first = world.recording(owner, folder_id=folder_id, title='First')
    second = world.recording(owner, folder_id=folder_id, title='Second')
    outside = world.recording(owner, title='Not in any project')
    return dict(world=world, owner=owner, admin=admin, project=project_id, folder=folder_id,
                recordings=[first, second], outside=outside)


# --- off --------------------------------------------------------------------

def test_off_by_default_an_admin_sees_the_project_but_not_its_recordings(private):
    world = private['world']
    assert not admin_access.setting_on()
    _startup()
    project = _project(private['project'])
    assert project.group_id is None
    assert StudioAdminGrant.query.count() == 0 and StudioAdminProject.query.count() == 0
    assert not any(_opens(private['admin'], rid) for rid in private['recordings'])
    body = world.client(private['admin']).get(f"/api/studio/projects/{private['project']}/recordings").get_json()
    assert body['items'] == []
    owner_name = db.session.get(User, private['owner']).username
    assert body['hidden'] == {'count': 2, 'owners': [owner_name]}


def test_the_card_counts_what_the_viewer_cannot_open(private):
    world = private['world']
    cards = world.client(private['admin']).get('/api/studio/projects').get_json()['projects']
    card = next(c for c in cards if c['id'] == private['project'])
    assert card['recording_count'] == 0
    assert card['hidden_recordings']['count'] == 2
    own = world.client(private['owner']).get('/api/studio/projects').get_json()['projects']
    mine = next(c for c in own if c['id'] == private['project'])
    assert mine['recording_count'] == 2 and mine['hidden_recordings'] == {'count': 0, 'owners': []}


def test_off_the_project_page_says_admins_do_not_open_recordings(private):
    detail = private['world'].client(private['admin']).get(f"/api/studio/projects/{private['project']}").get_json()
    assert detail['admins_see_recordings'] is False
    assert detail['can']['add_recording'] is False


def test_off_a_start_writes_nothing_when_the_rule_never_ran(private):
    before = (Group.query.count(), GroupMembership.query.count(), InternalShare.query.count())
    assert admin_access.undo_all() == (0, 0)
    _startup()
    assert (Group.query.count(), GroupMembership.query.count(), InternalShare.query.count()) == before


# --- on: the start-up pass ----------------------------------------------------

def test_on_the_start_up_pass_gives_admins_read_only_shares(private, admins_see):
    _startup()
    project = _project(private['project'])
    group = db.session.get(Group, project.group_id)
    folder = db.session.get(Folder, private['folder'])
    assert group is not None and folder.group_id == group.id and folder.auto_share_on_apply
    assert _members(group.id)[private['owner']] == 'admin'
    assert _members(group.id)[private['admin']] == 'member'
    assert _grants(project.id) >= {private['admin']}
    assert StudioAdminProject.query.filter_by(project_id=project.id).count() == 1
    for rid in private['recordings']:
        share = _shares(rid)[private['admin']]
        assert share.source_type == 'group_folder' and share.can_edit is False and share.can_reshare is False
        assert _opens(private['admin'], rid)
        assert not has_recording_access(db.session.get(Recording, rid), db.session.get(User, private['admin']),
                                        require_edit=True)
        # Not a burst of "new" recordings in the admin's inbox.
        state = SharedRecordingState.query.filter_by(recording_id=rid, user_id=private['admin']).one()
        assert state.is_inbox is False
    # A recording of the owner's that is in no project stays private.
    assert private['admin'] not in _shares(private['outside'])
    assert not _opens(private['admin'], private['outside'])


def test_on_the_role_and_the_people_list_do_not_change(private, admins_see):
    _startup()
    world = private['world']
    project = _project(private['project'])
    assert access.role_of(project, db.session.get(User, private['admin'])) == access.ADMIN
    assert access.member_ids(project) == set()
    detail = world.client(private['owner']).get(f"/api/studio/projects/{project.id}").get_json()
    assert [p['role'] for p in detail['people']] == ['owner']
    assert detail['admins_see_recordings'] is True
    card = next(c for c in world.client(private['owner']).get('/api/studio/projects').get_json()['projects']
                if c['id'] == project.id)
    assert card['shared'] is False
    admin_view = world.client(private['admin']).get(f"/api/studio/projects/{project.id}").get_json()
    assert admin_view['role'] == 'admin' and admin_view['can']['add_recording'] is True
    assert admin_view['can']['share'] is False and admin_view['can']['rename'] is False
    body = world.client(private['admin']).get(f"/api/studio/projects/{project.id}/recordings").get_json()
    assert {r['id'] for r in body['items']} == set(private['recordings'])
    assert body['hidden'] == {'count': 0, 'owners': []}


def test_on_a_second_start_changes_nothing(private, admins_see):
    _startup()
    counts = (Group.query.count(), GroupMembership.query.count(), InternalShare.query.count(),
              StudioAdminGrant.query.count(), StudioAdminProject.query.count())
    assert admin_access.sync_all()[1] == 0
    db.session.commit()
    _startup()
    assert (Group.query.count(), GroupMembership.query.count(), InternalShare.query.count(),
            StudioAdminGrant.query.count(), StudioAdminProject.query.count()) == counts


def test_on_but_with_sharing_off_the_start_changes_nothing(private, admins_see, sharing_off):
    _startup()
    assert _project(private['project']).group_id is None
    assert _grants(private['project']) == set()


# --- on: as things change -----------------------------------------------------

def test_on_a_new_project_has_its_group_and_the_admins_at_once(world, sharing_on, folders_on, admins_see):
    owner, admin = world.user(), world.user(admin=True)
    project_id = world.project(owner, 'New film')
    project = _project(project_id)
    assert project.group_id is not None
    assert _members(project.group_id)[admin] == 'member'
    assert db.session.get(Folder, project.folder_id).group_id == project.group_id


def test_on_a_recording_added_later_is_shared_with_the_admins_by_speakr(private, admins_see):
    from src.api.folders import apply_group_folder_shares
    _startup()
    later = private['world'].recording(private['owner'], folder_id=private['folder'], title='Later')
    apply_group_folder_shares(db.session.get(Recording, later), db.session.get(Folder, private['folder']))
    db.session.commit()
    assert _shares(later)[private['admin']].can_edit is False
    assert _opens(private['admin'], later)


def test_on_a_recording_moved_out_of_the_folder_is_no_longer_shared(private, admins_see):
    from src.services.auto_shares import drop_stale_auto_shares
    _startup()
    rid = private['recordings'][0]
    db.session.get(Recording, rid).folder_id = None
    drop_stale_auto_shares(recording_ids=[rid])
    db.session.commit()
    assert private['admin'] not in _shares(rid)


def test_on_making_a_shared_project_private_keeps_the_group_for_the_admins(private, admins_see):
    world = private['world']
    _startup()
    person = world.user()
    world.share(private['owner'], private['project'], person)
    group_id = _project(private['project']).group_id
    assert _members(group_id)[person] == 'member'
    world.share(private['owner'], private['project'])  # the list emptied
    project = _project(private['project'])
    assert project.group_id == group_id
    assert person not in _members(group_id) and _members(group_id)[private['admin']] == 'member'
    assert StudioProjectMember.query.filter_by(project_id=project.id).count() == 0
    assert all(_opens(private['admin'], rid) for rid in private['recordings'])
    assert not any(_opens(person, rid) for rid in private['recordings'])


def test_on_the_owner_removing_an_admin_from_the_list_leaves_them_reading(private, admins_see):
    world = private['world']
    _startup()
    world.share(private['owner'], private['project'], private['admin'])
    project = _project(private['project'])
    assert private['admin'] in access.member_ids(project)
    world.share(private['owner'], private['project'])
    project = _project(private['project'])
    assert private['admin'] not in access.member_ids(project)
    assert _members(project.group_id)[private['admin']] == 'member'
    assert all(_opens(private['admin'], rid) for rid in private['recordings'])


def test_deleting_a_project_removes_its_group_and_the_admins_shares(private, admins_see):
    world = private['world']
    _startup()
    group_id = _project(private['project']).group_id
    assert world.client(private['owner']).delete(f"/api/studio/projects/{private['project']}").status_code == 200
    assert db.session.get(Group, group_id) is None
    assert StudioAdminGrant.query.filter_by(project_id=private['project']).count() == 0
    assert StudioAdminProject.query.filter_by(project_id=private['project']).count() == 0
    assert not any(_opens(private['admin'], rid) for rid in private['recordings'])


# --- on: admin status ---------------------------------------------------------

def test_a_new_admin_made_in_the_admin_screen_is_added_after_that_request(private, admins_see):
    world = private['world']
    _startup()
    newcomer = world.user()
    response = world.client(private['admin']).post(f'/admin/users/{newcomer}/toggle-admin')
    assert response.status_code == 200, response.get_data(as_text=True)
    group_id = _project(private['project']).group_id
    assert _members(group_id)[newcomer] == 'member'
    assert all(_opens(newcomer, rid) for rid in private['recordings'])


def test_an_admin_demoted_in_the_admin_screen_loses_the_shares_after_that_request(private, admins_see):
    world = private['world']
    _startup()
    other_admin = world.user(admin=True)
    response = world.client(other_admin).put(f"/admin/users/{private['admin']}", json={'is_admin': False})
    assert response.status_code == 200, response.get_data(as_text=True)
    group_id = _project(private['project']).group_id
    assert private['admin'] not in _members(group_id)
    assert not any(_opens(private['admin'], rid) for rid in private['recordings'])
    assert _grants(private['project']) & {private['admin']} == set()


def test_a_demotion_by_any_other_route_takes_effect_before_their_next_request(private, admins_see):
    world = private['world']
    _startup()
    # A bulk update fires no ORM events, as a script or a future route might.
    User.query.filter_by(id=private['admin']).update({'is_admin': False}, synchronize_session=False)
    db.session.commit()
    rid = private['recordings'][0]
    response = world.client(private['admin']).get(f'/api/recordings/{rid}')
    assert response.status_code in (403, 404)
    assert not any(_opens(private['admin'], r) for r in private['recordings'])


def test_a_promotion_by_any_other_route_takes_effect_on_their_next_request(private, admins_see):
    world = private['world']
    _startup()
    newcomer = world.user()
    User.query.filter_by(id=newcomer).update({'is_admin': True}, synchronize_session=False)
    db.session.commit()
    world.client(newcomer).get('/api/studio/projects')
    assert all(_opens(newcomer, rid) for rid in private['recordings'])


def test_an_admin_on_the_people_list_who_is_demoted_stays_on_it(private, admins_see):
    world = private['world']
    _startup()
    world.share(private['owner'], private['project'], private['admin'])
    other_admin = world.user(admin=True)
    world.client(other_admin).put(f"/admin/users/{private['admin']}", json={'is_admin': False})
    project = _project(private['project'])
    assert private['admin'] in access.member_ids(project)
    assert all(_opens(private['admin'], rid) for rid in private['recordings'])


def test_a_demoted_admins_own_recordings_leave_the_project(private, admins_see):
    world = private['world']
    _startup()
    own = world.recording(private['admin'], folder_id=private['folder'], title="The admin's own")
    from src.api.folders import apply_group_folder_shares
    apply_group_folder_shares(db.session.get(Recording, own), db.session.get(Folder, private['folder']))
    db.session.commit()
    assert _opens(private['owner'], own)
    other_admin = world.user(admin=True)
    world.client(other_admin).put(f"/admin/users/{private['admin']}", json={'is_admin': False})
    db.session.expire_all()
    assert db.session.get(Recording, own).folder_id is None
    assert not _opens(private['owner'], own)


def test_a_membership_someone_made_a_group_admin_is_not_taken_back(private, admins_see):
    world = private['world']
    _startup()
    group_id = _project(private['project']).group_id
    membership = GroupMembership.query.filter_by(group_id=group_id, user_id=private['admin']).one()
    membership.role = 'admin'
    db.session.commit()
    other_admin = world.user(admin=True)
    world.client(other_admin).put(f"/admin/users/{private['admin']}", json={'is_admin': False})
    assert _members(group_id)[private['admin']] == 'admin'
    assert _grants(private['project']) & {private['admin']} == set()


def test_a_deleted_admin_account_leaves_no_grant(private, admins_see):
    world = private['world']
    _startup()
    assert private['admin'] in _grants(private['project'])
    other_admin = world.user(admin=True)
    response = world.client(other_admin).delete(f"/admin/users/{private['admin']}")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert _grants(private['project']) & {private['admin']} == set()
    assert private['admin'] not in _members(_project(private['project']).group_id)


# --- the setting goes off -----------------------------------------------------

def test_off_again_takes_back_exactly_what_the_rule_made(private, admins_see, monkeypatch):
    world = private['world']
    _startup()
    shared_id = world.project(private['owner'], 'Shared film')
    person = world.user()
    world.share(private['owner'], shared_id, person)
    shared_folder = _project(shared_id).folder_id
    shared_rec = world.recording(private['owner'], folder_id=shared_folder, title='Shared one')
    from src.api.folders import apply_group_folder_shares
    apply_group_folder_shares(db.session.get(Recording, shared_rec), db.session.get(Folder, shared_folder))
    db.session.commit()
    assert _opens(private['admin'], shared_rec) and _opens(person, shared_rec)

    monkeypatch.setenv('STUDIO_ADMINS_SEE_RECORDINGS', 'false')
    _startup()

    project = _project(private['project'])
    assert project.group_id is None
    assert db.session.get(Folder, private['folder']).group_id is None
    assert not any(_opens(private['admin'], rid) for rid in private['recordings'])
    shared = _project(shared_id)
    assert shared.group_id is not None and access.member_ids(shared) == {person}
    assert _opens(person, shared_rec) and not _opens(private['admin'], shared_rec)
    assert private['admin'] not in _members(shared.group_id)
    assert StudioAdminGrant.query.count() == 0 and StudioAdminProject.query.count() == 0
    # And a further start finds nothing to do.
    assert admin_access.undo_all() == (0, 0)


# --- the strings ----------------------------------------------------------------

def test_the_new_strings_exist():
    path = os.path.join(os.path.dirname(app.root_path), 'static', 'locales-studio', 'en.json')
    if not os.path.exists(path):
        path = os.path.join(app.root_path, 'static', 'locales-studio', 'en.json')
    strings = json.load(open(path, encoding='utf-8'))['studio']
    assert 'adminNoteRecordings' in strings['project']
    assert {'hiddenAll', 'hiddenSome'} <= set(strings['project']['recordings'])
    assert 'adminsSee' in strings['project']['people']
    assert {'countsHiddenAll', 'countsHiddenSome'} <= set(strings['projects'])
    assert 'yet' not in strings['project']['recordings']['hiddenAll']


# --- the request hooks never take a request down --------------------------------

def _boom(*args, **kwargs):
    raise RuntimeError('planted error')


def test_an_error_giving_an_admin_access_leaves_the_request_answered_and_nothing_half_written(
        private, admins_see, monkeypatch):
    world = private['world']
    _startup()
    newcomer = world.user()
    User.query.filter_by(id=newcomer).update({'is_admin': True}, synchronize_session=False)
    db.session.commit()
    group_id = _project(private['project']).group_id
    # The memberships are added and flushed, then sharing the folder fails.
    monkeypatch.setattr(admin_access, '_share_folder', _boom)
    response = world.client(newcomer).get('/api/studio/projects')
    assert response.status_code == 200
    assert newcomer not in _members(group_id)
    assert newcomer not in _grants(private['project'])
    assert not any(_opens(newcomer, rid) for rid in private['recordings'])
    # With the error gone, the next request gives the access.
    monkeypatch.undo()
    monkeypatch.setenv('STUDIO_ADMINS_SEE_RECORDINGS', 'true')
    assert world.client(newcomer).get('/api/studio/projects').status_code == 200
    assert _members(group_id)[newcomer] == 'member'
    assert all(_opens(newcomer, rid) for rid in private['recordings'])


def test_an_error_taking_access_back_refuses_the_request_until_it_works(private, admins_see, monkeypatch):
    world = private['world']
    _startup()
    User.query.filter_by(id=private['admin']).update({'is_admin': False}, synchronize_session=False)
    db.session.commit()
    monkeypatch.setattr(admin_access, '_take_back', _boom)
    response = world.client(private['admin']).get('/api/studio/projects')
    assert response.status_code == 503
    assert response.mimetype == 'text/plain'
    # Rolled back: the grant and the shares are still there, and nothing was served.
    assert private['admin'] in _grants(private['project'])
    # Every request of that person is refused while the take-back fails.
    assert world.client(private['admin']).get(f"/api/recordings/{private['recordings'][0]}").status_code == 503
    # Error removed: the next request takes the access back and is answered.
    monkeypatch.undo()
    monkeypatch.setenv('STUDIO_ADMINS_SEE_RECORDINGS', 'true')
    assert world.client(private['admin']).get('/api/studio/projects').status_code == 200
    assert private['admin'] not in _grants(private['project'])
    assert not any(_opens(private['admin'], rid) for rid in private['recordings'])


def test_a_request_that_needs_no_take_back_is_never_refused(private, admins_see, monkeypatch):
    world = private['world']
    _startup()
    monkeypatch.setattr(admin_access, '_take_back', _boom)
    monkeypatch.setattr(admin_access, 'sync_project', _boom)
    # The owner holds no grant; the admin is already in every group.
    assert world.client(private['owner']).get('/api/studio/projects').status_code == 200
    assert world.client(private['admin']).get('/api/studio/projects').status_code == 200


def test_an_error_in_the_check_itself_lets_the_request_go_on(private, admins_see, monkeypatch):
    world = private['world']
    _startup()
    monkeypatch.setattr(admin_access, '_needs_sync', _boom)
    assert world.client(private['owner']).get('/api/studio/projects').status_code == 200


def test_after_request_with_an_error_returns_the_original_response(private, admins_see, monkeypatch):
    world = private['world']
    _startup()
    newcomer = world.user()
    monkeypatch.setattr(admin_access, 'sync_user', _boom)
    response = world.client(private['admin']).post(f'/admin/users/{newcomer}/toggle-admin')
    assert response.status_code == 200
    assert response.get_json() == {'success': True, 'is_admin': True}
    db.session.expire_all()
    assert db.session.get(User, newcomer).is_admin is True
    assert newcomer not in _members(_project(private['project']).group_id)
    # Called directly, it hands back the very object it was given.
    from flask import g
    marker = app.response_class('kept', status=299)
    with app.test_request_context('/'):
        setattr(g, admin_access._CHANGED_KEY, {newcomer})
        assert admin_access.after_request(marker) is marker


def test_an_error_noting_a_status_change_does_not_break_speakrs_own_save(private, admins_see, monkeypatch):
    world = private['world']
    _startup()
    newcomer = world.user()
    monkeypatch.setattr(admin_access, 'inspect', _boom)
    response = world.client(private['admin']).post(f'/admin/users/{newcomer}/toggle-admin')
    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(User, newcomer).is_admin is True
    # The change is still caught, on that account's next request.
    monkeypatch.undo()
    monkeypatch.setenv('STUDIO_ADMINS_SEE_RECORDINGS', 'true')
    world.client(newcomer).get('/api/studio/projects')
    assert all(_opens(newcomer, rid) for rid in private['recordings'])
