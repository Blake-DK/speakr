"""An audit row for whatever an administrator changes in a project that is not theirs.

The studio's routes are the only way to change a project, so one pair of hooks
on the studio blueprint covers them all: before a write request it finds the
project and the viewer's role (src/studio/access.py), after a successful answer
it records one event when that role is ADMIN (an administrator who is neither
the owner nor on the People list). The actor is the administrator (upstream's
audit service takes it from the request), the target user is the project's
owner, and the details name the project and what kind of thing changed. A
project's owner and the people on its People list write no new rows.

The events are registered in upstream's registry (src/services/audit.py) when
the blueprint is registered, in its group "change", and documented with the
others in docs/admin-guide/audit-log.md.
"""

from flask import g, request
from flask_login import current_user

from src.database import db

EVENTS = {
    'studio.project.changed': ('change', frozenset({'project_id', 'fields'})),
    'studio.project.deleted': ('change', frozenset({'project_id'})),
    'studio.project.people_changed': ('change', frozenset({'project_id'})),
    'studio.item.added': ('change', frozenset({'project_id', 'kind'})),
    'studio.item.changed': ('change', frozenset({'project_id', 'kind', 'item_id'})),
    'studio.item.deleted': ('change', frozenset({'project_id', 'kind', 'item_id'})),
}

# endpoint -> (event, kind, the view argument that names the thing)
ENDPOINTS = {
    'studio.update_project': ('studio.project.changed', None, 'project_id'),
    'studio.delete_project': ('studio.project.deleted', None, 'project_id'),
    'studio.set_project_people': ('studio.project.people_changed', None, 'project_id'),
    'studio.upload_materials': ('studio.item.added', 'material', 'project_id'),
    'studio.add_voice': ('studio.item.added', 'voice', 'project_id'),
    'studio.create_project_document': ('studio.item.added', 'document', 'project_id'),
    'studio.generate_project_image': ('studio.item.added', 'image', 'project_id'),
    'studio.create_project_chat': ('studio.item.added', 'chat', 'project_id'),
    'studio.update_document': ('studio.item.changed', 'document', 'document_id'),
    'studio.rename_chat': ('studio.item.changed', 'chat', 'chat_id'),
    'studio.edit_chat_message': ('studio.item.changed', 'message', 'message_id'),
    'studio.delete_material': ('studio.item.deleted', 'material', 'material_id'),
    'studio.delete_voice': ('studio.item.deleted', 'voice', 'reference_id'),
    'studio.delete_document': ('studio.item.deleted', 'document', 'document_id'),
    'studio.delete_chat': ('studio.item.deleted', 'chat', 'chat_id'),
}


def _project_id(arg, value):
    from src.studio.models import StudioChat, StudioDocument, StudioMaterial, StudioMessage, StudioVoiceReference
    if arg == 'project_id':
        return value
    model = {'material_id': StudioMaterial, 'reference_id': StudioVoiceReference,
             'document_id': StudioDocument, 'chat_id': StudioChat}.get(arg)
    if model is not None:
        row = db.session.get(model, value)
        return getattr(row, 'project_id', None)
    if arg == 'message_id':
        message = db.session.get(StudioMessage, value)
        chat = db.session.get(StudioChat, message.chat_id) if message is not None else None
        return getattr(chat, 'project_id', None)
    return None


def _before():
    entry = ENDPOINTS.get(request.endpoint)
    if entry is None or not getattr(current_user, 'is_authenticated', False) or not getattr(current_user, 'is_admin', False):
        return
    event, kind, arg = entry
    value = (request.view_args or {}).get(arg)
    project_id = _project_id(arg, value) if value is not None else None
    if project_id is None:
        return
    from src.studio import access
    project, role = access.project_for(project_id, current_user)
    if project is None or role != access.ADMIN:
        return
    details = {'project_id': project.id}
    if kind:
        details['kind'] = kind
    if arg != 'project_id':
        details['item_id'] = value
    if event == 'studio.project.changed':
        body = request.get_json(silent=True) or {}
        details['fields'] = sorted(k for k in body if isinstance(k, str))[:20]
    g._studio_admin_audit = (event, project.owner_id, details)


def _after(response):
    pending = g.pop('_studio_admin_audit', None)
    if pending is not None and response.status_code < 400:
        event, owner_id, details = pending
        from src.services import audit
        audit.record(event, target_user_id=owner_id, details=details)
    return response


def register(blueprint):
    from src.services import audit
    audit.EVENTS.update(EVENTS)
    blueprint.before_request(_before)
    blueprint.after_request(_after)
