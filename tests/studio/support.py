"""Shared set-up for the studio add-on's tests.

Isolated database via the repo-root conftest (SHARED-DB: everything a test
makes is removed afterwards, in an order PostgreSQL accepts). Login by
session injection, as upstream's tests do. Hermetic and offline.
"""

import io
import sys
import uuid

import pytest
from flask import g
from flask.testing import FlaskClient

from src.app import app, db
from src.models import (Folder, Group, GroupMembership, InternalShare, ProcessingJob, Recording, RecordingTag,
                        SharedRecordingState, User)
from src.studio.models import (StudioAdminGrant, StudioAdminProject, StudioChat, StudioMaterial, StudioMessage,
                               StudioProject, StudioProjectMember, StudioProjectVisit, StudioVoiceReference)

app.config['WTF_CSRF_ENABLED'] = False
app.config['TESTING'] = True


class Client(FlaskClient):
    """A test client whose every request loads its user again (no user cached in g)."""

    def open(self, *args, **kwargs):
        try:
            g.pop('_login_user', None)
        except RuntimeError:
            pass
        return super().open(*args, **kwargs)


class World:
    """Users, recordings and projects made by one test, removed by cleanup()."""

    def __init__(self):
        self.users = []

    def user(self, admin=False, prefix='st'):
        suffix = uuid.uuid4().hex[:8]
        user = User(username=f'{prefix}_{suffix}', email=f'{prefix}_{suffix}@example.com', password='x',
                    is_admin=admin)
        db.session.add(user)
        db.session.commit()
        self.users.append(user.id)
        return user.id

    def client(self, user_id=None):
        client = Client(app, app.response_class)
        if user_id is not None:
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True
        return client

    def recording(self, owner_id, folder_id=None, status='COMPLETED', title='A recording'):
        recording = Recording(user_id=owner_id, title=title, audio_path='/tmp/studio-test.mp3', status=status,
                              folder_id=folder_id)
        db.session.add(recording)
        db.session.commit()
        return recording.id

    def project(self, owner_id, name='A film', **fields):
        response = self.client(owner_id).post('/api/studio/projects', json={'name': name, **fields})
        assert response.status_code == 201, response.get_data(as_text=True)
        return response.get_json()['id']

    def share(self, owner_id, project_id, *member_ids):
        names = [db.session.get(User, uid).username for uid in member_ids]
        response = self.client(owner_id).put(f'/api/studio/projects/{project_id}/people',
                                             json={'user_ids': list(member_ids), 'identifiers': names})
        assert response.status_code == 200, response.get_data(as_text=True)
        return response.get_json()

    def cleanup(self):
        from src.studio import materials
        db.session.rollback()
        ids = self.users
        if not ids:
            return
        projects = StudioProject.query.filter(StudioProject.owner_id.in_(ids)).all()
        project_ids = [p.id for p in projects]
        group_ids = {p.group_id for p in projects if p.group_id}
        group_ids |= {gid for (gid,) in db.session.query(Group.id).filter(Group.created_by.in_(ids))}
        StudioProjectVisit.query.filter(StudioProjectVisit.user_id.in_(ids)).delete(synchronize_session=False)
        StudioAdminGrant.query.filter(StudioAdminGrant.user_id.in_(ids)).delete(synchronize_session=False)
        # Chats of the test's projects, and any chat or message of its people elsewhere.
        chat_ids = {cid for (cid,) in db.session.query(StudioChat.id).filter(db.or_(
            StudioChat.project_id.in_(project_ids or [-1]), StudioChat.created_by.in_(ids)))}
        chat_ids |= {cid for (cid,) in db.session.query(StudioMessage.chat_id).filter(StudioMessage.sender_id.in_(ids))}
        if chat_ids:
            StudioMessage.query.filter(StudioMessage.chat_id.in_(chat_ids)).delete(synchronize_session=False)
            StudioChat.query.filter(StudioChat.id.in_(chat_ids)).delete(synchronize_session=False)
        if project_ids:
            for material in StudioMaterial.query.filter(StudioMaterial.project_id.in_(project_ids)).all():
                materials.delete_stored_file(material)
            for model in (StudioMaterial, StudioVoiceReference, StudioProjectMember, StudioProjectVisit,
                          StudioAdminGrant, StudioAdminProject):
                model.query.filter(model.project_id.in_(project_ids)).delete(synchronize_session=False)
            StudioProject.query.filter(StudioProject.id.in_(project_ids)).delete(synchronize_session=False)
        recording_ids = [rid for (rid,) in db.session.query(Recording.id).filter(Recording.user_id.in_(ids))]
        if recording_ids:
            StudioVoiceReference.query.filter(StudioVoiceReference.recording_id.in_(recording_ids)).delete(
                synchronize_session=False)
            for model in (InternalShare, SharedRecordingState, RecordingTag, ProcessingJob):
                model.query.filter(model.recording_id.in_(recording_ids)).delete(synchronize_session=False)
            Recording.query.filter(Recording.id.in_(recording_ids)).delete(synchronize_session=False)
        InternalShare.query.filter(InternalShare.shared_with_user_id.in_(ids)).delete(synchronize_session=False)
        SharedRecordingState.query.filter(SharedRecordingState.user_id.in_(ids)).delete(synchronize_session=False)
        GroupMembership.query.filter(GroupMembership.user_id.in_(ids)).delete(synchronize_session=False)
        if group_ids:
            GroupMembership.query.filter(GroupMembership.group_id.in_(group_ids)).delete(synchronize_session=False)
            Folder.query.filter(Folder.group_id.in_(group_ids)).update({'group_id': None}, synchronize_session=False)
            Group.query.filter(Group.id.in_(group_ids)).delete(synchronize_session=False)
        Folder.query.filter(Folder.user_id.in_(ids)).delete(synchronize_session=False)
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def world():
    with app.app_context():
        made = World()
        try:
            yield made
        finally:
            made.cleanup()


def _sharing_modules():
    """Every module that reads ENABLE_INTERNAL_SHARING as a hot-applied setting."""
    found = [sys.modules['src.app']]
    for module in list(sys.modules.values()):
        names = getattr(module, '__dict__', {}).get('_SYSTEM_SETTING_MAP')
        if isinstance(names, dict) and 'ENABLE_INTERNAL_SHARING' in names and module not in found:
            found.append(module)
    return found


def _set_sharing(value):
    saved = []
    for module in _sharing_modules():
        had = 'ENABLE_INTERNAL_SHARING' in module.__dict__
        saved.append((module, had, module.__dict__.get('ENABLE_INTERNAL_SHARING')))
        module.ENABLE_INTERNAL_SHARING = value
    return saved


def _restore_sharing(saved):
    # Put back exactly what was there, so the hot-applied setting
    # (a module __getattr__) is not frozen for later test files.
    for module, had, old in saved:
        if had:
            module.ENABLE_INTERNAL_SHARING = old
        else:
            module.__dict__.pop('ENABLE_INTERNAL_SHARING', None)


@pytest.fixture
def sharing_on():
    saved = _set_sharing(True)
    try:
        yield
    finally:
        _restore_sharing(saved)


@pytest.fixture
def sharing_off():
    saved = _set_sharing(False)
    try:
        yield
    finally:
        _restore_sharing(saved)


@pytest.fixture
def admins_see(monkeypatch):
    """STUDIO_ADMINS_SEE_RECORDINGS on for one test (read from the environment on every call)."""
    monkeypatch.setenv('STUDIO_ADMINS_SEE_RECORDINGS', 'true')
    yield


@pytest.fixture
def folders_on():
    from src.models import SystemSetting
    with app.app_context():
        old = SystemSetting.get_setting('enable_folders', None)
        SystemSetting.set_setting('enable_folders', 'true', setting_type='boolean')
    yield
    with app.app_context():
        SystemSetting.set_setting('enable_folders', 'true' if old else 'false', setting_type='boolean')


def file_part(content, name):
    return (io.BytesIO(content), name)
