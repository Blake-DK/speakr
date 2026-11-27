"""Studio: the add-on this fork carries on top of upstream Speakr.

Everything the add-on needs lives in its own files (src/studio/,
templates/studio/, static/js/studio/, static/locales-studio/, tests/studio/),
so merging an upstream release touches as few upstream files as possible.
The upstream files it does touch, each in one marked block:

- src/app.py: registers ``studio_bp``.
- src/models/__init__.py: imports ``src.studio.models`` so ``db.create_all()``
  creates the add-on's tables.
- templates/components/header-controls.html: the Projects button in the top bar.
- templates/index.html: one line that includes templates/studio/main_page.html,
  the studio's scripts on Speakr's main page.
"""

__all__ = ['studio_bp']


def __getattr__(name):
    # Loaded on first use, not on import: src.models imports src.studio.models,
    # and the routes import src.utils.token_auth, which imports src.models.
    if name == 'studio_bp':
        from src.studio.routes import studio_bp
        return studio_bp
    raise AttributeError(f"module 'src.studio' has no attribute {name!r}")
