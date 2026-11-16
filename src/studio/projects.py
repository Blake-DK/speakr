"""Projects: creating, describing and changing them, and the recordings they hold.

The routes in src/studio/routes.py stay thin and call these functions; every
check of who may do what is in src/studio/access.py.
"""

from datetime import datetime

from src.database import db
from src.studio import access, sharing

MAX_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 20_000
MAX_HINT_LENGTH = 2_000


class ProjectError(Exception):
    """A refused change; ``key`` names the studio string for it."""

    def __init__(self, key, status=400, **params):
        super().__init__(key)
        self.key = key
        self.status = status
        self.params = params


def _text(data, field, limit, required=False):
    value = data.get(field, '')
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ProjectError('studio.errors.fieldInvalid', field=field)
    value = value.strip()
    if required and not value:
        raise ProjectError('studio.errors.nameRequired')
    if len(value) > limit:
        raise ProjectError('studio.errors.fieldTooLong', field=field, max=limit)
    return value


def create(owner, data):
    """A new private project with its own folder. Commits."""
    from src.studio.models import StudioProject
    if not isinstance(data, dict):
        raise ProjectError('studio.errors.nameRequired')
    project = StudioProject(
        owner_id=owner.id,
        name=' '.join(_text(data, 'name', MAX_NAME_LENGTH, required=True).split()),
        logline=_text(data, 'logline', MAX_TEXT_LENGTH),
        notes=_text(data, 'notes', MAX_TEXT_LENGTH),
    )
    db.session.add(project)
    db.session.flush()
    sharing.make_folder(project,
                        hotwords=_text(data, 'hotwords', MAX_HINT_LENGTH),
                        initial_prompt=_text(data, 'initial_prompt', MAX_HINT_LENGTH))
    db.session.commit()
    return project


def update(project, caps, data):
    """Change what the request sends that the viewer may change. Commits."""
    if not isinstance(data, dict):
        data = {}
    if 'name' in data:
        if not caps['rename']:
            raise ProjectError('studio.errors.ownerOnly', 403)
        name = ' '.join(_text(data, 'name', MAX_NAME_LENGTH, required=True).split())
        if name != project.name:
            project.name = name
            sharing.rename_folder(project)
    for field in ('logline', 'notes'):
        if field in data:
            if not caps['edit_details']:
                raise ProjectError('studio.errors.ownerOnly', 403)
            setattr(project, field, _text(data, field, MAX_TEXT_LENGTH))
    if 'hotwords' in data or 'initial_prompt' in data:
        if not caps['edit_hints']:
            raise ProjectError('studio.errors.ownerOnly', 403)
        folder = access.project_folder(project)
        hotwords = _text(data, 'hotwords', MAX_HINT_LENGTH) if 'hotwords' in data else (
            (folder.default_hotwords or '') if folder else '')
        prompt = _text(data, 'initial_prompt', MAX_HINT_LENGTH) if 'initial_prompt' in data else (
            (folder.default_initial_prompt or '') if folder else '')
        sharing.set_hints(project, hotwords, prompt)
    project.updated_at = datetime.utcnow()
    db.session.commit()
    return project


def _owner_name(project):
    from src.models import User
    owner = db.session.get(User, project.owner_id)
    return owner.username if owner else None


def summary(project, user):
    """A project as the Projects list shows it."""
    from src.studio.models import StudioMaterial
    role = access.role_of(project, user)
    return {
        'id': project.id,
        'name': project.name,
        'logline': project.logline or '',
        'owner_id': project.owner_id,
        'owner_name': _owner_name(project),
        'role': role,
        'shared': bool(access.member_ids(project)),
        'material_count': StudioMaterial.query.filter_by(project_id=project.id).count(),
        'recording_count': len(visible_recordings(project, user)),
        'created_at': project.created_at.isoformat() + 'Z' if project.created_at else None,
    }


def detail(project, role, user):
    """A project as its home page shows it."""
    from src.services import user_lookup
    folder = access.project_folder(project)
    return {
        'id': project.id,
        'name': project.name,
        'logline': project.logline or '',
        'notes': project.notes or '',
        'hotwords': (folder.default_hotwords or '') if folder else '',
        'initial_prompt': (folder.default_initial_prompt or '') if folder else '',
        'owner_id': project.owner_id,
        'owner_name': _owner_name(project),
        'role': role,
        'folder_id': folder.id if folder else None,
        'folder_name': folder.name if folder else None,
        # Every project gets a folder when it is made, so no folder means it was
        # deleted in Speakr's folder screen (PostgreSQL has nulled the id by then).
        'folder_missing': folder is None,
        'people': sharing.people(project),
        'sharing_enabled': access.sharing_enabled(),
        'user_search_scope': user_lookup.search_scope(),
        'can': access.capabilities(project, role, user),
        'created_at': project.created_at.isoformat() + 'Z' if project.created_at else None,
        'updated_at': project.updated_at.isoformat() + 'Z' if project.updated_at else None,
    }


# --- recordings -------------------------------------------------------------

def visible_recordings(project, user):
    """The recordings in the project's folder that Speakr lets the user open, newest first."""
    from src.app import has_recording_access
    from src.models import Recording
    folder = access.project_folder(project)
    if folder is None:
        return []
    rows = Recording.query.filter(Recording.folder_id == folder.id).order_by(
        Recording.created_at.desc(), Recording.id.desc()).all()
    return [r for r in rows if has_recording_access(r, user)]


def recording_list(project, user):
    from src.models import User
    rows = visible_recordings(project, user)
    owners = {u.id: u.username for u in User.query.filter(User.id.in_({r.user_id for r in rows if r.user_id})).all()}
    return [{
        'id': r.id,
        'title': r.title or r.original_filename or '',
        'status': r.status,
        'created_at': r.created_at.isoformat() + 'Z' if r.created_at else None,
        'meeting_date': r.meeting_date.isoformat() if r.meeting_date else None,
        'duration_seconds': r.audio_duration_seconds,
        'owner_name': owners.get(r.user_id),
        'is_mine': r.user_id == user.id,
        'is_archived': bool(r.is_archived),
        'url': f'/recordings/{r.id}',
    } for r in rows]


def recording_for_voice(project, user, recording_id):
    """A recording of the project the user may open, for a voice reference, else ProjectError."""
    if isinstance(recording_id, bool) or not isinstance(recording_id, int):
        raise ProjectError('studio.voice.errors.recording', 404)
    for recording in visible_recordings(project, user):
        if recording.id == recording_id:
            return recording
    raise ProjectError('studio.voice.errors.recording', 404)


def recording_titles(ids, user):
    """{id: title} of the recordings among ids that the user may open."""
    from src.app import has_recording_access
    from src.models import Recording
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {r.id: (r.title or r.original_filename or '') for r in Recording.query.filter(Recording.id.in_(ids)).all()
            if has_recording_access(r, user)}
