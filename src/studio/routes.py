"""Routes of the studio add-on."""

import os

from flask import Blueprint, jsonify, render_template
from flask_login import login_required

from src.studio.i18n import studio_strings, studio_t, user_language

studio_bp = Blueprint('studio', __name__)

# Where the people who use this site can get its source code, as the AGPL asks
# of a modified version that is offered over a network. STUDIO_SOURCE_URL
# overrides it for a copy published somewhere else.
DEFAULT_SOURCE_URL = 'https://github.com/Blake-DK/speakr'


def source_url():
    return os.environ.get('STUDIO_SOURCE_URL', '').strip() or DEFAULT_SOURCE_URL


@studio_bp.app_context_processor
def inject_studio_helpers():
    """``studio_t`` for every template, so shared parts such as the header can label studio links."""
    return {'studio_t': studio_t}


@studio_bp.route('/studio', methods=['GET'])
@login_required
def projects_page():
    language = user_language()
    return render_template(
        'studio/projects.html',
        user_language=language,
        page_title=studio_t('studio.projects.title', language),
        studio_strings=studio_strings(language),
        studio_source_url=source_url(),
    )


@studio_bp.route('/api/studio/health', methods=['GET'])
def health():
    """Liveness of the add-on, for monitoring. Says nothing about the user or the data."""
    return jsonify({'status': 'ok'})
