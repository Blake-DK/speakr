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


class StudioProjectVisit(db.Model):
    """When a person last opened a project's page: the order of the project switcher and list.

    One row per person and project, kept on the server so every device shows
    the same order. A row says nothing about access; the lists show only the
    projects the person may see (src/studio/access.py).
    """

    __tablename__ = 'studio_project_visit'
    __table_args__ = (db.UniqueConstraint('user_id', 'project_id', name='uq_studio_project_visit'),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('studio_project.id', ondelete='CASCADE'), nullable=False,
                           index=True)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioAdminGrant(db.Model):
    """A membership of a project's Speakr group that the studio made because the person is a site admin.

    Only with STUDIO_ADMINS_SEE_RECORDINGS on (src/studio/admin_access.py). The
    row says the studio made the membership, so it takes back exactly these
    and never one that the People list or a person in Speakr's group screens
    made.
    """

    __tablename__ = 'studio_admin_grant'
    __table_args__ = (db.UniqueConstraint('project_id', 'user_id', name='uq_studio_admin_grant'),)

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('studio_project.id', ondelete='CASCADE'), nullable=False,
                           index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id', ondelete='CASCADE'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioAdminProject(db.Model):
    """A project the administrators' rule has set up: its Speakr group and group folder.

    Only with STUDIO_ADMINS_SEE_RECORDINGS on. When the setting goes off, a
    project with this row and an empty People list is made private again (its
    group goes); one with people on its list keeps the group for them.
    """

    __tablename__ = 'studio_admin_project'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('studio_project.id', ondelete='CASCADE'), nullable=False,
                           unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioChat(db.Model):
    """A conversation with a language model in a project (src/studio/chats.py).

    ``kind`` is ``chat`` or the id of a workflow (src/studio/workflows.py);
    ``provider`` is the provider of the latest answer. ``busy_until`` and
    ``busy_token`` hold the chat while an answer is being written, so a second
    send to the same chat is refused instead of mixing the history; a holder
    that died (a crashed worker) lets go when ``busy_until`` passes.
    """

    __tablename__ = 'studio_chat'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('studio_project.id', ondelete='CASCADE'), nullable=False,
                           index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    title = db.Column(db.String(200), nullable=False, default='')
    kind = db.Column(db.String(40), nullable=False, default='chat')
    provider = db.Column(db.String(20), nullable=False, default='claude')
    busy_until = db.Column(db.DateTime, nullable=True)
    busy_token = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioMessage(db.Model):
    """One message of a chat: the person's (``user``), the model's (``assistant``) or a failed answer (``error``).

    ``error`` rows are shown in the chat and kept after a reload, but never
    sent to a provider; regenerating or editing removes them with the rest of
    the turn. ``attachments`` is a JSON list of ``{"type": "material" |
    "recording", "id": ..., "name": ...}``; each is read again, with the access
    of the person sending, whenever the history goes to a provider.
    ``sender_id`` and ``created_at`` may be set by an importer. A message
    stays when its sender's account is deleted (``sender_id`` becomes NULL).
    """

    __tablename__ = 'studio_message'

    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('studio_chat.id', ondelete='CASCADE'), nullable=False,
                        index=True)
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False, default='')
    attachments = db.Column(db.Text, nullable=False, default='[]')
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    provider = db.Column(db.String(20), nullable=True)
    model = db.Column(db.String(100), nullable=True)
    # Token counts of an answer, as JSON ({"input_tokens": ..., "output_tokens": ..., ...}).
    usage = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudioSetting(db.Model):
    """A setting of the studio's chat that an administrator changes on its settings page.

    Nothing secret: API keys come from the environment only
    (src/studio/providers/__init__.py).
    """

    __tablename__ = 'studio_setting'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False, default='')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StudioSchemaStep(db.Model):
    """The schema steps of src/studio/schema.py that have run on this database."""

    __tablename__ = 'studio_schema_step'

    step_id = db.Column(db.String(100), primary_key=True)
    applied_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# Studio rows go with the upstream row they point at, on SQLite too.
from src.studio import cleanup as _cleanup  # noqa: E402

_cleanup.register()
