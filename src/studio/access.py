"""Who may see and change a studio project: one rule, in one place.

A person sees a project when they

- own it; or
- were added on its People list by the owner AND are still a member of the
  project's Speakr group; or
- are a site admin (admins could open every project in the standalone app
  the studio replaces, and its access test said so).

Both conditions of the second line are needed. The People list alone would
keep showing a project to someone an admin took out of the group in Speakr's
group screens; the group alone would show it to anyone an admin put into the
group there. Either change in Speakr's screens can only narrow who sees a
project, never widen it.

Everyone else gets "not found" for the project and for everything in it, so
nothing tells them that it exists (studio routes answer 404, never 403).

Recordings are not decided here: a project lists only the recordings of its
folder that Speakr itself lets the viewer open (``has_recording_access``), so
an admin who is not on a project's People list sees its materials but not
other people's recordings, unless the site switches on
STUDIO_ADMINS_SEE_RECORDINGS: then every site admin is a member of every
project's Speakr group and Speakr shares the folder's recordings with them,
read-only (src/studio/admin_access.py). Their role here stays ADMIN.

What each role may do (``capabilities``): owners do everything; people on the
People list and site admins read the project, edit its logline and notes,
add materials and voice references, and write and change its saved
documents; only the owner renames, shares and
deletes the project and changes its transcription hints (they are settings of
the Speakr folder, which Speakr lets only the folder's owner change); a site
admin may also delete a project whose owner's account no longer exists.
Adding a recording needs the folder in Speakr, so it is for the owner and the
People list, and for site admins while STUDIO_ADMINS_SEE_RECORDINGS makes
them members of the project's group.
"""

from src.database import db

OWNER = 'owner'
MEMBER = 'member'
ADMIN = 'admin'


def sharing_enabled():
    """Speakr's ENABLE_INTERNAL_SHARING (a System Setting, else .env), read as upstream reads it."""
    import src.app as speakr_app
    return bool(getattr(speakr_app, 'ENABLE_INTERNAL_SHARING', False))


def project_group(project):
    """The project's Speakr group, or None (never shared, made private, or deleted in Speakr's screens)."""
    from src.models import Group
    if not project.group_id:
        return None
    group = db.session.get(Group, project.group_id)
    # The studio makes the group with the owner as its creator; any other
    # group under this id is not the project's (see src/studio/cleanup.py).
    if group is None or group.created_by != project.owner_id:
        return None
    return group


def project_folder(project):
    """The project's Speakr folder, or None (not made yet, or deleted in Speakr's screens)."""
    from src.models import Folder
    if not project.folder_id:
        return None
    folder = db.session.get(Folder, project.folder_id)
    # The project's folder always belongs to the owner; any other folder
    # under this id is not the project's (see src/studio/cleanup.py).
    if folder is None or folder.user_id != project.owner_id:
        return None
    return folder


def member_ids(project):
    """Ids of the people on the project's People list who are still in its Speakr group."""
    from src.models import GroupMembership
    from src.studio.models import StudioProjectMember
    if project_group(project) is None:
        return set()
    rows = db.session.query(StudioProjectMember.user_id).join(
        GroupMembership,
        db.and_(GroupMembership.user_id == StudioProjectMember.user_id,
                GroupMembership.group_id == project.group_id),
    ).filter(StudioProjectMember.project_id == project.id)
    return {user_id for (user_id,) in rows if user_id != project.owner_id}


def role_of(project, user):
    """OWNER, MEMBER, ADMIN or None: the one access rule."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    if project.owner_id == user.id:
        return OWNER
    if user.id in member_ids(project):
        return MEMBER
    if getattr(user, 'is_admin', False):
        return ADMIN
    return None


def project_for(project_id, user):
    """(project, role) when the user may see project_id, else (None, None)."""
    from src.studio.models import StudioProject
    project = db.session.get(StudioProject, project_id) if isinstance(project_id, int) else None
    if project is None:
        return None, None
    role = role_of(project, user)
    if role is None:
        return None, None
    return project, role


def visible_projects(user):
    """The projects the user may see, newest first."""
    from src.models import GroupMembership
    from src.studio.models import StudioProject, StudioProjectMember
    query = StudioProject.query
    if not getattr(user, 'is_admin', False):
        shared = db.session.query(StudioProjectMember.project_id).join(
            StudioProject, StudioProject.id == StudioProjectMember.project_id
        ).join(
            GroupMembership,
            db.and_(GroupMembership.user_id == StudioProjectMember.user_id,
                    GroupMembership.group_id == StudioProject.group_id),
        ).filter(StudioProjectMember.user_id == user.id)
        query = query.filter(db.or_(StudioProject.owner_id == user.id, StudioProject.id.in_(shared)))
    candidates = query.order_by(StudioProject.created_at.desc(), StudioProject.id.desc()).all()
    # The query narrows the list; role_of, the one rule, decides.
    return [project for project in candidates if role_of(project, user) is not None]


def owner_exists(project):
    from src.models import User
    return db.session.get(User, project.owner_id) is not None


def acts_as_owner(project, role, user):
    """A site administrator, with STUDIO_ADMINS_AS_OWNERS on, may do here what the owner may
    (src/studio/admin_owner.py); the role stays ADMIN."""
    from src.studio.admin_owner import acts_as_owner as _acts
    return role == ADMIN and _acts(user, project.owner_id)


def capabilities(project, role, user):
    """What the viewer may do on this project, for the routes and for the page."""
    from src.studio import admin_access
    is_owner = role == OWNER or acts_as_owner(project, role, user)
    can_use_folder = role in (OWNER, MEMBER) or is_owner or (role == ADMIN and admin_access.active())
    return {
        'edit_details': role in (OWNER, MEMBER, ADMIN),
        'rename': is_owner,
        'edit_hints': is_owner,
        'share': is_owner,
        'delete': is_owner or (role == ADMIN and not owner_exists(project)),
        'add_recording': can_use_folder,
        'add_material': role in (OWNER, MEMBER, ADMIN),
        'add_voice': role in (OWNER, MEMBER, ADMIN),
        'add_document': role in (OWNER, MEMBER, ADMIN),
    }


def may_delete_item(project, role, user, created_by):
    """A material or voice reference: the person who added it, or the project's owner."""
    return role == OWNER or acts_as_owner(project, role, user) or (created_by is not None and created_by == user.id)
