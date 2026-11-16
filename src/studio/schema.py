"""Changes to the studio's own tables after they were first created.

``db.create_all()`` creates a missing table but never changes one that
exists. A later change to a ``studio_*`` table (a new column, an index, a
type change) is therefore a step in STEPS:

    STEPS = [
        ('0001_material_page_count', lambda engine: add_column_if_not_exists(
            engine, 'studio_material', 'page_count', 'INTEGER')),
    ]

Each step runs once per database, in list order, at start-up, right after
upstream's own migrations (the studio blueprint is registered after
``initialize_database``), under upstream's migration lock. A step that ran is
recorded in ``studio_schema_step`` and never runs again; a step that fails is
not recorded, is logged, and runs again at the next start. Steps use
upstream's helpers in ``src/utils/database.py`` (``add_column_if_not_exists``,
``create_index_if_not_exists``, ...), which work on SQLite and PostgreSQL and
are safe to run twice. A step id is never reused or renumbered. Upstream's
tables are never changed here.
"""

import logging

from sqlalchemy import text

from src.database import db

logger = logging.getLogger(__name__)

# (step id, callable taking the engine). Empty: the tables are as created.
STEPS = []


def _applied(engine):
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(text('SELECT step_id FROM studio_schema_step'))}


def _record(engine, step_id):
    with engine.connect() as conn:
        conn.execute(text('INSERT INTO studio_schema_step (step_id, applied_at) VALUES (:id, CURRENT_TIMESTAMP)'),
                     {'id': step_id})
        conn.commit()


def apply_schema_steps(steps=None, engine=None):
    """Run the steps that have not run on this database yet. Returns the ids that ran."""
    from src.studio.models import StudioSchemaStep
    from src.utils.database import migration_lock

    steps = STEPS if steps is None else steps
    engine = engine or db.engine
    ran = []
    with migration_lock(engine, logger=logger):
        StudioSchemaStep.__table__.create(bind=engine, checkfirst=True)
        done = _applied(engine)
        for step_id, step in steps:
            if step_id in done:
                continue
            try:
                step(engine)
            except Exception:
                logger.exception("Studio schema step %s failed; it runs again at the next start", step_id)
                break
            _record(engine, step_id)
            ran.append(step_id)
            logger.info("Applied studio schema step %s", step_id)
    return ran
