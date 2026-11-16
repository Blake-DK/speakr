"""Where a signed-in person lands: the Projects page, when they arrive at the
bare address from outside the app.

"From outside the app" means:

- the first page right after signing in (the password form or the single
  sign-on callback), noted in the session by Flask-Login's ``user_logged_in``
  signal and used once, within a short time. Always on.
- only with ``STUDIO_LANDING_FROM_OUTSIDE=true``: also a page navigation the
  browser marks as not started by this site (Fetch Metadata
  ``Sec-Fetch-Site``: ``none`` for a typed address, a bookmark or the
  installed app's start address; ``cross-site`` or ``same-site`` for a link
  from another site), that is not a reload. Off by default: a browser cannot
  tell a person's bookmark from a script opening the address, and Speakr's
  own browser tests open ``/`` that way and expect the recordings page.

Everything else is left to Speakr, so its recordings page shows as before:
a link or redirect inside the app (``same-origin``), a reload, any path other
than ``/``, any query string, a request that is not a top-level page
navigation (fetch, iframe, the service worker's own requests), a HEAD
request, a browser that sends no Fetch Metadata (except right after signing
in), and a signed-out visitor (Speakr sends them to sign in with ``next=/``,
and signing in brings them back here). The Projects page is never redirected,
so there is no loop, and every link to ``/`` inside the app reaches the
recordings page.

Nothing here edits an upstream file: a request hook and a signal handler,
both registered by the studio blueprint.
"""

import os
import time

from flask import redirect, request, session, url_for
from flask_login import current_user, user_logged_in

# The session key of the sign-in note, and how long the note is good for.
SIGNED_IN_KEY = 'studio_signed_in_at'
SIGNED_IN_WINDOW_SECONDS = 120

# Speakr's routes that sign a person in: the password form (also the
# administrators' form when password sign-in is off) and the single sign-on
# callback. Other calls of login_user (signing in again after a password
# change) are not a first sign-in. tests/studio/test_studio_landing.py fails
# if an upstream release renames one of these.
SIGN_IN_ENDPOINTS = ('auth.login', 'auth.sso_callback')

# Sec-Fetch-Site values of a navigation that did not start on this site.
_FROM_OUTSIDE = frozenset({'none', 'cross-site', 'same-site'})

# The switch for the second rule (a typed address, a bookmark, a link from
# another site). Read on every request, so a test or a restart changes it.
FROM_OUTSIDE_SETTING = 'STUDIO_LANDING_FROM_OUTSIDE'


def from_outside_enabled():
    return os.environ.get(FROM_OUTSIDE_SETTING, '').strip().lower() in ('1', 'true', 'yes', 'on')


def _note_sign_in(sender, user=None, **extra):
    """user_logged_in: note the time, when the sign-in came through a sign-in route."""
    try:
        if request.endpoint in SIGN_IN_ENDPOINTS:
            session[SIGNED_IN_KEY] = time.time()
    except RuntimeError:  # outside a request
        pass


def _is_page_navigation():
    """A top-level page load (not a fetch, an iframe or a worker's request)."""
    mode = request.headers.get('Sec-Fetch-Mode')
    dest = request.headers.get('Sec-Fetch-Dest')
    if mode or dest:
        return mode == 'navigate' and dest == 'document'
    # A browser without Fetch Metadata: a GET that asks for a page.
    return 'text/html' in request.headers.get('Accept', '')


def _is_reload():
    cache_control = request.headers.get('Cache-Control', '').lower()
    return ('max-age=0' in cache_control or 'no-cache' in cache_control
            or request.headers.get('Pragma', '').lower() == 'no-cache')


def _take_sign_in_note():
    """True when the session holds a fresh sign-in note; the note is removed either way."""
    if SIGNED_IN_KEY not in session:
        return False
    noted = session.pop(SIGNED_IN_KEY, None)
    if isinstance(noted, bool) or not isinstance(noted, (int, float)):
        return False
    return 0 <= time.time() - noted <= SIGNED_IN_WINDOW_SECONDS


def landing_redirect():
    """before_app_request: the Projects page for the bare address from outside, else None."""
    if request.method != 'GET' or not _is_page_navigation():
        return None
    # The first page navigation after signing in uses the note up, wherever it
    # goes: a sign-in with next=/account must not send a later click on the
    # logo to the Projects page.
    just_signed_in = _take_sign_in_note()
    if request.path != '/' or request.query_string:
        return None
    if not current_user.is_authenticated:
        return None
    if not just_signed_in:
        if not from_outside_enabled():
            return None
        if request.headers.get('Sec-Fetch-Site') not in _FROM_OUTSIDE or _is_reload():
            return None
    response = redirect(url_for('studio.projects_page'))
    # The same address answers differently by where the visit came from.
    response.headers['Cache-Control'] = 'no-store'
    return response


def register(blueprint):
    """Attach the request hook to the blueprint and the signal handler to Flask-Login."""
    blueprint.before_app_request(landing_redirect)
    user_logged_in.connect(_note_sign_in)
