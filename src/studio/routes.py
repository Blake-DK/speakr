"""Routes of the studio add-on: the pages and the JSON API behind them.

Every route that names a project, a material or a voice reference finds it
through ``access.project_for``; a person who may not see the project gets
404 for it and for everything in it. The routes stay thin: the rules are in
access.py, sharing.py, projects.py, materials.py and voice.py.
"""

import os

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from src.database import db
from src.studio import access, admin_access, landing, materials, projects, sharing, voice
from src.studio.i18n import studio_message, studio_strings, studio_strings_for, studio_t, user_language

studio_bp = Blueprint('studio', __name__)

# Where the people who use this site can get its source code, as the AGPL asks
# of a modified version that is offered over a network. STUDIO_SOURCE_URL
# overrides it for a copy published somewhere else.
DEFAULT_SOURCE_URL = 'https://github.com/Blake-DK/speakr'


def source_url():
    return os.environ.get('STUDIO_SOURCE_URL', '').strip() or DEFAULT_SOURCE_URL


def _apply_schema_steps(state):
    """The studio's own schema steps, when src/app.py registers the blueprint.

    That is right after upstream's start-up migrations (initialize_database),
    in every process that imports the app, as upstream's migrations run.
    """
    from src.studio.schema import apply_schema_steps
    with state.app.app_context():
        try:
            apply_schema_steps()
        except Exception:
            state.app.logger.exception("Studio schema steps could not run")
        # STUDIO_ADMINS_SEE_RECORDINGS: bring every project in line, or take
        # back what an earlier "on" made (src/studio/admin_access.py).
        admin_access.startup(state.app.logger)


studio_bp.record_once(_apply_schema_steps)

# The bare address reached from outside the app opens the Projects page
# (src/studio/landing.py).
landing.register(studio_bp)

# Site admins' memberships follow their admin status, before and after requests
# (src/studio/admin_access.py; does nothing while the setting is off).
admin_access.register(studio_bp)


@studio_bp.app_context_processor
def inject_studio_helpers():
    """``studio_t`` for every template, so shared parts such as the header can label studio links;
    ``studio_strings_for`` for templates/studio/main_page.html on Speakr's main page."""
    return {'studio_t': studio_t, 'studio_strings_for': studio_strings_for}


def _error(key, status, **params):
    return jsonify({'error': studio_message(key, **params), 'code': key.rsplit('.', 1)[-1]}), status


def _not_found():
    return _error('studio.errors.notFound', 404)


def _page(template, title, **extra):
    language = user_language()
    return render_template(
        template,
        user_language=language,
        page_title=title,
        studio_strings=studio_strings(language),
        studio_source_url=source_url(),
        **extra,
    )


# --- pages ------------------------------------------------------------------

@studio_bp.route('/studio', methods=['GET'])
@login_required
def projects_page():
    return _page('studio/projects.html', studio_t('studio.projects.title'))


@studio_bp.route('/studio/projects/<int:project_id>', methods=['GET'])
@login_required
def project_page(project_id):
    project, _role = access.project_for(project_id, current_user)
    if project is None:
        abort(404)
    projects.record_visit(project, current_user)
    return _page('studio/project.html', project.name, studio_project_id=project.id)


@studio_bp.route('/api/studio/health', methods=['GET'])
def health():
    """Liveness of the add-on, for monitoring. Says nothing about the user or the data."""
    return jsonify({'status': 'ok'})


# --- projects ---------------------------------------------------------------

@studio_bp.route('/api/studio/projects', methods=['GET'])
@login_required
def list_projects():
    """The projects the person may see, the most recently opened first."""
    return jsonify({'projects': [projects.summary(p, current_user, opened_at)
                                 for p, opened_at in projects.ordered_for(current_user)]})


@studio_bp.route('/api/studio/switcher', methods=['GET'])
@login_required
def project_switcher():
    """The project switcher's list: the same projects in the same order, without the counts."""
    return jsonify({'projects': [projects.switcher_item(p, current_user, opened_at)
                                 for p, opened_at in projects.ordered_for(current_user)]})


@studio_bp.route('/api/studio/projects', methods=['POST'])
@login_required
def create_project():
    try:
        project = projects.create(current_user, request.get_json(silent=True))
    except projects.ProjectError as exc:
        db.session.rollback()
        return _error(exc.key, exc.status, **exc.params)
    return jsonify(projects.detail(project, access.OWNER, current_user)), 201


@studio_bp.route('/api/studio/projects/<int:project_id>', methods=['GET'])
@login_required
def get_project(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    return jsonify(projects.detail(project, role, current_user))


@studio_bp.route('/api/studio/projects/<int:project_id>', methods=['PUT'])
@login_required
def update_project(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    caps = access.capabilities(project, role, current_user)
    try:
        projects.update(project, caps, request.get_json(silent=True))
    except (projects.ProjectError, sharing.SharingError) as exc:
        db.session.rollback()
        return _error(exc.key, exc.status, **exc.params)
    return jsonify(projects.detail(project, role, current_user))


@studio_bp.route('/api/studio/projects/<int:project_id>', methods=['DELETE'])
@login_required
def delete_project(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['delete']:
        return _error('studio.errors.ownerOnly', 403)
    try:
        sharing.delete_project(project)
        db.session.commit()
    except sharing.SharingError as exc:
        db.session.rollback()
        return _error(exc.key, exc.status, **exc.params)
    return jsonify({'success': True})


@studio_bp.route('/api/studio/projects/<int:project_id>/recordings', methods=['GET'])
@login_required
def project_recordings(project_id):
    project, _role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    visible, hidden = projects.split_recordings(project, current_user)
    return jsonify({'items': projects.recording_list(project, current_user, visible),
                    'hidden': projects.hidden_summary(hidden)})


@studio_bp.route('/api/studio/projects/<int:project_id>/recording-folder', methods=['POST'])
@login_required
def recording_folder(project_id):
    """The folder an upload from the project page goes into, made again if it was deleted."""
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['add_recording']:
        return _error('studio.errors.membersOnly', 403)
    try:
        folder = sharing.ensure_folder(project)
        db.session.commit()
    except sharing.SharingError as exc:
        db.session.rollback()
        return _error(exc.key, exc.status, **exc.params)
    return jsonify({'folder_id': folder.id})


@studio_bp.route('/api/studio/projects/<int:project_id>/people', methods=['PUT'])
@login_required
def set_project_people(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['share']:
        return _error('studio.errors.ownerOnly', 403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    try:
        wanted = sharing.resolve_people(current_user, project, data.get('user_ids', []),
                                        data.get('identifiers', []))
        sharing.set_people(project, wanted)
        db.session.commit()
    except sharing.SharingError as exc:
        db.session.rollback()
        return _error(exc.key, exc.status, **exc.params)
    return jsonify(projects.detail(project, role, current_user))


# --- materials --------------------------------------------------------------

def _material_for(material_id):
    from src.studio.models import StudioMaterial
    material = db.session.get(StudioMaterial, material_id)
    if material is None:
        return None, None, None
    project, role = access.project_for(material.project_id, current_user)
    if project is None:
        return None, None, None
    return material, project, role


@studio_bp.route('/api/studio/projects/<int:project_id>/materials', methods=['GET'])
@login_required
def list_materials(project_id):
    from src.studio.models import StudioMaterial
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    rows = StudioMaterial.query.filter_by(project_id=project.id).order_by(
        StudioMaterial.created_at.desc(), StudioMaterial.id.desc()).all()
    return jsonify({'items': [
        materials.to_dict(m, access.may_delete_item(project, role, current_user, m.uploaded_by)) for m in rows]})


@studio_bp.route('/api/studio/projects/<int:project_id>/materials', methods=['POST'])
@login_required
def upload_materials(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['add_material']:
        return _error('studio.errors.ownerOnly', 403)
    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return _error('studio.materials.errors.none', 400)
    if len(files) > materials.MAX_FILES_PER_UPLOAD:
        return _error('studio.materials.errors.tooMany', 400, max=materials.MAX_FILES_PER_UPLOAD)
    stored, refused = [], []
    for upload in files:
        try:
            material = materials.store_upload(project, current_user, upload)
            db.session.flush()
            stored.append(material)
        except materials.MaterialError as exc:
            refused.append({'name': exc.params.get('name') or upload.filename or '',
                            'error': studio_message(exc.key, **exc.params), 'code': exc.key.rsplit('.', 1)[-1]})
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for material in stored:
            materials.delete_stored_file(material)
        raise
    status = 201 if stored else 400
    return jsonify({'stored': [materials.to_dict(m, True) for m in stored], 'refused': refused}), status


@studio_bp.route('/api/studio/materials/<int:material_id>', methods=['GET'])
@login_required
def get_material(material_id):
    material, project, role = _material_for(material_id)
    if material is None:
        return _not_found()
    return jsonify(materials.to_dict(material, access.may_delete_item(project, role, current_user,
                                                                      material.uploaded_by), with_text=True))


@studio_bp.route('/api/studio/materials/<int:material_id>/file', methods=['GET'])
@login_required
def material_file(material_id):
    material, _project, _role = _material_for(material_id)
    if material is None:
        return _not_found()
    response = materials.send(material, download=request.args.get('download') == '1')
    if response is None:
        return _error('studio.materials.errors.missing', 404)
    return response


@studio_bp.route('/api/studio/materials/<int:material_id>', methods=['DELETE'])
@login_required
def delete_material(material_id):
    material, project, role = _material_for(material_id)
    if material is None:
        return _not_found()
    if not access.may_delete_item(project, role, current_user, material.uploaded_by):
        return _error('studio.errors.uploaderOrOwner', 403)
    materials.delete_stored_file(material)
    db.session.delete(material)
    db.session.commit()
    return jsonify({'success': True})


# --- voice references -------------------------------------------------------

def _voice_for(reference_id):
    from src.studio.models import StudioVoiceReference
    reference = db.session.get(StudioVoiceReference, reference_id)
    if reference is None:
        return None, None, None
    project, role = access.project_for(reference.project_id, current_user)
    if project is None:
        return None, None, None
    return reference, project, role


@studio_bp.route('/api/studio/projects/<int:project_id>/voice', methods=['GET'])
@login_required
def list_voice(project_id):
    from src.studio.models import StudioVoiceReference
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    rows = StudioVoiceReference.query.filter_by(project_id=project.id).order_by(
        StudioVoiceReference.created_at.desc(), StudioVoiceReference.id.desc()).all()
    titles = projects.recording_titles([r.recording_id for r in rows], current_user)
    return jsonify({'items': [
        voice.to_dict(r, titles.get(r.recording_id), access.may_delete_item(project, role, current_user,
                                                                          r.created_by))
        for r in rows]})


@studio_bp.route('/api/studio/projects/<int:project_id>/voice', methods=['POST'])
@login_required
def add_voice(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['add_voice']:
        return _error('studio.errors.ownerOnly', 403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    try:
        if data.get('recording_id') is not None:
            reference = voice.create_from_recording(project, current_user, data.get('recording_id'),
                                                    data.get('title'))
        else:
            reference = voice.create_from_text(project, current_user, data.get('text'), data.get('title'))
    except projects.ProjectError as exc:
        db.session.rollback()
        return _error(exc.key, exc.status, **exc.params)
    db.session.commit()
    titles = projects.recording_titles([reference.recording_id], current_user)
    return jsonify(voice.to_dict(reference, titles.get(reference.recording_id), True, with_text=True)), 201


@studio_bp.route('/api/studio/voice/<int:reference_id>', methods=['GET'])
@login_required
def get_voice(reference_id):
    reference, project, role = _voice_for(reference_id)
    if reference is None:
        return _not_found()
    titles = projects.recording_titles([reference.recording_id], current_user)
    return jsonify(voice.to_dict(reference, titles.get(reference.recording_id),
                                 access.may_delete_item(project, role, current_user, reference.created_by),
                                 with_text=True))


@studio_bp.route('/api/studio/voice/<int:reference_id>', methods=['DELETE'])
@login_required
def delete_voice(reference_id):
    reference, project, role = _voice_for(reference_id)
    if reference is None:
        return _not_found()
    if not access.may_delete_item(project, role, current_user, reference.created_by):
        return _error('studio.errors.uploaderOrOwner', 403)
    db.session.delete(reference)
    db.session.commit()
    return jsonify({'success': True})


# The chats (src/studio/chat_routes.py) and the documents and generated images
# (src/studio/document_routes.py) are routes of this blueprint too.
from src.studio import chat_routes as _chat_routes  # noqa: E402,F401
from src.studio import document_routes as _document_routes  # noqa: E402,F401
