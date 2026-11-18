"""The version the pages put on their static files.

Upstream gives the templates ``app_version`` (``_app_version`` in src/app.py,
the VERSION file): the ``?v=`` of every script and stylesheet, the address of
the service worker (``/sw.js?v=``) and the version of the locale files
(``window.__I18N_ASSET_VERSION``). Upstream's service worker (static/sw.js)
answers a static file whose ``v`` is its own from the browser's Cache Storage
first, and fetches it again only when the version changes.

The fork keeps VERSION at the upstream release it is built on, so every
studio image would carry the same value, and a browser would keep running the
scripts of the first studio release it saw against the pages of a later one.
Here the value is upstream's version followed by a short hash of every file
under static/ as it is in the running image (the stylesheet compiled at build
time included). It changes with every image whose static files differ, and it
is the same in every worker process and after every restart of one image.

Upstream's version itself is unchanged everywhere else: What's new, the About
page and the API's version fields still read ``get_version()``.
"""

import hashlib
import logging
import os

logger = logging.getLogger(__name__)

# Hex digits of the hash in the version: 48 bits, far more than the number of
# images that will ever be built.
DIGEST_LENGTH = 12


def static_digest(static_dir):
    """A SHA-256 over every file under ``static_dir``: its path relative to
    ``static_dir``, its size and its content, in path order. Directories that
    are symbolic links are not followed."""
    paths = []
    for dirpath, _dirnames, filenames in os.walk(static_dir):
        for name in filenames:
            full = os.path.join(dirpath, name)
            paths.append((os.path.relpath(full, static_dir).replace(os.sep, '/'), full))
    paths.sort()
    digest = hashlib.sha256()
    for rel, full in paths:
        with open(full, 'rb') as handle:
            content = handle.read()
        digest.update(f'{rel}\0{len(content)}\0'.encode('utf-8'))
        digest.update(content)
    return digest.hexdigest()


def asset_version(static_dir, base_version):
    """``base_version`` followed by the hash of ``static_dir``, for example
    ``v0.11.0-1a2b3c4d5e6f``. When a file cannot be read, ``base_version``
    alone (upstream's value), with a warning."""
    try:
        version = f'{base_version}-{static_digest(static_dir)[:DIGEST_LENGTH]}'
    except OSError as error:
        logger.warning('Static asset version: %s could not be read (%s); %r is used', static_dir, error, base_version)
        return base_version
    logger.info('Static asset version: %s', version)
    return version
