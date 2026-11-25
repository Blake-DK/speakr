"""Sharing a project through a Speakr group and group folder, and what
happens when the folder or the group is changed in Speakr's own screens.

Speakr's own screens are driven through Speakr's own routes (folder update and
delete, the admin group routes), as an admin or the owner would use them.
"""

import pytest

from src.app import db
from src.models import Folder, Group, GroupMembership, InternalShare, Recording, User
from src.studio import access
from src.studio.models import StudioProject, StudioProjectMember
from tests.studio.support import folders_on, sharing_off, sharing_on, world  # noqa: F401


def _project(project_id):
    db.session.expire_all()
    return db.session.get(StudioProject, project_id)


def _shares(recording_id):
    return {s.shared_with_user_id: s for s in InternalShare.query.filter_by(recording_id=recording_id)}


def _sees(world, user_id, project_id):
    return world.client(user_id).get(f'/api/studio/projects/{project_id}').status_code == 200


@pytest.fixture
def shared(world, sharing_on, folders_on):
    owner, member, other = world.user(), world.user(), world.user()
    project_id = world.project(owner, 'Shared film')
    folder_id = _project(project_id).folder_id
    before = world.recording(owner, folder_id=folder_id, title='Before sharing')
    detail = world.share(owner, project_id, member)
    return dict(world=world, owner=owner, member=member, other=other, project=project_id, folder=folder_id,
                before=before, detail=detail)


def test_a_new_project_has_a_personal_folder_of_its_own(world):
    owner = world.user()
    project_id = world.project(owner, 'My film', hotwords='Zed, Quill', initial_prompt='A heist.')
    project = _project(project_id)
    folder = db.session.get(Folder, project.folder_id)
    assert folder.user_id == owner and folder.group_id is None
    assert folder.name == 'My film'
    assert folder.default_hotwords == 'Zed, Quill' and folder.default_initial_prompt == 'A heist.'
    assert project.group_id is None


def test_a_second_project_of_the_same_name_gets_its_own_folder(world):
    owner = world.user()
    first, second = world.project(owner, 'Twin'), world.project(owner, 'Twin')
    names = {db.session.get(Folder, _project(p).folder_id).name for p in (first, second)}
    assert names == {'Twin', 'Twin (2)'}


def test_sharing_makes_a_group_and_a_group_folder_and_shares_what_is_there(shared):
    project = _project(shared['project'])
    group = db.session.get(Group, project.group_id)
    folder = db.session.get(Folder, shared['folder'])
    assert group is not None and folder.group_id == group.id
    assert folder.auto_share_on_apply is True and folder.user_id == shared['owner']
    roles = {m.user_id: m.role for m in GroupMembership.query.filter_by(group_id=group.id)}
    assert roles == {shared['owner']: 'admin', shared['member']: 'member'}
    assert f"(project {project.id})" in group.name
    # The recording that was in the folder before sharing is shared, read-only.
    share = _shares(shared['before'])[shared['member']]
    assert share.source_type == 'group_folder' and share.source_folder_id == folder.id and share.can_edit is False
    assert [p['role'] for p in shared['detail']['people']] == ['owner', 'member']


def test_a_recording_added_later_is_shared_by_speakr_itself(shared):
    from src.services.auto_shares import apply_folder_shares
    world = shared['world']
    later = world.recording(shared['owner'], folder_id=shared['folder'], title='Later')
    apply_folder_shares(db.session.get(Recording, later), db.session.get(Folder, shared['folder']),
                        sharing_enabled=True)
    db.session.commit()
    assert shared['member'] in _shares(later)
    ids = [r['id'] for r in world.client(shared['member']).get(
        f"/api/studio/projects/{shared['project']}/recordings").get_json()['items']]
    assert set(ids) == {shared['before'], later}


def test_only_who_may_edit_a_recording_is_offered_its_transcript_import(shared):
    world = shared['world']
    url = f"/api/studio/projects/{shared['project']}/recordings"
    can_edit = lambda user: {r['id']: r['can_edit'] for r in world.client(user).get(url).get_json()['items']}
    assert can_edit(shared['owner'])[shared['before']] is True
    assert can_edit(shared['member'])[shared['before']] is False  # shared read-only


def test_a_member_upload_goes_into_the_group_folder(shared):
    from src.services.recording_ingestion import select_tags_and_folder
    member = db.session.get(User, shared['member'])
    chosen = select_tags_and_folder(form={'folder_id': str(shared['folder'])}, owner=member)
    assert chosen.selected_folder is not None and chosen.selected_folder.id == shared['folder']


def test_removing_a_person_ends_their_access_and_takes_their_recordings_out(shared):
    world = shared['world']
    theirs = world.recording(shared['member'], folder_id=shared['folder'], title='Theirs')
    from src.services.auto_shares import apply_folder_shares
    apply_folder_shares(db.session.get(Recording, theirs), db.session.get(Folder, shared['folder']),
                        sharing_enabled=True)
    db.session.commit()
    assert shared['owner'] in _shares(theirs)
    second = world.user()
    world.share(shared['owner'], shared['project'], shared['member'], second)
    world.share(shared['owner'], shared['project'], second)
    db.session.expire_all()
    assert not _sees(world, shared['member'], shared['project'])
    assert shared['member'] not in _shares(shared['before'])
    moved = db.session.get(Recording, theirs)
    assert moved.folder_id is None and moved.user_id == shared['member']
    assert _shares(theirs) == {}
    assert GroupMembership.query.filter_by(group_id=_project(shared['project']).group_id,
                                           user_id=shared['member']).first() is None


def test_removing_the_last_person_makes_the_project_private_again(shared):
    world = shared['world']
    group_id = _project(shared['project']).group_id
    detail = world.share(shared['owner'], shared['project'])
    assert [p['role'] for p in detail['people']] == ['owner']
    project = _project(shared['project'])
    assert project.group_id is None and db.session.get(Group, group_id) is None
    folder = db.session.get(Folder, shared['folder'])
    assert folder.group_id is None and folder.user_id == shared['owner']
    assert _shares(shared['before']) == {}
    assert db.session.get(Recording, shared['before']).folder_id == shared['folder']
    assert not _sees(world, shared['member'], shared['project'])


def test_people_are_checked_as_speakr_checks_the_people_a_share_names(world, sharing_on):
    from unittest.mock import patch
    owner, someone = world.user(), world.user()
    project_id = world.project(owner)
    client = world.client(owner)
    with patch('src.services.user_lookup.search_scope', return_value='exact'):
        # An id alone, for an account the owner could not find, is refused...
        refused = client.put(f'/api/studio/projects/{project_id}/people', json={'user_ids': [someone]})
        assert refused.status_code == 404
        assert _project(project_id).group_id is None
        # ...and accepted with the full username.
        name = db.session.get(User, someone).username
        accepted = client.put(f'/api/studio/projects/{project_id}/people',
                              json={'user_ids': [someone], 'identifiers': [name]})
        assert accepted.status_code == 200
    assert client.put(f'/api/studio/projects/{project_id}/people',
                      json={'user_ids': [], 'identifiers': ['nobody-is-called-this']}).status_code == 404
    assert client.put(f'/api/studio/projects/{project_id}/people', json={'user_ids': ['1']}).status_code == 400


def test_sharing_needs_internal_sharing(world, sharing_off):
    owner, someone = world.user(), world.user()
    project_id = world.project(owner)
    name = db.session.get(User, someone).username
    response = world.client(owner).put(f'/api/studio/projects/{project_id}/people',
                                       json={'user_ids': [someone], 'identifiers': [name]})
    assert response.status_code == 403
    assert _project(project_id).group_id is None
    assert world.client(owner).get(f'/api/studio/projects/{project_id}').get_json()['sharing_enabled'] is False


def test_a_folder_with_subfolders_is_not_shared(world, sharing_on):
    owner, someone = world.user(), world.user()
    project_id = world.project(owner)
    db.session.add(Folder(name='Inside', user_id=owner, parent_id=_project(project_id).folder_id))
    db.session.commit()
    name = db.session.get(User, someone).username
    response = world.client(owner).put(f'/api/studio/projects/{project_id}/people',
                                       json={'user_ids': [someone], 'identifiers': [name]})
    assert response.status_code == 409
    project = _project(project_id)
    assert project.group_id is None and Group.query.filter(Group.created_by == owner).count() == 0


# --- changes made in Speakr's own screens -----------------------------------

def test_folder_renamed_in_speakr_the_project_keeps_its_name(shared):
    world = shared['world']
    response = world.client(shared['owner']).put(f"/api/folders/{shared['folder']}", json={'name': 'Renamed there'})
    assert response.status_code == 200, response.get_data(as_text=True)
    detail = world.client(shared['owner']).get(f"/api/studio/projects/{shared['project']}").get_json()
    assert detail['name'] == 'Shared film' and detail['folder_name'] == 'Renamed there'
    assert _sees(world, shared['member'], shared['project'])


def test_renaming_the_project_renames_its_folder(world):
    owner = world.user()
    project_id = world.project(owner, 'Old name')
    response = world.client(owner).put(f'/api/studio/projects/{project_id}', json={'name': 'New name'})
    assert response.status_code == 200
    assert db.session.get(Folder, _project(project_id).folder_id).name == 'New name'


def test_folder_deleted_in_speakr_the_project_stays_and_gets_a_new_folder(shared):
    world = shared['world']
    owner = world.client(shared['owner'])
    assert owner.delete(f"/api/folders/{shared['folder']}").status_code == 200
    db.session.expire_all()
    assert db.session.get(Recording, shared['before']).folder_id is None
    detail = owner.get(f"/api/studio/projects/{shared['project']}").get_json()
    assert detail['folder_missing'] is True and detail['folder_id'] is None
    assert owner.get(f"/api/studio/projects/{shared['project']}/recordings").get_json()['items'] == []
    assert world.client(shared['member']).get(f"/studio/projects/{shared['project']}").status_code == 200
    # Adding a recording makes a new folder, still the group's.
    folder_id = owner.post(f"/api/studio/projects/{shared['project']}/recording-folder").get_json()['folder_id']
    folder = db.session.get(Folder, folder_id)
    assert folder.group_id == _project(shared['project']).group_id and folder.user_id == shared['owner']


def test_group_deleted_in_speakr_the_project_becomes_private(shared):
    world = shared['world']
    admin = world.user(admin=True)
    group_id = _project(shared['project']).group_id
    response = world.client(admin).delete(f'/api/admin/groups/{group_id}')
    assert response.status_code == 200, response.get_data(as_text=True)
    db.session.expire_all()
    assert not _sees(world, shared['member'], shared['project'])
    owner = world.client(shared['owner'])
    detail = owner.get(f"/api/studio/projects/{shared['project']}").get_json()
    assert [p['role'] for p in detail['people']] == ['owner']
    assert db.session.get(Folder, shared['folder']).group_id is None
    assert _shares(shared['before']) == {}
    # Sharing again makes a new group.
    world.share(shared['owner'], shared['project'], shared['member'])
    new_group = db.session.get(Group, _project(shared['project']).group_id)
    assert new_group is not None and new_group.created_by == shared['owner']
    assert GroupMembership.query.filter_by(group_id=new_group.id, user_id=shared['member']).first() is not None
    assert _sees(world, shared['member'], shared['project'])


def test_a_member_added_in_speakrs_group_screen_does_not_see_the_project(shared):
    world = shared['world']
    admin = world.user(admin=True)
    group_id = _project(shared['project']).group_id
    response = world.client(admin).post(f'/api/admin/groups/{group_id}/members',
                                        json={'user_id': shared['other'], 'role': 'member'})
    assert response.status_code == 201, response.get_data(as_text=True)
    assert not _sees(world, shared['other'], shared['project'])
    assert shared['project'] not in {p['id'] for p in world.client(shared['other']).get(
        '/api/studio/projects').get_json()['projects']}
    people = world.client(shared['owner']).get(f"/api/studio/projects/{shared['project']}").get_json()['people']
    assert shared['other'] not in {p['id'] for p in people}


def test_a_member_removed_in_speakrs_group_screen_loses_the_project(shared):
    world = shared['world']
    admin = world.user(admin=True)
    group_id = _project(shared['project']).group_id
    response = world.client(admin).delete(f"/api/admin/groups/{group_id}/members/{shared['member']}")
    assert response.status_code == 200
    assert not _sees(world, shared['member'], shared['project'])
    assert shared['member'] not in _shares(shared['before'])
    # The owner's list no longer shows them; saving it as it is keeps them off.
    people = world.client(shared['owner']).get(f"/api/studio/projects/{shared['project']}").get_json()['people']
    assert [p['role'] for p in people] == ['owner']
    world.share(shared['owner'], shared['project'])
    assert StudioProjectMember.query.filter_by(project_id=shared['project']).count() == 0


def test_a_members_role_changed_in_speakr_changes_nothing_in_the_studio(shared):
    world = shared['world']
    admin = world.user(admin=True)
    group_id = _project(shared['project']).group_id
    response = world.client(admin).put(f"/api/admin/groups/{group_id}/members/{shared['member']}",
                                       json={'role': 'admin'})
    assert response.status_code == 200
    detail = world.client(shared['member']).get(f"/api/studio/projects/{shared['project']}").get_json()
    assert detail['role'] == 'member' and detail['can']['share'] is False


def test_the_owner_taken_out_of_the_group_is_put_back_when_sharing_again(shared):
    world = shared['world']
    admin = world.user(admin=True)
    group_id = _project(shared['project']).group_id
    assert world.client(admin).delete(f"/api/admin/groups/{group_id}/members/{shared['owner']}").status_code == 200
    assert _sees(world, shared['owner'], shared['project'])
    world.share(shared['owner'], shared['project'], shared['member'])
    owner = GroupMembership.query.filter_by(group_id=group_id, user_id=shared['owner']).first()
    assert owner is not None and owner.role == 'admin'


def test_deleting_the_project_keeps_the_recordings(shared):
    world = shared['world']
    group_id = _project(shared['project']).group_id
    response = world.client(shared['owner']).delete(f"/api/studio/projects/{shared['project']}")
    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(StudioProject, shared['project']) is None
    assert db.session.get(Group, group_id) is None
    assert db.session.get(Folder, shared['folder']) is None
    recording = db.session.get(Recording, shared['before'])
    assert recording is not None and recording.folder_id is None
    assert _shares(shared['before']) == {}


def test_member_ids_need_both_the_list_and_the_group(shared):
    project = _project(shared['project'])
    assert access.member_ids(project) == {shared['member']}
    StudioProjectMember.query.filter_by(project_id=project.id).delete()
    db.session.commit()
    assert access.member_ids(_project(shared['project'])) == set()


# --- rows Speakr deletes (src/studio/cleanup.py) ----------------------------

def test_a_deleted_folder_is_let_go_of_so_a_reused_id_is_never_the_projects(world, folders_on):
    owner, other = world.user(), world.user()
    project_id = world.project(owner)
    folder_id = _project(project_id).folder_id
    assert world.client(owner).delete(f'/api/folders/{folder_id}').status_code == 200
    assert _project(project_id).folder_id is None
    # A folder of someone else under any id is never taken for the project's.
    stranger = Folder(name='Not yours', user_id=other)
    db.session.add(stranger)
    db.session.commit()
    project = _project(project_id)
    project.folder_id = stranger.id
    db.session.commit()
    assert access.project_folder(_project(project_id)) is None


def test_a_deleted_group_is_let_go_of(shared):
    group_id = _project(shared['project']).group_id
    group = db.session.get(Group, group_id)
    db.session.delete(group)
    db.session.commit()
    project = _project(shared['project'])
    assert project.group_id is None
    assert StudioProjectMember.query.filter_by(project_id=project.id).count() == 0


def test_a_deleted_account_takes_its_projects_and_their_files(world):
    from src.services.storage import get_storage_service
    from src.studio.models import StudioMaterial
    owner, admin = world.user(), world.user(admin=True)
    project_id = world.project(owner)
    up = world.client(owner).post(f'/api/studio/projects/{project_id}/materials',
                                  data={'files': [(__import__('io').BytesIO(b'%PDF-1.4\n%%EOF\n'), 'a.pdf')]},
                                  content_type='multipart/form-data')
    material_id = up.get_json()['stored'][0]['id']
    locator = db.session.get(StudioMaterial, material_id).storage_locator
    assert world.client(admin).delete(f'/admin/users/{owner}').status_code == 200
    db.session.expire_all()
    assert db.session.get(StudioProject, project_id) is None
    assert db.session.get(StudioMaterial, material_id) is None
    assert not get_storage_service().exists(locator)


def test_a_deleted_recording_takes_its_voice_reference(world):
    from src.studio.models import StudioVoiceReference
    owner = world.user()
    project_id = world.project(owner)
    recording_id = world.recording(owner, folder_id=_project(project_id).folder_id)
    reference = world.client(owner).post(f'/api/studio/projects/{project_id}/voice',
                                         json={'recording_id': recording_id}).get_json()
    db.session.delete(db.session.get(Recording, recording_id))
    db.session.commit()
    assert db.session.get(StudioVoiceReference, reference['id']) is None


def test_a_removed_persons_recording_is_not_shared_again_from_a_list_loaded_before(shared):
    from src.services.auto_shares import apply_folder_shares
    from src.studio import sharing
    world = shared['world']
    theirs = world.recording(shared['member'], folder_id=shared['folder'], title='Theirs')
    apply_folder_shares(db.session.get(Recording, theirs), db.session.get(Folder, shared['folder']),
                        sharing_enabled=True)
    db.session.commit()
    second = world.user()
    world.share(shared['owner'], shared['project'], shared['member'], second)
    assert second in _shares(theirs)
    project = _project(shared['project'])
    folder = db.session.get(Folder, shared['folder'])
    assert theirs in [r.id for r in folder.recordings]  # the list is loaded now
    sharing.set_people(project, {second})
    db.session.commit()
    assert _shares(theirs) == {}
    assert db.session.get(Recording, theirs).folder_id is None
