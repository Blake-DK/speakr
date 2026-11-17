"""Routes of a project's saved documents, their downloads, and generated images.

Every route that names a project or a document finds it through
``access.project_for``; a person who may not see the project gets 404 for the
project and for every document in it. The rules are in
src/studio/documents.py, src/studio/export.py and src/studio/images.py.
"""

from flask import jsonify, request
from flask_login import current_user, login_required

from src.database import db
from src.studio import access, documents, export, images, materials
from src.studio.routes import _error, _not_found, studio_bp


def _refused(exc):
    db.session.rollback()
    return _error(exc.key, exc.status, **exc.params)


# --- documents --------------------------------------------------------------

@studio_bp.route('/api/studio/projects/<int:project_id>/documents', methods=['GET'])
@login_required
def list_project_documents(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    return jsonify({'items': documents.list_documents(project, role, current_user)})


@studio_bp.route('/api/studio/projects/<int:project_id>/documents', methods=['POST'])
@login_required
def create_project_document(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['add_document']:
        return _error('studio.errors.ownerOnly', 403)
    try:
        document = documents.create(project, current_user, request.get_json(silent=True))
    except documents.DocumentError as exc:
        return _refused(exc)
    return jsonify(documents.to_dict(document, project, role, current_user, with_body=True)), 201


@studio_bp.route('/api/studio/documents/<int:document_id>', methods=['GET'])
@login_required
def get_document(document_id):
    document, project, role = documents.document_for(document_id, current_user)
    if document is None:
        return _not_found()
    return jsonify(documents.to_dict(document, project, role, current_user, with_body=True))


@studio_bp.route('/api/studio/documents/<int:document_id>', methods=['PUT'])
@login_required
def update_document(document_id):
    document, project, role = documents.document_for(document_id, current_user)
    if document is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['add_document']:
        return _error('studio.errors.ownerOnly', 403)
    try:
        documents.update(document, current_user, request.get_json(silent=True))
    except documents.DocumentError as exc:
        return _refused(exc)
    return jsonify(documents.to_dict(document, project, role, current_user, with_body=True))


@studio_bp.route('/api/studio/documents/<int:document_id>', methods=['DELETE'])
@login_required
def delete_document(document_id):
    document, project, role = documents.document_for(document_id, current_user)
    if document is None:
        return _not_found()
    if not documents.may_delete(document, project, role, current_user):
        return _error('studio.documents.errors.makerOrOwner', 403)
    documents.delete(document)
    return jsonify({'success': True})


@studio_bp.route('/api/studio/documents/<int:document_id>/download', methods=['GET'])
@login_required
def download_document(document_id):
    document, project, role = documents.document_for(document_id, current_user)
    if document is None:
        return _not_found()
    fmt = request.args.get('format', 'md')
    if fmt not in export.FORMATS:
        return _error('studio.documents.errors.format', 400)
    try:
        response = export.download(document, fmt)
    except ImportError:
        # The PDF export needs WeasyPrint, which the studio image installs.
        return _error('studio.documents.errors.pdfUnavailable', 503)
    db.session.commit()  # end the read before the file goes out
    return response


# --- generated images -------------------------------------------------------

@studio_bp.route('/api/studio/projects/<int:project_id>/images', methods=['POST'])
@login_required
def generate_project_image(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    if not access.capabilities(project, role, current_user)['add_material']:
        return _error('studio.errors.ownerOnly', 403)
    data = request.get_json(silent=True)
    # The request to the image API takes up to a minute: no transaction is held meanwhile.
    db.session.commit()
    try:
        material, usage = images.create(project, current_user, data)
        db.session.flush()
    except images.ImageError as exc:
        return _refused(exc)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        materials.delete_stored_file(material)
        raise
    # The token counts OpenAI reported (what the image cost), with the material.
    return jsonify(dict(materials.to_dict(material, True), usage=usage)), 201
