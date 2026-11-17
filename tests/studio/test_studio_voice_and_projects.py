"""Voice references, project details, and the studio's schema steps."""

import pytest
from sqlalchemy import inspect

from src.app import app, db
from src.models import Folder
from src.studio import schema, voice
from src.studio.models import StudioProject, StudioVoiceReference
from tests.studio.support import sharing_on, world  # noqa: F401

SRT = """1
00:00:01,000 --> 00:00:03,000
<i>One night.</i>

2
00:00:03,500 --> 00:00:05,000
One night.

3
00:00:05,000 --> 00:00:07,000
No way out.
"""


def test_subtitles_are_cleaned_as_the_standalone_app_cleaned_them():
    assert voice.clean_transcript(SRT) == 'One night.\nNo way out.'
    assert voice.clean_transcript('WEBVTT\n\n00:01.000 --> 00:02.000\nHello') == 'Hello'


def test_pasted_text_becomes_a_voice_reference(world):
    owner = world.user()
    project_id = world.project(owner)
    client = world.client(owner)
    created = client.post(f'/api/studio/projects/{project_id}/voice', json={'title': 'Trailer', 'text': SRT})
    assert created.status_code == 201
    body = created.get_json()
    assert body['source'] == 'text' and body['text'] == 'One night.\nNo way out.'
    assert client.post(f'/api/studio/projects/{project_id}/voice', json={'text': '1\n00:00:01,000 --> 00:00:02,000\n'}
                       ).status_code == 400
    untitled = client.post(f'/api/studio/projects/{project_id}/voice', json={'text': 'Hello.'}).get_json()
    assert untitled['title'] == 'Untitled text'


def test_a_recording_of_the_project_becomes_a_voice_reference(world):
    owner = world.user()
    project_id = world.project(owner)
    folder_id = db.session.get(StudioProject, project_id).folder_id
    inside = world.recording(owner, folder_id=folder_id, title='Director commentary')
    outside = world.recording(owner, title='Somewhere else')
    client = world.client(owner)
    created = client.post(f'/api/studio/projects/{project_id}/voice', json={'recording_id': inside})
    assert created.status_code == 201
    assert created.get_json()['title'] == 'Director commentary'
    assert created.get_json()['recording_title'] == 'Director commentary'
    assert client.post(f'/api/studio/projects/{project_id}/voice', json={'recording_id': inside}).status_code == 400
    assert client.post(f'/api/studio/projects/{project_id}/voice', json={'recording_id': outside}).status_code == 404
    assert client.post(f'/api/studio/projects/{project_id}/voice', json={'recording_id': '1'}).status_code == 404


def test_someone_elses_recording_needs_access_in_speakr(world, sharing_on):
    owner, member, admin = world.user(), world.user(), world.user(admin=True)
    project_id = world.project(owner)
    world.share(owner, project_id, member)
    folder_id = db.session.get(StudioProject, project_id).folder_id
    recording = world.recording(owner, folder_id=folder_id, title='Owner only')
    # Not shared yet with the member (no apply_folder_shares ran for it): not reachable.
    assert world.client(member).post(f'/api/studio/projects/{project_id}/voice',
                                     json={'recording_id': recording}).status_code == 404
    reference = world.client(owner).post(f'/api/studio/projects/{project_id}/voice',
                                         json={'recording_id': recording}).get_json()
    # The admin sees the reference, but not the title of a recording Speakr does not let them open.
    listed = world.client(admin).get(f'/api/studio/projects/{project_id}/voice').get_json()['items']
    assert [(i['id'], i['recording_title']) for i in listed] == [(reference['id'], None)]


def test_owner_details_hints_and_validation(world):
    owner = world.user()
    project_id = world.project(owner, 'Details film')
    client = world.client(owner)
    response = client.put(f'/api/studio/projects/{project_id}',
                          json={'logline': 'A heist.', 'notes': 'Shoot in May.', 'hotwords': 'Zed',
                                'initial_prompt': 'Two voices.'})
    assert response.status_code == 200
    body = response.get_json()
    assert (body['logline'], body['notes'], body['hotwords'], body['initial_prompt']) == (
        'A heist.', 'Shoot in May.', 'Zed', 'Two voices.')
    folder = db.session.get(Folder, body['folder_id'])
    assert folder.default_hotwords == 'Zed' and folder.default_initial_prompt == 'Two voices.'
    assert client.put(f'/api/studio/projects/{project_id}', json={'name': '   '}).status_code == 400
    assert client.put(f'/api/studio/projects/{project_id}', json={'notes': 5}).status_code == 400
    assert client.put(f'/api/studio/projects/{project_id}', json={'notes': 'x' * 20_001}).status_code == 400
    assert client.post('/api/studio/projects', json={'name': ''}).status_code == 400
    assert client.post('/api/studio/projects', json=['not', 'an', 'object']).status_code == 400


def test_the_project_pages_render(world):
    owner = world.user()
    project_id = world.project(owner, 'Rendered film')
    page = world.client(owner).get(f'/studio/projects/{project_id}')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '<title>Rendered film - Speakr</title>' in html
    assert 'data-studio-project' in html and f'projectId: {project_id}' in html
    assert 'js/studio/project.js' in html and 'js/app-dialog.js' in html
    listed = world.client(owner).get('/studio').get_data(as_text=True)
    assert 'data-studio-new-project' in listed


# --- schema steps -----------------------------------------------------------

def test_schema_steps_run_once_and_a_failed_step_runs_again():
    calls = []

    def add_column(engine):
        from src.utils.database import add_column_if_not_exists
        calls.append('add')
        add_column_if_not_exists(engine, 'studio_material', 'test_only_column', 'INTEGER')

    def broken(engine):
        calls.append('broken')
        raise RuntimeError('fails on purpose')

    with app.app_context():
        engine = db.engine
        try:
            assert schema.apply_schema_steps([('9001_test_add', add_column), ('9002_test_broken', broken)],
                                             engine=engine) == ['9001_test_add']
            assert 'test_only_column' in {c['name'] for c in inspect(engine).get_columns('studio_material')}
            assert schema.apply_schema_steps([('9001_test_add', add_column), ('9002_test_broken', broken)],
                                             engine=engine) == []
            assert calls == ['add', 'broken', 'broken']
        finally:
            from sqlalchemy import text
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM studio_schema_step WHERE step_id LIKE '900%_test_%'"))
                conn.commit()
            with engine.connect() as conn:
                try:
                    conn.execute(text('ALTER TABLE studio_material DROP COLUMN test_only_column'))
                    conn.commit()
                except Exception:
                    conn.rollback()


def test_no_studio_column_is_on_an_upstream_table():
    from src.models import Folder, Group, Recording, User
    for model in (Folder, Group, Recording, User):
        assert not [c.name for c in model.__table__.columns if c.name.startswith('studio')]
    studio_tables = {t for t in db.metadata.tables if t.startswith('studio_')}
    assert studio_tables == {'studio_project', 'studio_project_member', 'studio_material',
                             'studio_voice_reference', 'studio_project_visit', 'studio_schema_step',
                             'studio_admin_grant', 'studio_admin_project', 'studio_chat', 'studio_message',
                             'studio_setting'}
    with app.app_context():
        on_disk = set(inspect(db.engine).get_table_names())
    assert studio_tables <= on_disk
    assert StudioVoiceReference.__table__.c.recording_id.foreign_keys
