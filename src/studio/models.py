"""Database tables of the studio add-on.

Imported from src/models/__init__.py so that ``db.create_all()`` creates them
along with upstream's tables. Rules that keep upstream merges cheap:

- every table is named ``studio_*``;
- no column is ever added to an upstream table; a studio row points at an
  upstream row by id instead.

There are no tables yet: projects arrive in a later step.
"""
