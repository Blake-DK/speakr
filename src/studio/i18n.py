"""Interface strings of the studio add-on.

The strings live in static/locales-studio/<locale>.json, apart from upstream's
static/locales/, so an upstream release never conflicts with them and
upstream's locale checks never see them. English is the fallback for every
missing key.

Templates call ``studio_t('studio.x.y')`` (server side) or ``st('studio.x.y')``
in a studio page's Vue app (static/js/studio/strings.js). The name ``st`` is
deliberate: upstream's tests read every ``t('...')`` call in templates/ and
static/js/ as a key of upstream's own locale files.
"""

import json
import os
import re
import threading

from flask_login import current_user

FALLBACK_LOCALE = 'en'
_LOCALE_RE = re.compile(r'^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$')
_LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'static', 'locales-studio')

_cache = {}
_lock = threading.Lock()


def _load(locale):
    """The parsed locale file, or {} when it is missing, invalid or not a locale name."""
    if not locale or not _LOCALE_RE.match(locale):
        return {}
    path = os.path.join(_LOCALES_DIR, f'{locale}.json')
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    cached = _cache.get(locale)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding='utf-8') as fh:
            table = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(table, dict):
        return {}
    with _lock:
        _cache[locale] = (mtime, table)
    return table


def _lookup(table, key):
    node = table
    for part in key.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def _merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def user_language():
    """The signed-in user's interface language, as upstream's pages pick it."""
    try:
        if current_user.is_authenticated and current_user.ui_language:
            return current_user.ui_language
    except Exception:  # outside a request, or no user loader yet
        pass
    return FALLBACK_LOCALE


def studio_t(key, language=None):
    """The string for ``key`` in ``language`` (default: the user's), English as fallback, else the key."""
    language = language or user_language()
    for locale in (language, FALLBACK_LOCALE):
        value = _lookup(_load(locale), key)
        if value is not None:
            return value
    return key


def studio_message(key, language=None, **params):
    """studio_t with ``{{name}}`` placeholders filled in, as window.studioT does in the browser."""
    text = studio_t(key, language)
    for name, value in params.items():
        text = text.replace('{{' + name + '}}', str(value))
    return text


# Sections used only on the server, never embedded in a page: "brand" holds the About tab's licence
# line, the one place where the name Speakr may appear (templates/studio/about_licence.html).
SERVER_ONLY = ('brand',)


def studio_strings(language=None):
    """All studio strings for ``language``, English filling the gaps; embedded in studio pages."""
    language = language or user_language()
    strings = _load(FALLBACK_LOCALE)
    if language != FALLBACK_LOCALE:
        strings = _merge(strings, _load(language))
    table = strings.get('studio')
    if isinstance(table, dict) and any(name in table for name in SERVER_ONLY):
        strings = {**strings, 'studio': {k: v for k, v in table.items() if k not in SERVER_ONLY}}
    return strings


def studio_strings_for(*sections, language=None):
    """Only the named sections of the studio strings, for an upstream page that needs a few of them."""
    table = studio_strings(language).get('studio', {})
    return {'studio': {name: table[name] for name in sections if name in table}}
