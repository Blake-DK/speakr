"""Project materials: accepted and refused files, storage, serving, deleting."""

import io
import zipfile

import pytest

from src.app import db
from src.studio import materials
from src.studio.models import StudioMaterial
from tests.studio.support import file_part, sharing_on, world  # noqa: F401

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
GIF = b'GIF89a' + b'\x00' * 32
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 32


def _minimal_pptx():
    """A zip with the two parts that make a file a PowerPoint deck (not openable by python-pptx)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('ppt/presentation.xml', '<presentation/>')
    return buffer.getvalue()


def _zip_without_deck():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', '<document/>')
    return buffer.getvalue()


def _upload(world, user_id, project_id, *parts):
    return world.client(user_id).post(f'/api/studio/projects/{project_id}/materials',
                                      data={'files': [file_part(c, n) for c, n in parts]},
                                      content_type='multipart/form-data')


@pytest.fixture
def owned(world):
    owner = world.user()
    return world, owner, world.project(owner, 'Materials film')


def test_each_accepted_type_is_stored_and_listed(owned):
    world, owner, project_id = owned
    response = _upload(world, owner, project_id, (PDF, 'one.pdf'), (PNG, 'two.png'), (JPEG, 'three.jpeg'),
                       (GIF, 'four.gif'), (WEBP, 'five.webp'), (_minimal_pptx(), 'six.pptx'))
    assert response.status_code == 201, response.get_data(as_text=True)
    body = response.get_json()
    assert body['refused'] == []
    kinds = {m['name']: (m['kind'], m['content_type']) for m in body['stored']}
    assert kinds == {
        'one.pdf': ('pdf', 'application/pdf'), 'two.png': ('image', 'image/png'),
        'three.jpeg': ('image', 'image/jpeg'), 'four.gif': ('image', 'image/gif'),
        'five.webp': ('image', 'image/webp'), 'six.pptx': ('pptx', materials.PPTX_TYPE),
    }
    listed = world.client(owner).get(f'/api/studio/projects/{project_id}/materials').get_json()['items']
    assert {m['name'] for m in listed} == set(kinds)
    for row in StudioMaterial.query.filter_by(project_id=project_id):
        assert row.storage_locator.startswith('local://studio/materials/') or 'studio/materials/' in row.storage_locator


@pytest.mark.parametrize('content,name,code', [
    (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', 'logo.svg', 'type'),
    (b'MZ\x90\x00 not a pdf', 'invoice.pdf', 'content'),
    (PNG, 'photo.jpg', 'content'),
    (b'<html><script>alert(1)</script></html>', 'page.png', 'content'),
    (_zip_without_deck(), 'letter.pptx', 'content'),
    (PDF, 'notes.txt', 'type'),
    (PDF, 'no-extension', 'type'),
    (b'', 'empty.pdf', 'empty'),
])
def test_refused_files_are_named_and_nothing_is_stored(owned, content, name, code):
    world, owner, project_id = owned
    response = _upload(world, owner, project_id, (content, name))
    assert response.status_code == 400
    refused = response.get_json()['refused']
    assert [r['code'] for r in refused] == [code]
    assert StudioMaterial.query.filter_by(project_id=project_id).count() == 0


def test_a_file_over_the_limit_is_refused(owned, monkeypatch):
    world, owner, project_id = owned
    monkeypatch.setattr(materials, 'MAX_MATERIAL_BYTES', 64)
    response = _upload(world, owner, project_id, (PDF + b'x' * 64, 'big.pdf'), (PNG, 'small.png'))
    assert response.status_code == 201
    body = response.get_json()
    assert [r['code'] for r in body['refused']] == ['size']
    assert [m['name'] for m in body['stored']] == ['small.png']


def test_the_carried_over_limit_is_thirty_megabytes():
    assert materials.MAX_MATERIAL_BYTES == 30 * 1024 * 1024


def test_too_many_files_or_none_at_all(owned, monkeypatch):
    world, owner, project_id = owned
    assert _upload(world, owner, project_id).status_code == 400
    monkeypatch.setattr(materials, 'MAX_FILES_PER_UPLOAD', 1)
    assert _upload(world, owner, project_id, (PDF, 'a.pdf'), (PDF, 'b.pdf')).status_code == 400


def test_a_pdf_and_an_image_open_in_the_browser_and_a_deck_downloads(owned):
    world, owner, project_id = owned
    stored = _upload(world, owner, project_id, (PDF, 'one.pdf'), (PNG, 'two.png'),
                     (_minimal_pptx(), 'three.pptx')).get_json()['stored']
    by_name = {m['name']: m['id'] for m in stored}
    client = world.client(owner)

    pdf = client.get(f"/api/studio/materials/{by_name['one.pdf']}/file")
    assert pdf.status_code == 200 and pdf.data == PDF
    assert pdf.headers['Content-Type'] == 'application/pdf'
    assert pdf.headers['X-Content-Type-Options'] == 'nosniff'
    assert pdf.headers['Content-Disposition'].startswith('inline')
    assert 'sandbox' not in pdf.headers.get('Content-Security-Policy', '')

    png = client.get(f"/api/studio/materials/{by_name['two.png']}/file")
    assert png.headers['Content-Type'] == 'image/png'
    assert png.headers['Content-Disposition'].startswith('inline')
    assert png.headers['Content-Security-Policy'] == 'sandbox'

    deck = client.get(f"/api/studio/materials/{by_name['three.pptx']}/file")
    assert deck.headers['Content-Disposition'].startswith('attachment')
    assert deck.headers['Content-Type'] == materials.PPTX_TYPE

    asked = client.get(f"/api/studio/materials/{by_name['one.pdf']}/file?download=1")
    assert asked.headers['Content-Disposition'].startswith('attachment')


def test_a_file_name_cannot_reach_another_folder(owned):
    world, owner, project_id = owned
    stored = _upload(world, owner, project_id, (PDF, '../../etc/passwd.pdf')).get_json()['stored']
    assert stored[0]['name'] == 'passwd.pdf'
    row = db.session.get(StudioMaterial, stored[0]['id'])
    assert '..' not in row.storage_locator and 'passwd' not in row.storage_locator


def test_the_uploader_or_the_owner_deletes_and_the_file_goes(world, sharing_on):
    from src.services.storage import get_storage_service
    owner, member = world.user(), world.user()
    project_id = world.project(owner)
    world.share(owner, project_id, member)
    mine = _upload(world, member, project_id, (PDF, 'mine.pdf')).get_json()['stored'][0]['id']
    theirs = _upload(world, owner, project_id, (PDF, 'owners.pdf')).get_json()['stored'][0]['id']
    locator = db.session.get(StudioMaterial, mine).storage_locator
    member_client = world.client(member)
    assert member_client.delete(f'/api/studio/materials/{theirs}').status_code == 403
    assert member_client.delete(f'/api/studio/materials/{mine}').status_code == 200
    assert not get_storage_service().exists(locator)
    assert world.client(owner).delete(f'/api/studio/materials/{theirs}').status_code == 200


def test_deleting_the_project_deletes_its_files(owned):
    from src.services.storage import get_storage_service
    world, owner, project_id = owned
    material_id = _upload(world, owner, project_id, (PDF, 'one.pdf')).get_json()['stored'][0]['id']
    locator = db.session.get(StudioMaterial, material_id).storage_locator
    assert world.client(owner).delete(f'/api/studio/projects/{project_id}').status_code == 200
    assert not get_storage_service().exists(locator)
    assert db.session.get(StudioMaterial, material_id) is None


def test_a_missing_file_is_a_clear_not_found(owned):
    from src.services.storage import get_storage_service
    world, owner, project_id = owned
    material_id = _upload(world, owner, project_id, (PDF, 'one.pdf')).get_json()['stored'][0]['id']
    get_storage_service().delete(db.session.get(StudioMaterial, material_id).storage_locator)
    response = world.client(owner).get(f'/api/studio/materials/{material_id}/file')
    assert response.status_code == 404 and response.get_json()['code'] == 'missing'


def test_a_slide_deck_text_is_read_at_upload(owned, tmp_path):
    """The standalone app's own extraction check (the upload side of its PPTX test), on the upload route."""
    pptx = pytest.importorskip('pptx')
    from pptx.util import Inches
    world, owner, project_id = owned
    prs = pptx.Presentation()
    prs.slides.add_slide(prs.slide_layouts[0])
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = 'The Hook'
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(4), Inches(4), Inches(1)).table
    table.cell(0, 0).text = 'Budget'
    table.cell(0, 1).text = '£250k'
    slide.notes_slide.notes_text_frame.text = 'pause here for effect'
    path = tmp_path / 'deck.pptx'
    prs.save(str(path))

    stored = _upload(world, owner, project_id, (path.read_bytes(), 'deck.pptx')).get_json()['stored']
    assert stored[0]['has_text'] is True
    text = world.client(owner).get(f"/api/studio/materials/{stored[0]['id']}").get_json()['text']
    assert '--- Slide 2 ---' in text and 'The Hook' in text
    assert 'Budget | £250k' in text, 'table cells must be extracted'
    assert '[Speaker notes: pause here for effect]' in text
    assert 'slide deck "deck.pptx"' in text


def test_an_unreadable_deck_is_kept_with_a_note(owned):
    pytest.importorskip('pptx')
    world, owner, project_id = owned
    stored = _upload(world, owner, project_id, (_minimal_pptx(), 'broken.pptx')).get_json()['stored']
    text = world.client(owner).get(f"/api/studio/materials/{stored[0]['id']}").get_json()['text']
    assert 'not a readable PowerPoint' in text
