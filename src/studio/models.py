"""Database tables of the studio add-on.

Imported from src/models/__init__.py so that ``db.create_all()`` creates them
along with upstream's tables. Rules that keep upstream merges cheap:

- every table is named ``studio_*``;
- no column is ever added to an upstream table; a studio row points at an
  upstream row by id instead;
- no relationship or backref is declared on an upstream model, so upstream's
  own deletes never load or touch studio rows. Every foreign key to an
  upstream table says what happens on delete, because PostgreSQL enforces
  them (a key without ON DELETE would refuse the deletion of a user, a folder
  or a recording there). SQLite, as Speakr runs it, does not enforce them, so
  the studio code also copes with an id whose row is gone.

A column added to one of these tables later is added by a step in
``src/studio/schema.py``; ``create_all`` only creates missing tables.
"""

from datetime import datetime

from src.database import db


class StudioProject(db.Model):
    """A film or production: a Speakr folder for its recordings, plus what the studio adds.

    The recordings live in the folder (``folder_id``). While the project is
    shared, the folder belongs to a Speakr group (``group_id``) that holds the
    owner (group admin) and the people the owner added (members); Speakr then
    shares the folder's recordings with them itself.
    """

    __tablename__ = 'studio_project'

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    logline = db.Column(db.Text, nullable=False, default='')
    notes = db.Column(db.Text, nullable=False, default='')
    folder_id = db.Column(db.Integer, db.ForeignKey('folder.id', ondelete='SET NULL'), nullable=True, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StudioProjectMember(db.Model):
    """A person the owner added to a project on the studio's People list.

    Access needs this row AND a membership of the project's Speakr group, so a
    change made in Speakr's own group screens never shows a project to
    someone the owner did not add (see src/studio/access.py).
    """

    __tablename__ = 'studio_project_member'
    __table_args__ = (db.UniqueConstraint('project_id', 'user_id', name='uq_studio_project_member'),)

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('studio_project.id', ondelete='CASCADE'), nullable=False,
                           index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    added_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioMaterial(db.Model):
    """A PDF, slide deck or image uploaded to a project.

    The file is kept by Speakr's storage service (local disk or S3) under the
    key prefix ``studio/materials/``; ``storage_locator`` is what that service
    returns. ``text`` holds a slide deck's extracted text.
    """

    __tablename__ = 'studio_material'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('studio_project.id', ondelete='CASCADE'), nullable=False,
                           index=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    original_name = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(10), nullable=False)  # pdf, pptx, image
    content_type = db.Column(db.String(100), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    storage_locator = db.Column(db.String(500), nullable=False)
    text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioVoiceReference(db.Model):
    """An example of how the project should sound: a recording of the project, or pasted text."""

    __tablename__ = 'studio_voice_reference'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('studio_project.id', ondelete='CASCADE'), nullable=False,
                           index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    recording_id = db.Column(db.Integer, db.ForeignKey('recording.id', ondelete='CASCADE'), nullable=True,
                             index=True)
    text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioSchemaStep(db.Model):
    """The schema steps of src/studio/schema.py that have run on this database."""

    __tablename__ = 'studio_schema_step'

    step_id = db.Column(db.String(100), primary_key=True)
    applied_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# Studio rows go with the upstream row they point at, on SQLite too.
from src.studio import cleanup as _cleanup  # noqa: E402

_cleanup.register()
