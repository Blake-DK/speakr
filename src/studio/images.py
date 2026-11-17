"""Images made for a project: poster concepts and social visuals, through OpenAI's image API.

Carried over from the standalone app the studio replaces: a prompt (the page
starts it with the project's name and logline), a shape (portrait poster,
square, wide banner) and a quality; the PNG lands in the project's
materials. Here it is stored through the same path and checks as an upload
(``materials.store_bytes``), named after the prompt, with the person as its
uploader, so it can be opened, attached to a chat and deleted like any other
material.

The key comes from the environment only (``STUDIO_OPENAI_API_KEY``, see
src/studio/providers/__init__.py) and image generation follows the GPT
provider's switch on the chat settings page. The model is the studio setting
``openai_image_model``, else ``STUDIO_OPENAI_IMAGE_MODEL``, else
``gpt-image-1``. A request can take a minute; gunicorn's worker timeout
(600 seconds in upstream's image) is well above the client's 300.
"""

import base64
import os
import re

from src.studio import providers

SIZES = ('1024x1536', '1024x1024', '1536x1024')
QUALITIES = ('low', 'medium', 'high')
DEFAULT_MODEL = 'gpt-image-1'
MODEL_ENV = 'STUDIO_OPENAI_IMAGE_MODEL'
MODEL_SETTING = 'openai_image_model'
PROMPT_MAX = 4000
TIMEOUT_SECONDS = 300
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'

_client_factory = None


class ImageError(Exception):
    """A refused or failed image; ``key`` names the studio string for it."""

    def __init__(self, key, status=400, **params):
        super().__init__(key)
        self.key = key
        self.status = status
        self.params = params


def model():
    """The image model: the administrator's choice, else the environment, else the default."""
    chosen = providers.setting(MODEL_SETTING)
    if chosen:
        return chosen
    return (os.environ.get(MODEL_ENV, '') or '').strip() or DEFAULT_MODEL


def available():
    return providers.available('openai')


def describe():
    """What the page needs: whether images can be made, and why not. No key."""
    return {
        'available': available(),
        'has_key': bool(providers.api_key('openai')),
        'enabled': providers.enabled('openai'),
        'model': model(),
        'key_env': providers.KEY_ENV['openai'],
        'sizes': list(SIZES),
        'qualities': list(QUALITIES),
    }


def _client():
    from openai import OpenAI
    if _client_factory is not None:  # tests
        return _client_factory()
    return OpenAI(api_key=providers.api_key('openai'), timeout=TIMEOUT_SECONDS, max_retries=1)


def generate(prompt, size, quality):
    """One PNG from OpenAI's image API. Raises the openai package's errors."""
    result = _client().images.generate(model=model(), prompt=prompt, size=size, quality=quality)
    item = result.data[0] if getattr(result, 'data', None) else None
    encoded = getattr(item, 'b64_json', None) if item is not None else None
    if not encoded:
        raise ValueError('the image API returned no image')
    return base64.b64decode(encoded)


def file_name(prompt):
    """The standalone app's name for a generated image: the prompt's first 40 letters and digits."""
    return (re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40].strip("-") or "image") + ".png"


def create(project, user, data):
    """Make one image and store it as a material (added, not committed). Raises ImageError."""
    import openai

    from src.studio import materials

    data = data if isinstance(data, dict) else {}
    prompt = data.get('prompt')
    prompt = prompt.strip() if isinstance(prompt, str) else ''
    if not prompt:
        raise ImageError('studio.images.errors.prompt')
    if len(prompt) > PROMPT_MAX:
        raise ImageError('studio.images.errors.promptTooLong', max=PROMPT_MAX)
    size = data.get('size') or SIZES[0]
    quality = data.get('quality') or 'medium'
    if size not in SIZES or quality not in QUALITIES:
        raise ImageError('studio.images.errors.options')
    if not providers.api_key('openai'):
        raise ImageError('studio.images.errors.noKey', env=providers.KEY_ENV['openai'])
    if not providers.enabled('openai'):
        raise ImageError('studio.images.errors.off')

    try:
        png = generate(prompt, size, quality)
    except openai.AuthenticationError:
        raise ImageError('studio.images.errors.keyRefused', 502, env=providers.KEY_ENV['openai'])
    except openai.RateLimitError:
        raise ImageError('studio.images.errors.rateLimited', 502)
    except openai.APIStatusError as e:
        message = getattr(e, 'message', None) or str(e)
        raise ImageError('studio.images.errors.api', 502, code=e.status_code, message=str(message)[:300])
    except openai.APIConnectionError:
        raise ImageError('studio.images.errors.connection', 502)
    except (ValueError, TypeError):
        raise ImageError('studio.images.errors.noImage', 502)
    if not png.startswith(PNG_SIGNATURE):
        raise ImageError('studio.images.errors.noImage', 502)
    try:
        return materials.store_bytes(project, user, png, file_name(prompt))
    except materials.MaterialError as exc:
        raise ImageError(exc.key, 400, **exc.params)
