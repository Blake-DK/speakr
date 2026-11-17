"""Project materials: PDFs, slide decks (PPTX) and images.

Accepted: .pdf, .pptx, .png, .jpg/.jpeg, .webp and .gif, up to 30 MB each
(the limit for documents and images in the standalone app the studio
replaces). The file's first bytes must
match its name: a PDF starts with "%PDF-", a PPTX is a zip holding
ppt/presentation.xml, and each image type has its own signature. SVG and
everything else is refused, so nothing a browser would run is ever stored.

An image made with "Generate an image" (src/studio/images.py) is stored
through the same checks and path as an upload (``store_bytes``).

Files are kept by Speakr's storage service (local disk or S3) under
``studio/materials/<project id>/``; a deleted project's empty folder is
removed (``prune_project_folder``). A slide deck's text (slides, tables and
speaker notes) is read once, at upload, with the standalone app's code, and
shown as its preview; the chat of a later version reads the same text.
"""

import os
import tempfile
import uuid
import zipfile

MAX_MATERIAL_BYTES = 30 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 20
MAX_NAME_LENGTH = 255
KEY_PREFIX = 'studio/materials'

PPTX_TYPE = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

# extension -> (kind, content type)
TYPES = {
    '.pdf': ('pdf', 'application/pdf'),
    '.pptx': ('pptx', PPTX_TYPE),
    '.png': ('image', 'image/png'),
    '.jpg': ('image', 'image/jpeg'),
    '.jpeg': ('image', 'image/jpeg'),
    '.webp': ('image', 'image/webp'),
    '.gif': ('image', 'image/gif'),
}

# Shown in the browser (Content-Disposition: inline); anything else is a download.
INLINE_KINDS = {'pdf', 'image'}


class MaterialError(Exception):
    """A refused file; ``key`` names the studio string for it."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.key = key
        self.params = params


def _content_type_from_bytes(head):
    """The content type the first bytes of a file show, or None."""
    if head.startswith(b'%PDF-'):
        return 'application/pdf'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head.startswith(b'PK\x03\x04'):
        return 'application/zip'
    return None


def _is_pptx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    return '[Content_Types].xml' in names and 'ppt/presentation.xml' in names


def clean_name(filename):
    """The file's own name, without folders or control characters, at most 255 characters."""
    name = os.path.basename((filename or '').replace('\\', '/'))
    name = ''.join(ch for ch in name if ch.isprintable()).strip()
    if len(name) > MAX_NAME_LENGTH:
        stem, ext = os.path.splitext(name)
        name = stem[:MAX_NAME_LENGTH - len(ext)] + ext
    return name


def check_type(path, filename):
    """(kind, content type) for an accepted file, else MaterialError."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in TYPES:
        raise MaterialError('studio.materials.errors.type', name=filename)
    kind, content_type = TYPES[ext]
    with open(path, 'rb') as fh:
        head = fh.read(16)
    found = _content_type_from_bytes(head)
    if kind == 'pptx':
        if found != 'application/zip' or not _is_pptx(path):
            raise MaterialError('studio.materials.errors.content', name=filename)
    elif found != content_type:
        raise MaterialError('studio.materials.errors.content', name=filename)
    return kind, content_type


def pptx_text(path, filename):
    """Neither provider accepts .pptx natively, so decks go in as extracted
    text: slide-by-slide shape text, table cells, and speaker notes.

    Carried over from the standalone app unchanged, apart from the import
    guard: without python-pptx (upstream's test run does not
    install it) the deck is kept and its text is left empty.
    """
    try:
        from pptx import Presentation
    except ImportError:
        return None

    try:
        prs = Presentation(path)
    except Exception:
        return f'[Attached file "{filename}" is not a readable PowerPoint file.]'

    parts = [f'Contents of attached slide deck "{filename}":']
    for n, slide in enumerate(prs.slides, 1):
        lines = [f"--- Slide {n} ---"]
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                lines.append(shape.text_frame.text.strip())
            elif getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        notes = slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide else ""
        if notes:
            lines.append(f"[Speaker notes: {notes}]")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _stage(upload, staging_dir):
    """Copy the upload to a staging file, refusing it past the size limit. Returns (path, size)."""
    handle, path = tempfile.mkstemp(prefix='studio-material-', dir=staging_dir)
    size = 0
    try:
        with os.fdopen(handle, 'wb') as out:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_MATERIAL_BYTES:
                    raise MaterialError('studio.materials.errors.size', name=upload.filename or '',
                                        max=MAX_MATERIAL_BYTES // (1024 * 1024))
                out.write(chunk)
    except BaseException:
        _remove(path)
        raise
    if size == 0:
        _remove(path)
        raise MaterialError('studio.materials.errors.empty', name=upload.filename or '')
    return path, size


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def store_upload(project, user, upload):
    """Check and store one uploaded file; returns the new StudioMaterial (added, not committed)."""
    from src.services.storage import get_storage_service

    name = clean_name(upload.filename)
    if not name:
        raise MaterialError('studio.materials.errors.type', name='')
    storage = get_storage_service()
    path, size = _stage(upload, storage.get_staging_dir())
    return _store_staged(project, user, storage, path, size, name)


def store_bytes(project, user, data, filename):
    """Check and store a file made by the studio itself (a generated image) as an upload is stored.

    The same limits, the same check of the first bytes against the name, the
    same storage path. Returns the new StudioMaterial (added, not committed).
    """
    from src.services.storage import get_storage_service

    name = clean_name(filename)
    if not name:
        raise MaterialError('studio.materials.errors.type', name='')
    if not data:
        raise MaterialError('studio.materials.errors.empty', name=name)
    if len(data) > MAX_MATERIAL_BYTES:
        raise MaterialError('studio.materials.errors.size', name=name, max=MAX_MATERIAL_BYTES // (1024 * 1024))
    storage = get_storage_service()
    handle, path = tempfile.mkstemp(prefix='studio-material-', dir=storage.get_staging_dir())
    try:
        with os.fdopen(handle, 'wb') as out:
            out.write(data)
    except BaseException:
        _remove(path)
        raise
    return _store_staged(project, user, storage, path, len(data), name)


def _store_staged(project, user, storage, path, size, name):
    """Check a staged file and move it into storage; returns the new StudioMaterial (added, not committed)."""
    from src.database import db
    from src.studio.models import StudioMaterial

    try:
        kind, content_type = check_type(path, name)
        text = pptx_text(path, name) if kind == 'pptx' else None
        ext = os.path.splitext(name)[1].lower()
        key = f'{KEY_PREFIX}/{project.id}/{uuid.uuid4().hex}{ext}'
        stored = storage.upload_local_file(path, key, content_type=content_type, delete_source=True)
    finally:
        _remove(path)
    material = StudioMaterial(
        project_id=project.id,
        uploaded_by=user.id,
        original_name=name,
        kind=kind,
        content_type=content_type,
        size=size,
        storage_locator=stored.locator,
        text=text,
    )
    db.session.add(material)
    return material


def delete_stored_file(material):
    """Remove the material's file from storage; a missing file is no error."""
    from src.services.storage import get_storage_service
    try:
        get_storage_service().delete(material.storage_locator, missing_ok=True)
    except (OSError, ValueError, RuntimeError):
        import logging
        logging.getLogger(__name__).warning("Studio material %s: its file could not be removed", material.id)


def prune_project_folder(project_id):
    """Remove the project's material folder on local storage when nothing is left in it.

    Deleting each file leaves its folder behind; a folder with anything in it
    is left alone. Nothing to do on S3, which has no folders.
    """
    from src.services.storage import get_storage_service
    from src.services.storage.locator import local_path_from_key
    try:
        storage = get_storage_service()
        if storage.default_backend_kind() == 's3':
            return
        os.rmdir(local_path_from_key(storage.settings.local_root, f'{KEY_PREFIX}/{int(project_id)}'))
    except (OSError, ValueError, RuntimeError, AttributeError):
        pass


def send(material, download):
    """The stored file as a response: its own content type, never sniffed; inline or a download.

    A PDF or an image opens in the browser unless download is asked for; a
    slide deck is always a download. An image also gets a sandbox policy, as
    Speakr serves its media, so it can never run script. A PDF does not: a
    sandboxed document cannot use the browser's PDF viewer.
    """
    import io

    from flask import send_file
    from src.services.storage import get_storage_service

    as_attachment = download or material.kind not in INLINE_KINDS
    storage = get_storage_service()
    try:
        with storage.materialize(material.storage_locator) as stored:
            if not os.path.exists(stored.local_path):
                return None
            if stored.cleanup_required:
                with open(stored.local_path, 'rb') as fh:
                    source = io.BytesIO(fh.read())
            else:
                source = stored.local_path
            response = send_file(source, mimetype=material.content_type, as_attachment=as_attachment,
                                 download_name=material.original_name, conditional=True, max_age=0)
    except (OSError, ValueError, RuntimeError):
        return None
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-cache'
    if material.kind == 'image':
        response.headers['Content-Security-Policy'] = 'sandbox'
    return response


def to_dict(material, can_delete, with_text=False):
    data = {
        'id': material.id,
        'project_id': material.project_id,
        'name': material.original_name,
        'kind': material.kind,
        'content_type': material.content_type,
        'size': material.size,
        'created_at': material.created_at.isoformat() + 'Z' if material.created_at else None,
        'uploaded_by': material.uploaded_by,
        'has_text': bool(material.text),
        'inline': material.kind in INLINE_KINDS,
        'can_delete': can_delete,
    }
    if with_text:
        data['text'] = material.text or ''
    return data
