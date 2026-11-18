"""The app's name, the studio release that runs, and where its source code is.

The name a person reads is ScriptBot, not Speakr. Upstream's files keep their
text in this repository; src/studio/rebrand.py rewrites them when the image is
built (Dockerfile.studio), with the name from ``STUDIO_APP_NAME``. The image
carries the same value in its environment, so the studio's own pages and the
rewritten upstream pages agree. It is a build setting: a new name needs a new
image.

``STUDIO_RELEASE`` is the image's release without the "v" (for example
0.11.0-studio.16), set by .github/workflows/studio-publish.yml; outside an image
it is empty.
"""

import os
import re

DEFAULT_APP_NAME = 'ScriptBot'

# Where the people who use this site can get its source code, as the AGPL asks
# of a modified version that is offered over a network. STUDIO_SOURCE_URL
# overrides it for a copy published somewhere else.
DEFAULT_SOURCE_URL = 'https://github.com/Blake-DK/speakr'

# Letters, digits, spaces and . ' - only: the name is written into HTML, JSON and
# JavaScript strings as it is, so nothing in it may need escaping.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'-]{0,39}$")
_RELEASE_RE = re.compile(r'^\d+\.\d+\.\d+-studio\.\d+$')


def app_name():
    """The name a person reads; ``DEFAULT_APP_NAME`` when the setting is empty or not a plain name."""
    name = os.environ.get('STUDIO_APP_NAME', '').strip()
    return name if _NAME_RE.match(name) else DEFAULT_APP_NAME


def valid_name(name):
    return bool(_NAME_RE.match(name or ''))


def release():
    """The studio release that runs (for example 0.11.0-studio.16), or '' outside a published image."""
    return os.environ.get('STUDIO_RELEASE', '').strip()


def source_url():
    return os.environ.get('STUDIO_SOURCE_URL', '').strip() or DEFAULT_SOURCE_URL


def source_link():
    """The source code of this version: the release's tag in the fork's repository when both are known,
    else the repository (or STUDIO_SOURCE_URL as it is)."""
    url = source_url()
    tag = release()
    if url == DEFAULT_SOURCE_URL and _RELEASE_RE.match(tag):
        return f'{url}/tree/v{tag}'
    return url
