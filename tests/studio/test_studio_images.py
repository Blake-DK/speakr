"""Generated images: stored as materials, only with a key, the model chosen, and errors in plain words.
OpenAI's client is faked: no real call is made."""

import base64
import json
from types import SimpleNamespace

import pytest

from src.app import db
from src.studio import images, providers
from src.studio.models import StudioMaterial, StudioSetting
from tests.studio.support import folders_on, sharing_on, world  # noqa: F401

openai = pytest.importorskip('openai')

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


class FakeImages:
    def __init__(self, data=PNG, error=None):
        self.data = data
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(self.data).decode())])


@pytest.fixture
def fake(monkeypatch):
    holder = FakeImages()
    monkeypatch.setattr(images, '_client_factory', lambda: SimpleNamespace(images=holder))
    monkeypatch.delenv('STUDIO_OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('STUDIO_OPENAI_IMAGE_MODEL', raising=False)
    yield holder
    StudioSetting.query.delete()
    db.session.commit()


@pytest.fixture
def owned(world, fake):
    owner = world.user()
    return world, owner, world.project(owner, 'Poster film', logline='A heist on the moon')


def _post(world, user_id, project_id, **body):
    return world.client(user_id).post(f'/api/studio/projects/{project_id}/images',
                                      json=body or {'prompt': 'Film poster for "Poster film"'})


def _http():
    """The HTTP library of the installed openai package (httpx2 in newer releases, httpx before)."""
    try:
        import httpx2
        return httpx2
    except ImportError:
        import httpx
        return httpx


def _status_error(cls, status):
    http = _http()
    request = http.Request('POST', 'https://api.openai.com/v1/images/generations')
    return cls('refused', response=http.Response(status, request=request), body=None)


def test_without_a_key_nothing_is_called_and_the_page_is_told_why(owned):
    world, owner, project_id = owned
    response = _post(world, owner, project_id)
    assert response.status_code == 400 and 'STUDIO_OPENAI_API_KEY' in response.get_json()['error']
    assert world.client(owner).get('/api/studio/chat-config').get_json()['images'] == {
        'available': False, 'has_key': False, 'enabled': True, 'model': 'gpt-image-1',
        'key_env': 'STUDIO_OPENAI_API_KEY', 'sizes': ['1024x1536', '1024x1024', '1536x1024'],
        'qualities': ['low', 'medium', 'high']}
    assert world.client(owner).get(f'/api/studio/projects/{project_id}/materials').get_json()['items'] == []


def test_an_image_is_stored_as_a_material_of_the_project(owned, fake, monkeypatch):
    world, owner, project_id = owned
    monkeypatch.setenv('STUDIO_OPENAI_API_KEY', 'test-key-not-real')
    response = _post(world, owner, project_id, prompt='Film poster for "Poster film": moon, vault, neon.',
                     size='1536x1024', quality='high')
    assert response.status_code == 201, response.get_data(as_text=True)
    made = response.get_json()
    assert made['name'] == 'film-poster-for-poster-film-moon-vault-n.png'
    assert made['kind'] == 'image' and made['size'] == len(PNG)
    assert fake.calls == [{'model': 'gpt-image-1', 'prompt': 'Film poster for "Poster film": moon, vault, neon.',
                           'size': '1536x1024', 'quality': 'high'}]
    material = db.session.get(StudioMaterial, made['id'])
    assert material.uploaded_by == owner and material.content_type == 'image/png'
    file = world.client(owner).get(f"/api/studio/materials/{made['id']}/file")
    assert file.status_code == 200 and file.get_data() == PNG
    assert 'test-key-not-real' not in json.dumps(made)


def test_the_model_comes_from_the_settings_then_the_environment(owned, fake, monkeypatch):
    world, owner, project_id = owned
    monkeypatch.setenv('STUDIO_OPENAI_API_KEY', 'test-key-not-real')
    monkeypatch.setenv('STUDIO_OPENAI_IMAGE_MODEL', 'gpt-image-env')
    _post(world, owner, project_id)
    assert fake.calls[-1]['model'] == 'gpt-image-env'
    admin = world.client(world.user(admin=True))
    assert admin.put('/api/studio/settings', json={'openai_image_model': 'bad model; drop'}).status_code == 400
    saved = admin.put('/api/studio/settings', json={'openai_image_model': 'gpt-image-2'})
    assert saved.status_code == 200 and saved.get_json()['image_model_setting'] == 'gpt-image-2'
    _post(world, owner, project_id)
    assert fake.calls[-1]['model'] == 'gpt-image-2'
    assert admin.put('/api/studio/settings', json={'openai_image_model': ''}).get_json()['images']['model'] == (
        'gpt-image-env')


def test_what_a_request_may_ask_for(owned, fake, monkeypatch):
    world, owner, project_id = owned
    monkeypatch.setenv('STUDIO_OPENAI_API_KEY', 'test-key-not-real')
    assert _post(world, owner, project_id, prompt='   ').status_code == 400
    assert _post(world, owner, project_id, prompt='x' * (images.PROMPT_MAX + 1)).status_code == 400
    assert _post(world, owner, project_id, prompt='p', size='4096x4096').status_code == 400
    assert _post(world, owner, project_id, prompt='p', quality='ultra').status_code == 400
    assert fake.calls == []


def test_switched_off_with_gpt_nothing_is_called(owned, fake, monkeypatch):
    world, owner, project_id = owned
    monkeypatch.setenv('STUDIO_OPENAI_API_KEY', 'test-key-not-real')
    providers.set_setting('openai_enabled', '0')
    db.session.commit()
    response = _post(world, owner, project_id)
    assert response.status_code == 400 and response.get_json()['code'] == 'off'
    assert fake.calls == []


@pytest.mark.parametrize('make_error, code', [
    (lambda: _status_error(openai.AuthenticationError, 401), 'keyRefused'),
    (lambda: _status_error(openai.RateLimitError, 429), 'rateLimited'),
    (lambda: _status_error(openai.BadRequestError, 400), 'api'),
    (lambda: openai.APIConnectionError(request=_http().Request('POST', 'https://api.openai.com/')), 'connection'),
])
def test_errors_are_said_plainly_and_nothing_is_stored(owned, fake, monkeypatch, make_error, code):
    world, owner, project_id = owned
    monkeypatch.setenv('STUDIO_OPENAI_API_KEY', 'test-key-not-real')
    fake.error = make_error()
    response = _post(world, owner, project_id)
    assert response.status_code == 502 and response.get_json()['code'] == code
    assert 'test-key-not-real' not in response.get_data(as_text=True)
    assert StudioMaterial.query.filter_by(project_id=project_id).count() == 0


def test_something_that_is_not_a_png_is_not_stored(owned, fake, monkeypatch):
    world, owner, project_id = owned
    monkeypatch.setenv('STUDIO_OPENAI_API_KEY', 'test-key-not-real')
    fake.data = b'<svg onload="x()"></svg>'
    response = _post(world, owner, project_id)
    assert response.status_code == 502 and response.get_json()['code'] == 'noImage'
    assert StudioMaterial.query.filter_by(project_id=project_id).count() == 0


def test_people_on_the_list_may_make_images_strangers_may_not(owned, fake, monkeypatch, sharing_on, folders_on):
    world, owner, project_id = owned
    monkeypatch.setenv('STUDIO_OPENAI_API_KEY', 'test-key-not-real')
    member = world.user()
    world.share(owner, project_id, member)
    assert _post(world, member, project_id).status_code == 201
    assert _post(world, world.user(), project_id).status_code == 404
    assert len(fake.calls) == 1


def test_a_made_file_goes_through_the_upload_checks(owned):
    from src.models import User
    from src.studio import materials
    from src.studio.models import StudioProject
    world, owner, project_id = owned
    project = db.session.get(StudioProject, project_id)
    user = db.session.get(User, owner)
    with pytest.raises(materials.MaterialError) as wrong:
        materials.store_bytes(project, user, b'GIF89a not a png', 'poster.png')
    assert wrong.value.key == 'studio.materials.errors.content'
    with pytest.raises(materials.MaterialError):
        materials.store_bytes(project, user, b'', 'poster.png')
    with pytest.raises(materials.MaterialError):
        materials.store_bytes(project, user, PNG, 'poster.svg')
    db.session.rollback()
    assert StudioMaterial.query.filter_by(project_id=project_id).count() == 0


def test_a_deleted_projects_empty_material_folder_goes(owned):
    import os

    from src.services.storage import get_storage_service
    from src.services.storage.locator import local_path_from_key
    from tests.studio.support import file_part
    world, owner, project_id = owned
    storage = get_storage_service()
    if storage.default_backend_kind() == 's3':
        pytest.skip('local storage only')
    up = world.client(owner).post(f'/api/studio/projects/{project_id}/materials',
                                  data={'files': [file_part(PNG, 'still.png')]}, content_type='multipart/form-data')
    assert up.status_code == 201
    folder = local_path_from_key(storage.settings.local_root, f'studio/materials/{project_id}')
    assert os.path.isdir(folder)
    assert world.client(owner).delete(f'/api/studio/projects/{project_id}').status_code == 200
    assert not os.path.exists(folder)
