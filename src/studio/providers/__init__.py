"""The language-model providers of the studio's chat: Claude and OpenAI.

Both providers implement one interface, carried over from the standalone app
the studio replaces:

    stream_chat(system: str, messages: list[dict], use_web_search: bool,
                model: str) -> Iterator[dict]

where each message is ``{"role": "user" | "assistant", "text": str,
"attachments": [...]}`` and each attachment is one of

    {"kind": "pdf" | "image", "name": str, "content_type": str, "data": bytes}
    {"kind": "text", "name": str, "text": str}

(a slide deck or a recording's transcript arrives as text; see
src/studio/attachments.py). The providers are plain generators, run in
gunicorn's threads, not asyncio. They yield

    {"type": "text", "text": ...}
    {"type": "sources", "sources": [{"title": ..., "url": ...}], "kind": "cited" | "searched"}
    {"type": "error", "message": ...}
    {"type": "keepalive"}           while the model thinks or searches
    {"type": "usage", "usage": {...}, "model": ...}   once, at the end

and never raise for a provider error: it becomes an ``error`` event.

Keys come from the environment only (``STUDIO_ANTHROPIC_API_KEY``,
``STUDIO_OPENAI_API_KEY``); they are never stored, logged or sent to the
browser. The model of each provider and whether it is switched on are studio
settings an administrator changes (``studio_setting``), with
``STUDIO_CLAUDE_MODEL`` and ``STUDIO_OPENAI_MODEL`` as their defaults.
"""

import os

PROVIDERS = ('claude', 'openai')

KEY_ENV = {'claude': 'STUDIO_ANTHROPIC_API_KEY', 'openai': 'STUDIO_OPENAI_API_KEY'}
MODEL_ENV = {'claude': 'STUDIO_CLAUDE_MODEL', 'openai': 'STUDIO_OPENAI_MODEL'}
DEFAULT_MODELS = {'claude': 'claude-opus-5-5', 'openai': 'gpt-5.1'}
LABELS = {'claude': 'Claude', 'openai': 'GPT'}
MAX_MODEL_LENGTH = 100

# The model's output budget per answer, as in the standalone app.
MAX_OUTPUT_TOKENS = 64000

# Keep-alive events at most this often (seconds), so a proxy sees traffic and
# a closed browser tab is noticed while the model thinks.
KEEPALIVE_SECONDS = 5.0


def api_key(name):
    """The provider's key from the environment, or ''. Never logged or shown."""
    return (os.environ.get(KEY_ENV[name], '') or '').strip()


def _setting(key):
    from src.database import db
    from src.studio.models import StudioSetting
    try:
        row = db.session.get(StudioSetting, key)
    except Exception:  # the table is missing during a first start
        db.session.rollback()
        return None
    return row.value if row is not None else None


def model(name):
    """The model the provider uses: the administrator's choice, else the environment, else the default."""
    chosen = _setting(f'{name}_model')
    if chosen:
        return chosen
    return (os.environ.get(MODEL_ENV[name], '') or '').strip() or DEFAULT_MODELS[name]


def enabled(name):
    """Whether an administrator left the provider switched on (default: on)."""
    return _setting(f'{name}_enabled') != '0'


def available(name):
    """Usable now: a key is set and the provider is switched on."""
    return name in PROVIDERS and bool(api_key(name)) and enabled(name)


def describe():
    """The providers as the chat's picker shows them. No key, only whether one is set."""
    return {name: {
        'label': LABELS[name],
        'model': model(name),
        'has_key': bool(api_key(name)),
        'enabled': enabled(name),
        'available': available(name),
    } for name in PROVIDERS}


def set_setting(key, value):
    """Store a studio setting (no commit)."""
    from src.database import db
    from src.studio.models import StudioSetting
    row = db.session.get(StudioSetting, key)
    if row is None:
        db.session.add(StudioSetting(key=key, value=value))
    else:
        row.value = value


def clear_setting(key):
    from src.database import db
    from src.studio.models import StudioSetting
    row = db.session.get(StudioSetting, key)
    if row is not None:
        db.session.delete(row)


def module(name):
    """The provider's module (imported on first use: the SDKs are not needed elsewhere)."""
    if name == 'claude':
        from src.studio.providers import claude
        return claude
    if name == 'openai':
        from src.studio.providers import openai_client
        return openai_client
    raise ValueError(name)
