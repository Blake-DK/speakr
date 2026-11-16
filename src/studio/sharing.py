"""A project's Speakr folder and group: making, sharing, unsharing, deleting.

How a project maps onto Speakr:

- Every project has a folder of its own, owned by the project's owner, made
  when the project is made. Its name is the project's name (shortened to
  Speakr's 50 characters, with " (2)", " (3)", ... when the owner already has
  a folder of that name). The project's transcription hints are the folder's
  hotwords and initial prompt, so Speakr applies them to every recording
  added to the folder.
- A private project's folder is a personal folder.
- Sharing a project makes a Speakr group for it (named after the project,
  with its id, because group names are unique), with the owner as group admin
  and each person added as a group member, and makes the folder that group's
  folder with "share with the group" on. From then on Speakr itself shares
  every recording in the folder, the ones already there included, with the
  group: members can play them, the owner (group admin) can also edit them.
  A member can add recordings to the folder too; Speakr shares those as well.
- Removing a person ends their group membership; the recordings they had put
  into the folder leave it (they keep them, outside any folder), and the
  shares the folder made for them and of their recordings end.
- Removing the last person makes the project private again: the folder goes
  back to being a personal folder, the group is deleted, and every share the
  folder made ends. Shares someone made by hand in Speakr stay.
- Deleting a project deletes its materials, voice references, its group and
  its folder. The recordings stay with their owners, outside any folder.

Everything goes through upstream's models and share functions
(``apply_folder_shares``, ``sync_source_shares``, ``drop_stale_auto_shares``)
and upstream's audit events, so Speakr's own screens, its group management
and its audit log see the same thing as if an admin had done it by hand.
"""

from datetime import datetime

from src.database import db
from src.studio import access

FOLDER_NAME_MAX = 50   # Folder.name is String(50)
GROUP_NAME_MAX = 100   # Group.name is String(100)
MAX_PEOPLE = 50


class SharingError(Exception):
    """A refused sharing change; ``key`` names the studio string for it."""

    def __init__(self, key, status=400, **params):
        super().__init__(key)
        self.key = key
        self.status = status
        self.params = params


def _audit(event, **fields):
    from src.services import audit
    audit.record_in_tx(event, **fields)


# --- the folder -------------------------------------------------------------

def _free_folder_name(owner_id, wanted, exclude_id=None):
    from src.models import Folder
    base = ' '.join((wanted or '').split())[:FOLDER_NAME_MAX].strip() or 'Project'
    candidate = base
    n = 2
    while True:
        query = Folder.query.filter(Folder.user_id == owner_id, Folder.name == candidate)
        if exclude_id is not None:
            query = query.filter(Folder.id != exclude_id)
        if query.first() is None:
            return candidate
        suffix = f' ({n})'
        candidate = base[:FOLDER_NAME_MAX - len(suffix)].rstrip() + suffix
        n += 1


def make_folder(project, hotwords=None, initial_prompt=None):
    """A new personal folder of the owner for the project; sets project.folder_id. No commit."""
    from src.models import Folder
    folder = Folder(
        name=_free_folder_name(project.owner_id, project.name),
        user_id=project.owner_id,
        default_hotwords=hotwords or None,
        default_initial_prompt=initial_prompt or None,
        auto_share_on_apply=True,
        share_with_group_lead=True,
    )
    db.session.add(folder)
    db.session.flush()
    project.folder_id = folder.id
    return folder


def ensure_folder(project):
    """The project's folder; a new one when it was deleted in Speakr's folder screen. No commit.

    A shared project's new folder becomes its group's folder at once, so
    what is added to it is shared as before.
    """
    folder = access.project_folder(project)
    if folder is not None:
        return folder
    folder = make_folder(project)
    group = access.project_group(project)
    if group is not None and access.member_ids(project):
        _link_folder(project, folder, group)
    return folder


def rename_folder(project):
    """Give the folder the project's (new) name, where that name is free. No commit."""
    folder = access.project_folder(project)
    if folder is None:
        return
    folder.name = _free_folder_name(project.owner_id, project.name, exclude_id=folder.id)
    folder.updated_at = datetime.utcnow()


def set_hints(project, hotwords, initial_prompt):
    """The project's transcription hints are its folder's hotwords and initial prompt. No commit."""
    folder = ensure_folder(project)
    folder.default_hotwords = hotwords or None
    folder.default_initial_prompt = initial_prompt or None
    folder.updated_at = datetime.utcnow()


# --- the group --------------------------------------------------------------

def _free_group_name(project):
    from src.models import Group
    base = ' '.join(project.name.split())
    suffix = f' (project {project.id})'
    candidate = base[:GROUP_NAME_MAX - len(suffix)].rstrip() + suffix
    n = 2
    while Group.query.filter(db.func.lower(Group.name) == candidate.lower()).first() is not None:
        extra = f' (project {project.id}, {n})'
        candidate = base[:GROUP_NAME_MAX - len(extra)].rstrip() + extra
        n += 1
    return candidate


def _ensure_group(project):
    """The project's group, made if missing, with the owner as its admin. No commit."""
    from src.models import Group, GroupMembership
    group = access.project_group(project)
    if group is None:
        group = Group(
            name=_free_group_name(project),
            description='Made by the Projects page to share a project. Change its people on the '
                        "project's People list; the studio keeps them in step with it.",
            created_by=project.owner_id,
        )
        db.session.add(group)
        db.session.flush()
        project.group_id = group.id
        _audit('group.created', group_id=group.id)
    owner = GroupMembership.query.filter_by(group_id=group.id, user_id=project.owner_id).first()
    if owner is None:
        db.session.add(GroupMembership(group_id=group.id, user_id=project.owner_id, role='admin'))
        _audit('group.member.added', group_id=group.id, target_user_id=project.owner_id,
               details={'role': 'admin'})
    elif owner.role != 'admin':
        old = owner.role
        owner.role = 'admin'
        _audit('group.member.role_changed', group_id=group.id, target_user_id=project.owner_id,
               details={'role': 'admin', 'old_role': old if old in ('admin', 'member') else 'other'})
    db.session.flush()
    return group


def _move_out(folder, query_filter):
    """Take the recordings matching query_filter out of the folder; returns their ids. No commit."""
    from src.models import Recording
    moved = []
    for recording in Recording.query.filter(Recording.folder_id == folder.id, query_filter).all():
        recording.folder_id = None
        moved.append(recording.id)
    if moved:
        db.session.flush()
        # A list of the folder's recordings loaded earlier still holds them.
        db.session.expire(folder, ['recordings'])
    return moved


def _link_folder(project, folder, group):
    """Make the folder the group's folder, so Speakr shares its recordings with the group. No commit.

    Recordings in it that belong to someone other than the owner leave it
    first: they are there from an earlier sharing that an admin ended in
    Speakr's screens, and their owners never agreed to share them again.
    """
    from src.models import Folder, Recording
    from src.services.auto_shares import drop_stale_auto_shares, sharing_state, sync_source_shares
    if Folder.query.filter_by(parent_id=folder.id).first() is not None:
        raise SharingError('studio.errors.folderHasSubfolders', 409)
    if folder.group_id == group.id and folder.auto_share_on_apply:
        return
    moved = _move_out(folder, Recording.user_id != project.owner_id)
    before = sharing_state(folder)
    folder.group_id = group.id
    folder.parent_id = None  # a group folder cannot sit in a personal folder (#585)
    folder.auto_share_on_apply = True
    folder.share_with_group_lead = True
    folder.updated_at = datetime.utcnow()
    db.session.flush()
    sync_source_shares(folder, before, sharing_enabled=True)
    if moved:
        drop_stale_auto_shares(recording_ids=moved)


def _add_person(project, folder, group, user_id):
    from src.models import GroupMembership, Recording
    from src.services.auto_shares import apply_folder_shares
    from src.studio.models import StudioProjectMember
    if StudioProjectMember.query.filter_by(project_id=project.id, user_id=user_id).first() is None:
        db.session.add(StudioProjectMember(project_id=project.id, user_id=user_id))
    if GroupMembership.query.filter_by(group_id=group.id, user_id=user_id).first() is None:
        db.session.add(GroupMembership(group_id=group.id, user_id=user_id, role='member'))
        _audit('group.member.added', group_id=group.id, target_user_id=user_id, details={'role': 'member'})
    db.session.flush()
    # A new member gets the recordings already in the folder (Speakr's own
    # add-member route shares only what is added to the folder later). Read
    # from the database, not from a list loaded before someone was removed.
    for recording in Recording.query.filter(Recording.folder_id == folder.id).all():
        apply_folder_shares(recording, folder, sharing_enabled=True)


def _remove_person(project, folder, group_id, user_id):
    from src.models import GroupMembership, Recording
    from src.services.auto_shares import drop_stale_auto_shares
    from src.studio.models import StudioProjectMember
    StudioProjectMember.query.filter_by(project_id=project.id, user_id=user_id).delete()
    if group_id is not None:
        membership = GroupMembership.query.filter_by(group_id=group_id, user_id=user_id).first()
        if membership is not None:
            db.session.delete(membership)
            _audit('group.member.removed', group_id=group_id, target_user_id=user_id)
    moved = _move_out(folder, Recording.user_id == user_id) if folder is not None else []
    drop_stale_auto_shares(user_ids=[user_id])
    if moved:
        drop_stale_auto_shares(recording_ids=moved)


def _end_group(project, folder):
    """Folder back to personal, group deleted, its shares ended. No commit."""
    from src.models import GroupMembership
    from src.services.auto_shares import drop_stale_auto_shares, sharing_state, sync_source_shares
    from src.studio.models import StudioProjectMember
    group = access.project_group(project)
    if folder is not None and project.group_id is not None and folder.group_id == project.group_id:
        before = sharing_state(folder)
        folder.group_id = None
        folder.updated_at = datetime.utcnow()
        db.session.flush()
        sync_source_shares(folder, before, sharing_enabled=True)
    if group is not None:
        user_ids = [m.user_id for m in GroupMembership.query.filter_by(group_id=group.id).all()]
        group_id = group.id
        db.session.delete(group)
        db.session.flush()
        drop_stale_auto_shares(user_ids=user_ids or [project.owner_id])
        _audit('group.deleted', group_id=group_id)
    StudioProjectMember.query.filter_by(project_id=project.id).delete()
    project.group_id = None


# --- the People list --------------------------------------------------------

def people(project):
    """The owner and the people on the People list, as the page shows them."""
    from src.models import User
    owner = db.session.get(User, project.owner_id)
    result = [{'id': project.owner_id, 'username': owner.username if owner else None, 'role': 'owner'}]
    ids = sorted(access.member_ids(project))
    if ids:
        users = {u.id: u for u in User.query.filter(User.id.in_(ids)).all()}
        for user_id in ids:
            user = users.get(user_id)
            if user is not None:
                result.append({'id': user_id, 'username': user.username, 'role': 'member'})
    return result


def resolve_people(actor, project, user_ids, identifiers):
    """The account ids the owner names, checked as Speakr checks the people a share names.

    user_ids come from Speakr's user search (``/api/users/search``), which
    follows the user_search_scope setting; an id is accepted only for an
    account that search would show the owner (``nameable_user_ids``), for an
    account named by its full username or email address in identifiers, or
    for someone already on the People list. Raises SharingError naming the
    first refused entry; nothing is written before every entry is accepted.
    """
    from src.services import user_lookup
    if not isinstance(user_ids, list) or not isinstance(identifiers, list):
        raise SharingError('studio.errors.peopleInvalid')
    wanted = set()
    for value in user_ids:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SharingError('studio.errors.peopleInvalid')
        wanted.add(value)
    named = set()
    for value in identifiers:
        if not isinstance(value, str) or not value.strip() or len(value) > 320:
            raise SharingError('studio.errors.peopleInvalid')
        user = user_lookup.find_user_exactly(value)
        if user is None:
            raise SharingError('studio.errors.personNotFound', 404, name=value.strip())
        named.add(user.id)
    wanted |= named
    wanted.discard(project.owner_id)
    if len(wanted) > MAX_PEOPLE:
        raise SharingError('studio.errors.tooManyPeople', max=MAX_PEOPLE)
    current = access.member_ids(project)
    allowed = user_lookup.nameable_user_ids(actor, sorted(wanted - current), named_ids=named) | (wanted & current)
    if wanted - allowed:
        raise SharingError('studio.errors.peopleNotAllowed', 404)
    return wanted


def set_people(project, wanted):
    """Make the People list exactly ``wanted`` (ids, owner excluded). No commit.

    Returns (added ids, removed ids).
    """
    from src.studio.models import StudioProjectMember
    if not access.sharing_enabled():
        raise SharingError('studio.errors.sharingOff', 403)
    current = access.member_ids(project)
    # Rows left from a membership an admin ended in Speakr's group screens.
    listed = {uid for (uid,) in db.session.query(StudioProjectMember.user_id).filter_by(project_id=project.id)}
    stale = listed - current
    added = wanted - current
    removed = current - wanted
    if not wanted:
        if current or listed or project.group_id:
            folder = access.project_folder(project)
            for user_id in sorted(current):
                _remove_person(project, folder, project.group_id, user_id)
            _end_group(project, folder)
        return set(), removed
    folder = ensure_folder(project)
    group = _ensure_group(project)
    _link_folder(project, folder, group)
    for user_id in sorted(stale - wanted):
        StudioProjectMember.query.filter_by(project_id=project.id, user_id=user_id).delete()
    for user_id in sorted(removed):
        _remove_person(project, folder, group.id, user_id)
    for user_id in sorted(wanted):
        # Also re-adds someone whose group membership was ended in Speakr's
        # screens while the owner kept them on the list.
        _add_person(project, folder, group, user_id)
    return added, removed


# --- deleting ---------------------------------------------------------------

def delete_project(project):
    """Delete the project and what is only the project's. Recordings stay with their owners. No commit."""
    from src.models import InternalShare, Recording
    from src.services.auto_shares import drop_stale_auto_shares
    from src.studio import materials
    from src.studio.models import StudioMaterial, StudioProjectVisit, StudioVoiceReference
    folder = access.project_folder(project)
    for user_id in sorted(access.member_ids(project)):
        _remove_person(project, folder, project.group_id, user_id)
    _end_group(project, folder)
    for material in StudioMaterial.query.filter_by(project_id=project.id).all():
        materials.delete_stored_file(material)
        db.session.delete(material)
    StudioVoiceReference.query.filter_by(project_id=project.id).delete()
    StudioProjectVisit.query.filter_by(project_id=project.id).delete()
    if folder is not None:
        recording_ids = [rid for (rid,) in db.session.query(Recording.id).filter(Recording.folder_id == folder.id)]
        recording_ids += [rid for (rid,) in db.session.query(InternalShare.recording_id).filter(
            InternalShare.source_folder_id == folder.id)]
        db.session.delete(folder)
        if recording_ids:
            drop_stale_auto_shares(recording_ids=recording_ids)
    db.session.delete(project)
