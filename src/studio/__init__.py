"""Studio: the add-on this fork carries on top of upstream Speakr.

Everything the add-on needs lives in its own files (src/studio/,
templates/studio/, static/js/studio/, static/locales-studio/, tests/studio/),
so merging an upstream release touches as few upstream files as possible.
The upstream files it does touch, each in one marked block:

- src/app.py: registers ``studio_bp``.
- src/models/__init__.py: imports ``src.studio.models`` so ``db.create_all()``
  creates the add-on's tables.
- templates/components/header-controls.html: the link to the Projects page.
"""

from src.studio.routes import studio_bp

__all__ = ['studio_bp']
