"""The studio add-on's page, header link, health route and strings.

Isolated database via the repo-root conftest; login by session injection, as
upstream's tests do. Hermetic and offline.
"""

import json
import os
import uuid

import pytest

from src.app import app, db
from src.models import User
from src.studio import i18n as studio_i18n
from src.studio.routes import DEFAULT_SOURCE_URL

app.config['WTF_CSRF_ENABLED'] = False
app.config['TESTING'] = True

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def user():
    with app.app_context():
        suffix = uuid.uuid4().hex[:8]
        u = User(username=f'studio_{suffix}', email=f'{suffix}@example.com', password='x')
        db.session.add(u)
        db.session.commit()
        user_id = u.id
    yield user_id
    with app.app_context():
        db.session.delete(db.session.get(User, user_id))
        db.session.commit()


def _client(user_id=None):
    client = app.test_client()
    if user_id is not None:
        with client.session_transaction() as s:
            s['_user_id'] = str(user_id)
            s['_fresh'] = True
    return client


def test_signed_out_visitors_are_sent_to_the_login_page():
    response = _client().get('/studio')
    assert response.status_code in (301, 302)
    assert '/login' in response.headers['Location']


def test_the_projects_page_shows_the_empty_state_and_the_source_link(user):
    response = _client(user).get('/studio')
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'data-studio-projects' in page
    assert 'data-studio-empty' in page
    assert "st('studio.projects.empty')" in page
    # The embedded strings and the AGPL source link.
    assert '"No projects yet."' in page
    assert json.dumps(DEFAULT_SOURCE_URL) in page
    # The shared header, labelled with the page's title.
    assert 'id="global-header"' in page
    assert '<title>Projects - Speakr</title>' in page


def test_the_source_link_can_point_elsewhere(user, monkeypatch):
    monkeypatch.setenv('STUDIO_SOURCE_URL', 'https://example.com/source.git')
    page = _client(user).get('/studio').get_data(as_text=True)
    assert '"https://example.com/source.git"' in page


def _projects_button(page):
    import re
    found = re.findall(r'<a href="/studio" data-studio-nav[^>]*>.*?</a>', page, flags=re.S)
    assert len(found) == 1, found
    return found[0]


def test_the_header_bar_links_to_the_projects_page(user):
    page = _client(user).get('/').get_data(as_text=True)
    button = _projects_button(page)
    # In the bar beside New Recording, not in the user menu.
    assert page.index('data-studio-nav') < page.index('switchToUploadView') < page.index('data-user-menu-dropdown')
    assert 'header-action' in button and 'aria-label="Projects"' in button
    # The label from 640px, the film icon alone below, as New Recording does.
    assert '<span class="header-action__label hidden sm:inline">Projects</span>' in button
    assert 'aria-current' not in button


def test_the_main_page_loads_the_studio_script_after_upstreams(user):
    page = _client(user).get('/').get_data(as_text=True)
    hook = page.index('js/studio/main-page.js')
    assert page.count('data-studio-main-page') == 1
    # After the app's own script and outside the Vue root, so it runs; before </body>.
    assert page.index('js/app.modular.js') < hook < page.index('</body>')
    assert '<script type="module" src="/static/js/studio/main-page.js?v=' in page
    # Only the strings the dialog needs, not all the studio's.
    strings = json.loads(page.split('window.__STUDIO_I18N = ', 1)[1].split(';\n', 1)[0])
    assert set(strings['studio']) == {'shareHelp'}
    assert strings['studio']['shareHelp']['title'] == 'What to share'


def test_the_main_page_hook_is_one_marked_line():
    with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
        lines = [line for line in fh if 'studio' in line]
    assert len(lines) == 1
    assert "{% include 'studio/main_page.html' %}" in lines[0] and 'studio add-on (this fork only' in lines[0]


def test_the_studio_pages_do_not_load_the_main_page_script(user):
    assert 'js/studio/main-page.js' not in _client(user).get('/studio').get_data(as_text=True)


@pytest.mark.parametrize('path', ['/account', '/studio'])
def test_every_page_header_has_the_projects_button(user, path):
    button = _projects_button(_client(user).get(path).get_data(as_text=True))
    assert ('aria-current="page"' in button) is (path == '/studio')


def test_the_health_route_answers_without_signing_in():
    response = _client().get('/api/studio/health')
    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}


def test_missing_strings_fall_back_to_english_then_to_the_key():
    assert studio_i18n.studio_t('studio.projects.title', 'de') == 'Projects'
    assert studio_i18n.studio_t('studio.no.such.key', 'en') == 'studio.no.such.key'


def test_locale_names_that_are_not_locales_load_nothing():
    assert studio_i18n._load('../locales/en') == {}
    assert studio_i18n._load('') == {}


def test_every_studio_locale_file_is_valid_json_with_a_studio_root():
    folder = os.path.join(ROOT, 'static', 'locales-studio')
    files = [f for f in os.listdir(folder) if f.endswith('.json')]
    assert 'en.json' in files
    for name in files:
        with open(os.path.join(folder, name), encoding='utf-8') as fh:
            table = json.load(fh)
        assert set(table) == {'studio'}, name
