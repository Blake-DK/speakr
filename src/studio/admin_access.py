"""Administrators open every project's recordings: STUDIO_ADMINS_SEE_RECORDINGS.

Speakr opens a recording to its owner and to the people it is shared with
(``has_recording_access`` in src/app.py); being a site admin gives nothing.
With this setting on, the studio gives every site admin a read-only share of
every recording in a project's folder, in the way Speakr's own group folders
share:

- every project has its Speakr group (the owner as group admin) and its
  folder is that group's folder, private projects included;
- every site admin other than the owner is a member of each project's group,
  role ``member``. Speakr then shares the folder's recordings with them,
  read-only, and keeps that in step itself: a new upload into the folder, a
  recording moved in or out, a recording deleted;
- ``studio_admin_grant`` records the memberships made here, so exactly those
  are taken back and never one that the People list or a person in Speakr's
  group screens made; ``studio_admin_project`` records the projects set up
  here, so a project with an empty People list is made private again when
  the setting goes off.

Studio roles do not change: an admin who is not on the People list is still
``access.ADMIN``, the People list is still the owner's, and "shared" still
means the People list has someone. Being in the group also lets an admin add
recordings of their own to the project, as a person on the People list can;
when they stop being an admin those recordings leave the project's folder,
as when someone leaves the People list.

When it runs:

- at start-up, in every process, under upstream's migration lock: with the
  setting on, every project is brought in line (sync_all); with it off,
  everything an earlier "on" made is taken back (undo_all);
- when the studio makes a project, changes its People list or makes its
  folder again (``project_changed``);
- after a request in which an account's admin status changed, for that
  account;
- before each request of a signed-in person whose memberships do not match
  their admin status (an admin missing from a project, or someone who is no
  longer an admin still holding one), so a change made by any route reaches
  them at the latest on their next request.

The request hooks never take a request down: an error while giving an admin
their memberships is rolled back and logged and the request goes on; an error
while taking access back from someone who is no longer an admin is rolled
back and logged and that person's request is refused with a 503 until the
take-back succeeds (see ``before_request``).

Off by default, so another install keeps Speakr's own rule. With the setting
off, nothing here touches the database during a request. The rule needs
Speakr's internal sharing (ENABLE_INTERNAL_SHARING): without it a share opens
nothing, so the rule then waits and changes nothing.
"""

import logging
import os

from flask import g, has_request_context, request
from flask_login import current_user
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError

from src.database import db
from src.studio import access

logger = logging.getLogger(__name__)

SETTING = 'STUDIO_ADMINS_SEE_RECORDINGS'
_CHANGED_KEY = 'studio_admin_status_changed'


def setting_on():
    """STUDIO_ADMINS_SEE_RECORDINGS, read on every call, so a test or a restart changes it."""
    return os.environ.get(SETTING, '').strip().lower() in ('1', 'true', 'yes', 'on')


def active():
    """The rule applies: the setting is on and Speakr's internal sharing is on."""
    return setting_on() and access.sharing_enabled()


# --- who ----------------------------------------------------------------------

def _admin_ids():
    from src.models import User
    return {user_id for (user_id,) in db.session.query(User.id).filter(User.is_admin.is_(True))}


def _listed_ids(project):
    from src.studio.models import StudioProjectMember
    return {user_id for (user_id,) in
            db.session.query(StudioProjectMember.user_id).filter(StudioProjectMember.project_id == project.id)}


def _projects_missing(user_id):
    """Projects of others whose group does not hold this account (or that have no group)."""
    from src.models import GroupMembership
    from src.studio.models import StudioProject
    in_group = db.session.query(GroupMembership.id).filter(
        GroupMembership.group_id == StudioProject.group_id,
        GroupMembership.user_id == user_id,
    ).exists()
    return StudioProject.query.filter(StudioProject.owner_id != user_id, ~in_group).order_by(StudioProject.id)


# --- one project ----------------------------------------------------------------

def _mark(project):
    from src.studio.models import StudioAdminProject
    if StudioAdminProject.query.filter_by(project_id=project.id).first() is None:
        db.session.add(StudioAdminProject(project_id=project.id))


def _share_folder(folder, user_ids):
    """Share what is in the folder with these new members, out of their inboxes. No commit.

    Speakr's apply_folder_shares puts each new share in the recipient's inbox;
    for recordings that were already there, that would fill an admin's inbox
    with every project's recordings at once. What is added to the folder later
    reaches their inbox as Speakr does it.
    """
    from src.models import InternalShare, Recording, SharedRecordingState
    from src.services.auto_shares import apply_folder_shares
    recordings = Recording.query.filter(Recording.folder_id == folder.id).order_by(Recording.id).all()
    if not recordings or not user_ids:
        return 0
    ids = [r.id for r in recordings]

    def pairs():
        return set(db.session.query(InternalShare.recording_id, InternalShare.shared_with_user_id).filter(
            InternalShare.recording_id.in_(ids), InternalShare.shared_with_user_id.in_(user_ids)))

    had = pairs()
    for recording in recordings:
        apply_folder_shares(recording, folder, sharing_enabled=True)
    new = pairs() - had
    if new:
        for state in SharedRecordingState.query.filter(SharedRecordingState.recording_id.in_(ids),
                                                       SharedRecordingState.user_id.in_(user_ids)):
            if (state.recording_id, state.user_id) in new:
                state.is_inbox = False
        db.session.flush()
    return len(new)


def _take_back(project, folder, grant):
    """End a membership the rule made. No commit.

    The membership stays when the person is on the project's People list (the
    list holds it then) or when someone made them a group admin in Speakr's
    group screens (theirs now). Otherwise it ends as when someone leaves the
    People list: their recordings leave the folder, and the shares the folder
    gave them, and gave of their recordings, end.
    """
    from src.models import GroupMembership, Recording
    from src.services.auto_shares import drop_stale_auto_shares
    from src.studio import sharing
    user_id, group_id = grant.user_id, grant.group_id
    db.session.delete(grant)
    if group_id is None or user_id in _listed_ids(project):
        return False
    membership = GroupMembership.query.filter_by(group_id=group_id, user_id=user_id).first()
    if membership is None or membership.role != 'member':
        return False
    db.session.delete(membership)
    sharing._audit('group.member.removed', group_id=group_id, target_user_id=user_id)
    moved = []
    if folder is not None and folder.group_id == group_id:
        moved = sharing._move_out(folder, Recording.user_id == user_id)
    drop_stale_auto_shares(user_ids=[user_id])
    if moved:
        drop_stale_auto_shares(recording_ids=moved)
    return True


def sync_project(project, admins=None):
    """Bring one project in line with the rule. No commit. Returns the number of memberships made.

    The project gets its group (the owner as its admin) and its folder becomes
    the group's folder; each site admin who is not the owner and not yet in
    the group becomes a member, and gets read-only shares of what is in the
    folder; a membership the rule made for someone who is no longer an admin
    is taken back.
    """
    from src.models import GroupMembership
    from src.studio import sharing
    from src.studio.models import StudioAdminGrant
    admins = _admin_ids() if admins is None else set(admins)
    group = sharing._ensure_group(project)
    folder = access.project_folder(project)
    if folder is not None:
        try:
            sharing._link_folder(project, folder, group)
        except sharing.SharingError:
            # A folder with subfolders cannot be a group folder (Speakr #585).
            logger.warning("Studio project %s: its folder has subfolders, so the administrators "
                           "cannot be given its recordings", project.id)
            folder = None
    _mark(project)
    grants = {grant.user_id: grant for grant in StudioAdminGrant.query.filter_by(project_id=project.id)}
    for user_id, grant in list(grants.items()):
        if user_id not in admins or user_id == project.owner_id:
            _take_back(project, folder, grant)
            del grants[user_id]
    members = {m.user_id for m in GroupMembership.query.filter_by(group_id=group.id)}
    added = []
    for user_id in sorted(admins - members - {project.owner_id}):
        db.session.add(GroupMembership(group_id=group.id, user_id=user_id, role='member'))
        sharing._audit('group.member.added', group_id=group.id, target_user_id=user_id, details={'role': 'member'})
        grant = grants.get(user_id)
        if grant is None:
            db.session.add(StudioAdminGrant(project_id=project.id, user_id=user_id, group_id=group.id))
        else:
            grant.group_id = group.id
        added.append(user_id)
    db.session.flush()
    if folder is not None and added:
        _share_folder(folder, added)
    return len(added)


def project_changed(project):
    """After the studio made a project, changed its People list or made its folder again. No commit."""
    if active():
        sync_project(project)


def forget_project(project):
    """Before the studio deletes a project: the rule's rows for it. No commit."""
    from src.studio.models import StudioAdminGrant, StudioAdminProject
    StudioAdminGrant.query.filter_by(project_id=project.id).delete(synchronize_session=False)
    StudioAdminProject.query.filter_by(project_id=project.id).delete(synchronize_session=False)


# --- one account ----------------------------------------------------------------

def sync_user(user):
    """Bring one account's memberships in line with its admin status. No commit. True when something changed."""
    from src.studio.models import StudioAdminGrant, StudioProject
    if getattr(user, 'is_admin', False):
        missing = _projects_missing(user.id).all()
        if missing:
            admins = _admin_ids()
            for project in missing:
                sync_project(project, admins)
        return bool(missing)
    grants = StudioAdminGrant.query.filter_by(user_id=user.id).order_by(StudioAdminGrant.id).all()
    for grant in grants:
        project = db.session.get(StudioProject, grant.project_id)
        if project is None:
            db.session.delete(grant)
            continue
        _take_back(project, access.project_folder(project), grant)
    if grants:
        db.session.flush()
    return bool(grants)


def _needs_sync(user):
    from src.studio.models import StudioAdminGrant
    if user.is_admin:
        return _projects_missing(user.id).first() is not None
    return StudioAdminGrant.query.filter_by(user_id=user.id).first() is not None


# --- every project ----------------------------------------------------------------

def sync_all():
    """The start-up pass with the rule on. No commit. Returns (projects, memberships made)."""
    from src.studio.models import StudioProject
    admins = _admin_ids()
    projects = StudioProject.query.order_by(StudioProject.id).all()
    made = 0
    for project in projects:
        made += sync_project(project, admins)
    return len(projects), made


def undo_all():
    """The start-up pass with the setting off: take back what the rule made. No commit.

    Returns (memberships ended, projects made private again). Writes nothing
    when the rule never ran.
    """
    from src.studio import sharing
    from src.studio.models import StudioAdminGrant, StudioAdminProject, StudioProject
    grants = StudioAdminGrant.query.order_by(StudioAdminGrant.id).all()
    marks = StudioAdminProject.query.order_by(StudioAdminProject.id).all()
    if not grants and not marks:
        return 0, 0
    ended = 0
    for grant in grants:
        project = db.session.get(StudioProject, grant.project_id)
        if project is None:
            db.session.delete(grant)
            continue
        ended += _take_back(project, access.project_folder(project), grant)
    db.session.flush()
    private = 0
    for mark in marks:
        project = db.session.get(StudioProject, mark.project_id)
        db.session.delete(mark)
        db.session.flush()
        if project is None or project.group_id is None or _listed_ids(project):
            continue
        sharing._end_group(project, access.project_folder(project))
        private += 1
    return ended, private


def startup(app_logger=None):
    """At start-up, in every process (src/studio/routes.py), under upstream's migration lock."""
    from src.utils.database import migration_lock
    log = app_logger or logger
    with migration_lock(db.engine, logger=log):
        try:
            if setting_on():
                if not access.sharing_enabled():
                    log.warning("%s is on, but internal sharing is off: administrators cannot be given "
                                "project recordings, so nothing was changed", SETTING)
                    return
                projects, made = sync_all()
                db.session.commit()
                if made:
                    log.info("Studio: administrators added to %s project group(s) across %s project(s)",
                             made, projects)
            else:
                ended, private = undo_all()
                db.session.commit()
                if ended or private:
                    log.info("Studio: %s off, %s administrator membership(s) ended, %s project(s) private again",
                             SETTING, ended, private)
        except Exception:
            db.session.rollback()
            log.exception("Studio: the administrators' access to project recordings could not be brought "
                          "in line at start-up")


# --- requests -----------------------------------------------------------------

def _unavailable():
    """The answer to a request refused because taking access back failed: a plain 503."""
    from flask import Response
    return Response('Your access is being updated. Try again in a moment.\n', status=503,
                    mimetype='text/plain', headers={'Retry-After': '5', 'Cache-Control': 'no-store'})


def _grants_left(user_id):
    from src.studio.models import StudioAdminGrant
    return StudioAdminGrant.query.filter_by(user_id=user_id).first() is not None


def before_request():
    """Bring the signed-in person's memberships in line with their admin status, before the request runs.

    This rule must never take requests down:

    - giving an admin their memberships: on any error the transaction is
      rolled back, the error is logged, and the request goes on as if the
      rule had not run (the next request tries again);
    - taking access back from someone who is no longer an admin: on any error
      the transaction is rolled back, the error is logged, and the request is
      refused with a plain 503 while that person still holds the access, so a
      former admin never keeps reading because the take-back failed. A request
      that needs no take-back is never refused; if even the check whether one
      is needed fails, the request goes on (nothing could be read then that
      the check would have stopped: the check reads only the rule's own table).
    """
    if not setting_on() or request.endpoint in (None, 'static'):
        return None
    try:
        if not current_user.is_authenticated or not access.sharing_enabled():
            return None
        user = current_user._get_current_object()
        user_id, is_admin = user.id, bool(user.is_admin)
        needed = _needs_sync(user)
    except Exception:
        _rollback()
        logger.exception("Studio: could not check whether a request needs the administrators' rule; "
                         "the request goes on")
        return None
    if not needed:
        return None
    if is_admin:
        try:
            sync_user(user)
            db.session.commit()
        except IntegrityError:
            _rollback()  # another request of the same person did it first
        except Exception:
            _rollback()
            logger.exception("Studio: an administrator's memberships could not be added; the request goes on")
        return None
    try:
        sync_user(user)
        db.session.commit()
        return None
    except Exception:
        _rollback()
        logger.exception("Studio: access of a former administrator could not be taken back; the request is "
                         "refused until it can")
    try:
        if not _grants_left(user_id):
            return None  # another request took it back meanwhile
    except Exception:
        _rollback()
    return _unavailable()


def _rollback():
    try:
        db.session.rollback()
    except Exception:
        logger.exception("Studio: rollback failed")


def _note_status_change(mapper, connection, target):
    """User after_insert / after_update: remember accounts whose admin status changed in this request.

    Runs inside Speakr's own flush, so it never raises: a failure here only
    leaves the change to the next request of that account, or the next start.
    """
    try:
        if not has_request_context():
            return
        # A new account made an admin has the value as a change too; one left at
        # the default (not an admin) has none and needs nothing.
        if not inspect(target).attrs.is_admin.history.has_changes():
            return
        changed = getattr(g, _CHANGED_KEY, None)
        if changed is None:
            changed = set()
            setattr(g, _CHANGED_KEY, changed)
        changed.add(target.id)
    except Exception:
        logger.exception("Studio: a change of admin status could not be noted; it is handled on that "
                         "account's next request")


def after_request(response):
    """After a request that changed someone's admin status: bring that account in line now.

    Never raises and always returns the response it was given. On an error the
    rule's changes are rolled back and logged; a person who lost admin status
    then meets the take-back again before their next request
    (``before_request``), which refuses that request if it still fails.
    """
    try:
        changed = getattr(g, _CHANGED_KEY, None)
        if not changed:
            return response
        setattr(g, _CHANGED_KEY, None)
        if not setting_on():
            return response
        from src.models import User
        # What the request left uncommitted would be rolled back when it ends
        # anyway; roll it back first, so only the rule's own changes commit.
        db.session.rollback()
        if not access.sharing_enabled():
            return response
        for user_id in sorted(i for i in changed if i is not None):
            user = db.session.get(User, user_id)
            if user is not None:
                sync_user(user)
        db.session.commit()
    except Exception:
        _rollback()
        logger.exception("Studio: the administrators' access could not be brought in line after a change "
                         "of admin status")
    return response


def register(blueprint):
    """The request hooks on the studio blueprint, and the admin-status listener on Speakr's User (once)."""
    from src.models import User
    blueprint.before_app_request(before_request)
    blueprint.after_app_request(after_request)
    for name in ('after_insert', 'after_update'):
        if not event.contains(User, name, _note_status_change):
            event.listen(User, name, _note_status_change)
