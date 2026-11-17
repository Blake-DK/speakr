"""Routes of the studio's chats and of its chat settings (administrators only).

Every route that names a chat or a project finds it through
``access.project_for``; a person who may not see the project gets 404 for
the project and for every chat and message in it. The rules are in
src/studio/chats.py, src/studio/attachments.py and src/studio/providers/.
"""

import re

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from src.database import db
from src.studio import access, attachments, chats, providers, workflows
from src.studio.i18n import studio_message, studio_t
from src.studio.routes import _error, _not_found, _page, studio_bp

MODEL_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$')


def _refused(exc):
    db.session.rollback()
    return _error(exc.key, exc.status, **exc.params)


def _chat_or_404(chat_id):
    chat, project, role = chats.chat_for(chat_id, current_user)
    return chat, project, role


# --- what the page needs ----------------------------------------------------

@studio_bp.route('/api/studio/chat-config', methods=['GET'])
@login_required
def chat_config():
    """The providers (no keys, only whether one is set) and the workflows (no prompt text)."""
    return jsonify({
        'providers': providers.describe(),
        'workflows': workflows.public_list(),
        'limits': {'max_attach_bytes': attachments.MAX_ATTACH_BYTES,
                   'max_attachments': attachments.MAX_ATTACHMENTS_PER_MESSAGE,
                   'max_message_chars': chats.MESSAGE_MAX},
        'is_admin': bool(getattr(current_user, 'is_admin', False)),
    })


@studio_bp.route('/api/studio/projects/<int:project_id>/attachables', methods=['GET'])
@login_required
def project_attachables(project_id):
    project, _role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    return jsonify(attachments.attachable(project, current_user))


# --- chats ------------------------------------------------------------------

@studio_bp.route('/api/studio/projects/<int:project_id>/chats', methods=['GET'])
@login_required
def list_project_chats(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    return jsonify({'items': chats.list_chats(project, role, current_user, request.args.get('q', ''))})


@studio_bp.route('/api/studio/projects/<int:project_id>/chats', methods=['POST'])
@login_required
def create_project_chat(project_id):
    project, role = access.project_for(project_id, current_user)
    if project is None:
        return _not_found()
    try:
        chat, opening, web_search = chats.create(project, current_user, request.get_json(silent=True))
    except chats.ChatError as exc:
        return _refused(exc)
    data = chats.to_dict(chat, role, current_user)
    data.update({'opening_message': opening, 'web_search': web_search})
    return jsonify(data), 201


@studio_bp.route('/api/studio/chats/<int:chat_id>', methods=['GET'])
@login_required
def get_chat(chat_id):
    chat, project, role = _chat_or_404(chat_id)
    if chat is None:
        return _not_found()
    return jsonify({'chat': chats.to_dict(chat, role, current_user),
                    'messages': chats.message_dicts(chat, current_user)})


@studio_bp.route('/api/studio/chats/<int:chat_id>', methods=['PUT'])
@login_required
def rename_chat(chat_id):
    chat, project, role = _chat_or_404(chat_id)
    if chat is None:
        return _not_found()
    if not chats.may_manage(chat, role, current_user):
        return _error('studio.chat.errors.makerOrOwner', 403)
    data = request.get_json(silent=True)
    try:
        chats.rename(chat, (data or {}).get('title') if isinstance(data, dict) else None)
    except chats.ChatError as exc:
        return _refused(exc)
    return jsonify(chats.to_dict(chat, role, current_user))


@studio_bp.route('/api/studio/chats/<int:chat_id>', methods=['DELETE'])
@login_required
def delete_chat(chat_id):
    chat, project, role = _chat_or_404(chat_id)
    if chat is None:
        return _not_found()
    if not chats.may_manage(chat, role, current_user):
        return _error('studio.chat.errors.makerOrOwner', 403)
    try:
        chats.delete(chat)
    except chats.ChatError as exc:
        return _refused(exc)
    return jsonify({'success': True})


@studio_bp.route('/api/studio/chats/<int:chat_id>/stream', methods=['POST'])
@login_required
def stream_chat_message(chat_id):
    chat, project, role = _chat_or_404(chat_id)
    if chat is None:
        return _not_found()
    try:
        return chats.start_send(chat, project, current_user, request.get_json(silent=True))
    except (chats.ChatError, attachments.AttachmentError) as exc:
        return _refused(exc)


@studio_bp.route('/api/studio/chats/<int:chat_id>/regenerate', methods=['POST'])
@login_required
def regenerate_chat_answer(chat_id):
    chat, project, role = _chat_or_404(chat_id)
    if chat is None:
        return _not_found()
    try:
        return chats.start_regenerate(chat, project, current_user, request.get_json(silent=True))
    except chats.ChatError as exc:
        return _refused(exc)


@studio_bp.route('/api/studio/messages/<int:message_id>', methods=['PUT'])
@login_required
def edit_chat_message(message_id):
    from src.studio.models import StudioMessage
    message = db.session.get(StudioMessage, message_id)
    if message is None:
        return _not_found()
    chat, project, role = _chat_or_404(message.chat_id)
    if chat is None:
        return _not_found()
    data = request.get_json(silent=True)
    try:
        chats.edit_last(message, chat, current_user, (data or {}).get('content') if isinstance(data, dict) else None)
    except chats.ChatError as exc:
        return _refused(exc)
    return jsonify({'chat': chats.to_dict(chat, role, current_user),
                    'messages': chats.message_dicts(chat, current_user)})


# --- chat settings (administrators) -----------------------------------------

def _admin_only():
    if not getattr(current_user, 'is_admin', False):
        abort(404)


@studio_bp.route('/studio/settings', methods=['GET'])
@login_required
def chat_settings_page():
    _admin_only()
    return _page('studio/settings.html', studio_t('studio.settings.title'))


@studio_bp.route('/api/studio/settings', methods=['GET'])
@login_required
def get_chat_settings():
    _admin_only()
    return jsonify({'providers': providers.describe(), 'key_env': providers.KEY_ENV,
                    'defaults': {name: providers.DEFAULT_MODELS[name] for name in providers.PROVIDERS}})


@studio_bp.route('/api/studio/settings', methods=['PUT'])
@login_required
def put_chat_settings():
    _admin_only()
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    for name in providers.PROVIDERS:
        model_key = f'{name}_model'
        if model_key in data:
            value = data[model_key]
            value = value.strip() if isinstance(value, str) else ''
            if not value:
                providers.clear_setting(model_key)
            elif not MODEL_ID_RE.match(value):
                db.session.rollback()
                return _error('studio.settings.errors.model', 400, provider=providers.LABELS[name])
            else:
                providers.set_setting(model_key, value)
        enabled_key = f'{name}_enabled'
        if enabled_key in data:
            providers.set_setting(enabled_key, '1' if data[enabled_key] else '0')
    db.session.commit()
    return jsonify({'providers': providers.describe(), 'key_env': providers.KEY_ENV,
                    'defaults': {name: providers.DEFAULT_MODELS[name] for name in providers.PROVIDERS}})


@studio_bp.route('/api/studio/settings/models', methods=['GET'])
@login_required
def list_provider_models():
    """Each provider's live model list (only for a provider whose key is set)."""
    _admin_only()
    result = {'errors': {}}
    for name in providers.PROVIDERS:
        result[name] = []
        if not providers.api_key(name):
            continue
        try:
            result[name] = providers.module(name).list_models()
        except Exception as e:  # shown to the administrator, never the key
            result['errors'][name] = studio_message('studio.settings.errors.listFailed',
                                                    error=type(e).__name__)
    return jsonify(result)


@studio_bp.route('/api/studio/settings/test', methods=['POST'])
@login_required
def test_provider():
    """One tiny live request with the given (or the stored) model."""
    _admin_only()
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    name = data.get('provider')
    if name not in providers.PROVIDERS:
        return _error('studio.chat.errors.unknownProvider', 400)
    if not providers.api_key(name):
        return jsonify({'ok': False, 'message': studio_message('studio.settings.noKey', env=providers.KEY_ENV[name])})
    model = data.get('model')
    model = model.strip() if isinstance(model, str) and model.strip() else providers.model(name)
    if not MODEL_ID_RE.match(model):
        return _error('studio.settings.errors.model', 400, provider=providers.LABELS[name])
    try:
        return jsonify({'ok': True, 'message': providers.module(name).test(model)})
    except Exception as e:
        message = getattr(e, 'message', None) or str(e)
        return jsonify({'ok': False, 'message': f'{type(e).__name__}: {str(message)[:300]}'})
