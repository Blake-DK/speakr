"""A site administrator acts as the owner: STUDIO_ADMINS_AS_OWNERS (default on).

Alex, 2026-11-19: an administrator may do with every project and every
recording what its owner can, recordings in no project included. One function
answers it; every seam in upstream's files and every rule of the studio's own
calls it, so switching the setting off ("false") gives back exactly the
behaviour before it.

What it widens: opening, playing, downloading, editing (title, transcript, the
recording's own notes, summary), regenerating, reprocessing, deleting a
recording, and sharing it with people inside the site; in a project, every
capability its owner has (src/studio/access.py). The project keeps its owner,
the People list is not changed, and an administrator's role stays ADMIN.

What stays the owner's alone: public share links, the owner's tags and
folders (and so moving a recording between folders or projects), merging,
speaker names and profiles, and the job list. Viewer-keyed state (star,
inbox, archive, an administrator's own notes on a recording) stays the
viewer's.

has_recording_access also answers for API v1, so an administrator's API token
reaches as far as the administrator does (STUDIO.md, "An administrator acts as
the owner").

STUDIO_ADMINS_SEE_RECORDINGS (src/studio/admin_access.py) is separate: it makes
every administrator a member of every project's Speakr group. With this
setting on that membership is no longer needed for access.
"""

import os

SETTING = 'STUDIO_ADMINS_AS_OWNERS'
_OFF = ('0', 'false', 'no', 'off')


def enabled():
    return os.environ.get(SETTING, '').strip().lower() not in _OFF


def is_admin(user):
    return bool(user is not None and getattr(user, 'is_authenticated', True) and getattr(user, 'is_admin', False))


def acts_as_owner(user, owner_id):
    """True when this administrator acts as the owner of something owned by owner_id, not their own.
    Something with no owner (owner_id None, upstream's #581) is nobody's, for an administrator too."""
    if not enabled() or not is_admin(user) or owner_id is None:
        return False
    return getattr(user, 'id', None) != owner_id


def on_recording(user, recording):
    """acts_as_owner for a recording (None: False)."""
    return recording is not None and acts_as_owner(user, getattr(recording, 'user_id', None))


def page_flags(rec_dict, recording, user, owner=None):
    """The recording's flags for the page, so every control the server now allows shows and nothing
    it refuses does: edit, delete and sharing inside the site, the owner's name; public links,
    tags, folders and speakers stay the owner's (studio_admin_as_owner tells the page)."""
    if not on_recording(user, recording):
        return rec_dict
    if owner is None:
        from src.database import db
        from src.models import User
        owner = db.session.get(User, recording.user_id)
    name = (getattr(owner, 'name', None) or getattr(owner, 'username', None)) if owner is not None else None
    rec_dict['can_edit'] = True
    rec_dict['can_delete'] = True
    rec_dict['studio_admin_as_owner'] = True
    rec_dict['owner_username'] = name
    info = dict(rec_dict.get('share_info') or {})
    info.update({'can_edit': True, 'can_reshare': True, 'owner_username': name})
    rec_dict['share_info'] = info
    return rec_dict


def refuses_speakers(user, recording):
    """Speaker names and profiles stay the owner's: an administrator acting as the owner is refused."""
    return on_recording(user, recording)


def project_folder_of(folder):
    """The studio project whose folder this is, or None."""
    from src.studio.models import StudioProject
    return StudioProject.query.filter_by(folder_id=folder.id).first() if folder is not None else None


def from_others(user, accessible_ids, show_shared):
    """The ids the main list starts from: for an administrator acting as the owner, "From others"
    (shared=true) holds every recording that has an owner and is not theirs; the default list stays
    their own and what is shared with them."""
    if not show_shared or not enabled() or not is_admin(user):
        return accessible_ids
    from src.database import db
    from src.models import Recording
    others = {rid for (rid,) in db.session.query(Recording.id).filter(
        Recording.user_id.isnot(None), Recording.user_id != user.id)}
    return list(set(accessible_ids) | others)
