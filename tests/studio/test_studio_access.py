"""Who sees a studio project: the access test of the standalone app the
studio replaces, ported, and a non-member check of every studio route.

That test asserted:

    owner must have access
    explicit member must have access
    admin has implicit access to every project
    outsider must not have access

Here the explicit member is a person the owner put on the People list, and
"access" is checked through the routes, not only the access function.
"""

import pytest

from src.app import db
from src.studio import access
from src.studio.models import StudioMaterial, StudioProject, StudioVoiceReference
from tests.studio.support import file_part, sharing_on, world  # noqa: F401

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


@pytest.fixture
def matrix(world, sharing_on):
    owner, member, admin, outsider = world.user(), world.user(), world.user(admin=True), world.user()
    project_id = world.project(owner, 'Matrix')
    folder_id = db.session.get(StudioProject, project_id).folder_id
    recording_id = world.recording(owner, folder_id=folder_id)
    world.share(owner, project_id, member)  # shares the recording already in the folder
    up = world.client(owner).post(f'/api/studio/projects/{project_id}/materials',
                                  data={'files': [file_part(PDF, 'deck.pdf')]}, content_type='multipart/form-data')
    assert up.status_code == 201, up.get_data(as_text=True)
    material_id = up.get_json()['stored'][0]['id']
    voice = world.client(owner).post(f'/api/studio/projects/{project_id}/voice', json={'title': 't', 'text': 'Hello.'})
    assert voice.status_code == 201
    return dict(world=world, owner=owner, member=member, admin=admin, outsider=outsider, project=project_id,
                folder=folder_id, recording=recording_id, material=material_id, voice=voice.get_json()['id'])


def _user(user_id):
    from src.models import User
    return db.session.get(User, user_id)


def test_the_ported_access_matrix(matrix):
    project = db.session.get(StudioProject, matrix['project'])
    assert access.role_of(project, _user(matrix['owner'])) == access.OWNER, "owner must have access"
    assert access.role_of(project, _user(matrix['member'])) == access.MEMBER, "explicit member must have access"
    assert access.role_of(project, _user(matrix['admin'])) == access.ADMIN, "admin has implicit access to every project"
    assert access.role_of(project, _user(matrix['outsider'])) is None, "outsider must not have access"


def test_the_projects_list_follows_the_matrix(matrix):
    def listed(user_id):
        response = matrix['world'].client(user_id).get('/api/studio/projects')
        assert response.status_code == 200
        return {p['id'] for p in response.get_json()['projects']}

    assert matrix['project'] in listed(matrix['owner'])
    assert matrix['project'] in listed(matrix['member'])
    assert matrix['project'] in listed(matrix['admin'])
    assert matrix['project'] not in listed(matrix['outsider'])


ROUTES = [
    ('get', '/studio/projects/{p}', None),
    ('get', '/api/studio/projects/{p}', None),
    ('put', '/api/studio/projects/{p}', {'notes': 'x'}),
    ('delete', '/api/studio/projects/{p}', None),
    ('get', '/api/studio/projects/{p}/recordings', None),
    ('post', '/api/studio/projects/{p}/recording-folder', None),
    ('put', '/api/studio/projects/{p}/people', {'user_ids': []}),
    ('get', '/api/studio/projects/{p}/materials', None),
    ('post', '/api/studio/projects/{p}/materials', None),
    ('get', '/api/studio/materials/{m}', None),
    ('get', '/api/studio/materials/{m}/file', None),
    ('delete', '/api/studio/materials/{m}', None),
    ('get', '/api/studio/projects/{p}/voice', None),
    ('post', '/api/studio/projects/{p}/voice', {'text': 'x'}),
    ('get', '/api/studio/voice/{v}', None),
    ('delete', '/api/studio/voice/{v}', None),
]


def _routes(m):
    return [(method, path.format(p=m['project'], m=m['material'], v=m['voice']), body)
            for method, path, body in ROUTES]


def test_every_studio_route_with_an_id_is_covered_here():
    from src.app import app
    covered = {(method.upper(), path) for method, path, _ in ROUTES}
    with_ids = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('studio.') and rule.arguments:
            path = (rule.rule.replace('<int:project_id>', '{p}').replace('<int:material_id>', '{m}')
                    .replace('<int:reference_id>', '{v}'))
            for method in rule.methods - {'HEAD', 'OPTIONS'}:
                with_ids.add((method, path))
    assert with_ids == covered


def test_a_non_member_gets_not_found_on_every_route_and_nothing_changes(matrix):
    client = matrix['world'].client(matrix['outsider'])
    for method, path, body in _routes(matrix):
        response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert response.status_code == 404, (method, path, response.status_code)
        if path.startswith('/api/'):
            assert 'Matrix' not in response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(StudioProject, matrix['project']).notes == ''
    assert db.session.get(StudioMaterial, matrix['material']) is not None
    assert db.session.get(StudioVoiceReference, matrix['voice']) is not None


def test_an_unknown_id_is_the_same_not_found(matrix):
    client = matrix['world'].client(matrix['outsider'])
    missing = client.get('/api/studio/projects/987654321')
    hidden = client.get(f"/api/studio/projects/{matrix['project']}")
    assert missing.status_code == hidden.status_code == 404
    assert missing.get_json() == hidden.get_json()


def test_signed_out_visitors_reach_nothing(matrix):
    client = matrix['world'].client()
    for method, path, body in _routes(matrix):
        response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert response.status_code in (302, 401), (method, path, response.status_code)


def test_a_member_reads_everything_and_may_not_rename_share_or_delete(matrix):
    client = matrix['world'].client(matrix['member'])
    p = matrix['project']
    detail = client.get(f'/api/studio/projects/{p}').get_json()
    assert detail['role'] == 'member'
    assert detail['can'] == {'edit_details': True, 'rename': False, 'edit_hints': False, 'share': False,
                             'delete': False, 'add_recording': True, 'add_material': True, 'add_voice': True}
    assert [r['id'] for r in client.get(f'/api/studio/projects/{p}/recordings').get_json()['items']] == [
        matrix['recording']]
    assert client.get(f"/api/studio/materials/{matrix['material']}/file").status_code == 200
    assert client.put(f'/api/studio/projects/{p}', json={'notes': 'A note'}).status_code == 200
    assert client.put(f'/api/studio/projects/{p}', json={'name': 'Renamed'}).status_code == 403
    assert client.put(f'/api/studio/projects/{p}', json={'hotwords': 'x'}).status_code == 403
    assert client.put(f'/api/studio/projects/{p}/people', json={'user_ids': []}).status_code == 403
    assert client.delete(f'/api/studio/projects/{p}').status_code == 403
    # Someone else's material and voice reference: not theirs to delete.
    assert client.delete(f"/api/studio/materials/{matrix['material']}").status_code == 403
    assert client.delete(f"/api/studio/voice/{matrix['voice']}").status_code == 403


def test_an_admin_sees_the_project_but_not_other_peoples_recordings(matrix):
    client = matrix['world'].client(matrix['admin'])
    p = matrix['project']
    detail = client.get(f'/api/studio/projects/{p}').get_json()
    assert detail['role'] == 'admin'
    assert detail['can']['add_recording'] is False
    assert detail['can']['share'] is False and detail['can']['delete'] is False
    assert client.get(f"/api/studio/materials/{matrix['material']}").status_code == 200
    # Speakr does not let an admin open other people's recordings, so neither does the project.
    assert client.get(f'/api/studio/projects/{p}/recordings').get_json()['items'] == []
    assert client.post(f'/api/studio/projects/{p}/recording-folder').status_code == 403


def test_an_admin_may_delete_a_project_whose_owner_is_gone(world):
    owner, admin = world.user(), world.user(admin=True)
    project_id = world.project(owner, 'Orphan')
    project = db.session.get(StudioProject, project_id)
    user = _user(admin)
    assert access.capabilities(project, access.ADMIN, user)['delete'] is False
    from unittest.mock import patch
    with patch.object(access, 'owner_exists', return_value=False):
        assert access.capabilities(project, access.ADMIN, user)['delete'] is True
