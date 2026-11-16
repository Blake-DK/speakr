"""Where a signed-in person lands: the Projects page for the bare address
reached from outside the app, Speakr's recordings page everywhere else.

The rule is in src/studio/landing.py. What these tests pin down:

- right after signing in (the password form or the single sign-on callback),
  the bare address answers with the Projects page;
- with STUDIO_LANDING_FROM_OUTSIDE=true, so does the bare address typed,
  opened from a bookmark, or reached from another site (Fetch Metadata:
  Sec-Fetch-Site none, cross-site, same-site); without it (the default) those
  show the recordings page, as Speakr's own browser tests expect;
- every other request is left alone: a link inside the app, a reload, any
  query or path, a `next=` after signing in, a request that is not a page
  navigation, a browser that sends no Fetch Metadata, a signed-out visitor;
- nobody can be caught in a loop, and the recordings page stays reachable.
"""

import time
import uuid

import pytest
from flask_login import login_user

from src.app import app, bcrypt, db
from src.models import User
from src.studio import landing
from tests.studio.support import world  # noqa: F401

PASSWORD = 'Landing-Passw0rd!'

# What a browser sends for a top-level page navigation.
TYPED = {'Sec-Fetch-Site': 'none', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document',
         'Sec-Fetch-User': '?1', 'Accept': 'text/html,application/xhtml+xml'}
IN_APP = dict(TYPED, **{'Sec-Fetch-Site': 'same-origin'})
FROM_OTHER_SITE = dict(TYPED, **{'Sec-Fetch-Site': 'cross-site'})
FROM_SAME_SITE = dict(TYPED, **{'Sec-Fetch-Site': 'same-site'})
RELOAD = dict(TYPED, **{'Cache-Control': 'max-age=0'})
HARD_RELOAD = dict(TYPED, **{'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
FETCH = {'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty',
         'Accept': 'application/json'}
IFRAME = dict(TYPED, **{'Sec-Fetch-Dest': 'iframe'})
OLD_BROWSER_PAGE = {'Accept': 'text/html,application/xhtml+xml'}


def _lands_on_projects(response):
    return response.status_code == 302 and response.headers['Location'].endswith('/studio')


@pytest.fixture
def from_outside_on(monkeypatch):
    monkeypatch.setenv(landing.FROM_OUTSIDE_SETTING, 'true')


@pytest.fixture(autouse=True)
def from_outside_unset(monkeypatch):
    # Every test starts from the default, whatever the environment says.
    monkeypatch.delenv(landing.FROM_OUTSIDE_SETTING, raising=False)


@pytest.fixture
def password_user(world):
    suffix = uuid.uuid4().hex[:8]
    user = User(username=f'land_{suffix}', email=f'land_{suffix}@example.com',
                password=bcrypt.generate_password_hash(PASSWORD).decode())
    db.session.add(user)
    db.session.commit()
    world.users.append(user.id)
    return user


def _sign_in(client, user, next_url=None):
    url = '/login' + (f'?next={next_url}' if next_url else '')
    response = client.post(url, data={'email': user.email, 'password': PASSWORD}, headers=IN_APP)
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    return response


# --- the bare address from outside the app ----------------------------------

@pytest.mark.parametrize('headers', [TYPED, FROM_OTHER_SITE, FROM_SAME_SITE], ids=['typed', 'cross-site', 'same-site'])
def test_by_default_the_bare_address_from_outside_shows_the_recordings_page(world, headers):
    client = world.client(world.user())
    assert client.get('/', headers=headers).status_code == 200


@pytest.mark.parametrize('value', ['', 'false', '0', 'no'])
def test_only_a_true_value_switches_the_second_rule_on(world, monkeypatch, value):
    monkeypatch.setenv(landing.FROM_OUTSIDE_SETTING, value)
    assert world.client(world.user()).get('/', headers=TYPED).status_code == 200


@pytest.mark.parametrize('headers', [TYPED, FROM_OTHER_SITE, FROM_SAME_SITE], ids=['typed', 'cross-site', 'same-site'])
def test_switched_on_the_bare_address_from_outside_lands_on_the_projects_page(world, from_outside_on, headers):
    client = world.client(world.user())
    response = client.get('/', headers=headers)
    assert _lands_on_projects(response)
    assert 'no-store' in response.headers.get('Cache-Control', '')


def test_the_projects_page_itself_is_never_redirected(world, from_outside_on):
    client = world.client(world.user())
    for headers in (TYPED, FROM_OTHER_SITE, IN_APP):
        assert client.get('/studio', headers=headers).status_code == 200


@pytest.mark.parametrize('headers', [IN_APP, RELOAD, HARD_RELOAD, FETCH, IFRAME, OLD_BROWSER_PAGE, {}],
                         ids=['in-app link', 'reload', 'hard reload', 'fetch', 'iframe', 'no fetch metadata',
                              'no headers'])
def test_everything_else_at_the_bare_address_shows_the_recordings_page(world, from_outside_on, headers):
    client = world.client(world.user())
    response = client.get('/', headers=headers)
    assert response.status_code == 200, (response.status_code, response.headers.get('Location'))


@pytest.mark.parametrize('path', ['/?q=interview', '/?view=list', '/recordings/1', '/label/interviews', '/account'])
def test_a_specific_address_goes_where_it_points(world, from_outside_on, path):
    client = world.client(world.user())
    response = client.get(path, headers=TYPED)
    assert not _lands_on_projects(response), (path, response.headers.get('Location'))


def test_a_head_request_is_left_alone(world, from_outside_on):
    client = world.client(world.user())
    assert not _lands_on_projects(client.head('/', headers=TYPED))


def test_a_signed_out_visitor_is_sent_to_sign_in_as_before(world, from_outside_on):
    response = world.client().get('/', headers=TYPED)
    assert response.status_code == 302
    location = response.headers['Location']
    assert '/login' in location and '/studio' not in location


def test_no_loop_from_the_bare_address_to_the_recordings_page(world, from_outside_on):
    client = world.client(world.user())
    first = client.get('/', headers=TYPED)
    assert _lands_on_projects(first)
    assert client.get('/studio', headers=TYPED).status_code == 200
    # The "All recordings" link on the Projects page and Speakr's own logo link.
    assert client.get('/', headers=IN_APP).status_code == 200
    assert client.get('/', headers=RELOAD).status_code == 200


def test_the_projects_page_links_to_all_recordings(world):
    page = world.client(world.user()).get('/studio').get_data(as_text=True)
    assert 'data-studio-all-recordings' in page
    assert 'href="/"' in page


# --- right after signing in -------------------------------------------------

def test_after_the_password_form_the_bare_address_lands_on_the_projects_page(password_user):
    client = app.test_client()
    signed_in = _sign_in(client, password_user)
    assert signed_in.headers['Location'].endswith('/')  # Speakr's own answer, unchanged
    # The redirect from a same-origin form post is a same-origin navigation:
    # only the sign-in note can tell it from a link inside the app.
    assert _lands_on_projects(client.get('/', headers=IN_APP))
    # The note is used once: the next visit is a visit like any other.
    assert client.get('/', headers=IN_APP).status_code == 200


def test_after_signing_in_with_next_the_bare_address_still_lands_on_projects(password_user):
    client = app.test_client()
    signed_in = _sign_in(client, password_user, next_url='/')
    assert signed_in.headers['Location'].endswith('/')
    assert _lands_on_projects(client.get('/', headers=IN_APP))


def test_after_signing_in_with_a_specific_next_it_goes_there_and_the_note_is_spent(password_user):
    client = app.test_client()
    signed_in = _sign_in(client, password_user, next_url='/account')
    assert signed_in.headers['Location'].endswith('/account')
    assert client.get('/account', headers=IN_APP).status_code == 200
    # Clicking the logo a moment later shows the recordings page, as before.
    assert client.get('/', headers=IN_APP).status_code == 200


def test_after_signing_in_an_old_browser_also_lands_on_projects(password_user):
    client = app.test_client()
    _sign_in(client, password_user)
    assert _lands_on_projects(client.get('/', headers=OLD_BROWSER_PAGE))


def test_the_sign_in_note_ignores_requests_that_are_not_page_navigations(password_user):
    client = app.test_client()
    _sign_in(client, password_user)
    assert client.get('/api/studio/health', headers=FETCH).status_code == 200
    assert client.get('/', headers=FETCH).status_code == 200
    # ... and is still there for the page itself.
    assert _lands_on_projects(client.get('/', headers=IN_APP))


def test_a_stale_sign_in_note_is_ignored(password_user):
    client = app.test_client()
    _sign_in(client, password_user)
    with client.session_transaction() as session:
        session[landing.SIGNED_IN_KEY] = time.time() - landing.SIGNED_IN_WINDOW_SECONDS - 5
    assert client.get('/', headers=IN_APP).status_code == 200


def test_a_wrong_password_leaves_no_note(password_user):
    client = app.test_client()
    response = client.post('/login', data={'email': password_user.email, 'password': 'not-it'}, headers=IN_APP)
    assert response.status_code == 200
    with client.session_transaction() as session:
        assert landing.SIGNED_IN_KEY not in session


@pytest.mark.parametrize('path,method,noted', [
    ('/login', 'POST', True),
    ('/auth/sso/callback', 'GET', True),
    ('/change_password', 'POST', False),   # signs the account in again after a password change
    ('/api/studio/health', 'GET', False),
])
def test_only_the_sign_in_routes_leave_the_note(password_user, path, method, noted):
    with app.test_request_context(path, method=method):
        login_user(password_user)
        from flask import session
        assert (landing.SIGNED_IN_KEY in session) is noted, path


def test_the_sign_in_routes_the_rule_listens_to_exist():
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert set(landing.SIGN_IN_ENDPOINTS) <= endpoints
    assert 'recordings.index' in endpoints
