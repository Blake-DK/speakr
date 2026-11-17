"""Studio rows that go when Speakr deletes the upstream row they point at.

PostgreSQL does this itself through the ON DELETE clauses of the studio
tables. SQLite, as Speakr runs it, does not enforce foreign keys, and it
reuses the id of a deleted row for the next row of that table; a studio row
left pointing at a deleted folder, group, recording or account would then
point at someone else's new one. So the same clean-up also runs here, in the
same transaction as Speakr's own delete, through SQLAlchemy's before_delete
events on upstream's models (Speakr deletes these rows through the ORM).
Before, not after: on PostgreSQL the ON DELETE clauses act within the DELETE
statement itself, so after it the rows to clean up (and the material files
of a deleted account) could no longer be found.

- a folder: projects that used it lose it (the project shows "folder
  deleted" and makes a new one when needed);
- a group: projects shared through it become private, their People lists
  are emptied;
- a recording: voice references made from it go;
- an account: the projects it owned go, with their materials (rows and
  files) and voice references; its places on other projects' People lists go,
  and so does its record of the projects it opened.

The administrators' rule (src/studio/admin_access.py) keeps two tables: the
group memberships it made go with their project, group or account, and its
mark on a project goes with the project or the project's group.
"""

import logging

from sqlalchemy import event, select

logger = logging.getLogger(__name__)


def _tables():
    from src.studio.models import StudioMaterial, StudioProject, StudioProjectMember, StudioVoiceReference
    return (StudioProject.__table__, StudioProjectMember.__table__, StudioMaterial.__table__,
            StudioVoiceReference.__table__)


def _visits():
    from src.studio.models import StudioProjectVisit
    return StudioProjectVisit.__table__


def _admin_tables():
    from src.studio.models import StudioAdminGrant, StudioAdminProject
    return StudioAdminGrant.__table__, StudioAdminProject.__table__


def _drop_projects(connection, project_ids):
    """Delete these projects and their rows; returns the storage locators of their materials."""
    projects, members, materials, voices = _tables()
    if not project_ids:
        return []
    locators = [row[0] for row in connection.execute(
        select(materials.c.storage_locator).where(materials.c.project_id.in_(project_ids)))]
    for table in (materials, voices, members, _visits()) + _admin_tables():
        connection.execute(table.delete().where(table.c.project_id.in_(project_ids)))
    connection.execute(projects.delete().where(projects.c.id.in_(project_ids)))
    return locators


def folder_deleted(mapper, connection, target):
    projects = _tables()[0]
    connection.execute(projects.update().where(projects.c.folder_id == target.id).values(folder_id=None))


def group_deleted(mapper, connection, target):
    projects, members, _materials, _voices = _tables()
    grants, marks = _admin_tables()
    connection.execute(grants.delete().where(grants.c.group_id == target.id))
    ids = [row[0] for row in connection.execute(select(projects.c.id).where(projects.c.group_id == target.id))]
    if ids:
        connection.execute(members.delete().where(members.c.project_id.in_(ids)))
        connection.execute(grants.delete().where(grants.c.project_id.in_(ids)))
        connection.execute(marks.delete().where(marks.c.project_id.in_(ids)))
        connection.execute(projects.update().where(projects.c.id.in_(ids)).values(group_id=None))


def recording_deleted(mapper, connection, target):
    voices = _tables()[3]
    connection.execute(voices.delete().where(voices.c.recording_id == target.id))


def user_deleted(mapper, connection, target):
    projects, members, materials, voices = _tables()
    connection.execute(members.delete().where(members.c.user_id == target.id))
    grants = _admin_tables()[0]
    connection.execute(grants.delete().where(grants.c.user_id == target.id))
    visits = _visits()
    connection.execute(visits.delete().where(visits.c.user_id == target.id))
    connection.execute(materials.update().where(materials.c.uploaded_by == target.id).values(uploaded_by=None))
    connection.execute(voices.update().where(voices.c.created_by == target.id).values(created_by=None))
    owned = [row[0] for row in connection.execute(select(projects.c.id).where(projects.c.owner_id == target.id))]
    locators = _drop_projects(connection, owned)
    if locators:
        from src.services.storage import get_storage_service
        storage = get_storage_service()
        for locator in locators:
            try:
                storage.delete(locator, missing_ok=True)
            except (OSError, ValueError, RuntimeError):
                logger.warning("A studio material file of a deleted account could not be removed")


def register():
    """Attach the listeners to upstream's models (once)."""
    from src.models import Folder, Group, Recording, User
    for model, listener in ((Folder, folder_deleted), (Group, group_deleted), (Recording, recording_deleted),
                            (User, user_deleted)):
        if not event.contains(model, 'before_delete', listener):
            event.listen(model, 'before_delete', listener)
