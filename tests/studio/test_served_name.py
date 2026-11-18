"""What a person is served says ScriptBot, not Speakr, and links to GitHub only
where the licence asks for it: every page that can be rendered, every locale
file, the manifest, the service worker's offline page, the scripts and
stylesheets, an export and a mail, as the image serves them.

Runs only where src/studio/rebrand.py has rewritten the files (it leaves
studio-rebrand.json beside src/): inside the image, where the "Studio images"
workflow runs it with tests/ mounted into /app. In a checkout it is skipped;
tests/studio/test_rebrand.py checks the rewrite on a copy there.

Allowed, one by one: "Speakr" as part of a longer name in code
(window.SpeakrOrganizer) or of a header name (Speakr-Signature); the About
tab's licence line, which names Speakr once and links to the source code once;
the share page's one Source code link.
"""

import os
import re
import uuid

import pytest

from src.app import app, db
from src.models import Recording, Share, SystemSetting
from tests.studio.support import World

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__import__('src.app', fromlist=['app']).__file__)))
REBRANDED = os.path.isfile(os.path.join(ROOT, 'studio-rebrand.json'))

# STUDIO_SERVED_CHECK=1 (the workflow's step) makes a missing rewrite a failure, not a skip.
REQUIRED = os.environ.get('STUDIO_SERVED_CHECK') == '1'

pytestmark = pytest.mark.skipif(not REBRANDED and not REQUIRED, reason='the files are rewritten when the image is built')


def test_the_files_were_rewritten():
    assert REBRANDED, f'{ROOT}/studio-rebrand.json is missing: src/studio/rebrand.py did not run'

WORD = re.compile(r'Speakr')
# What may follow a "Speakr" that is part of a name in code: the rest of a CamelCase name
# (SpeakrOrganizer, SpeakrRecordings), a webhook header name, the worker's cache prefix.
ALLOWED_AFTER = re.compile(r'[A-Z][A-Za-z]|-(Signature|Attempt|Delivery|Event|Timestamp)\b|-cache-')
LINK = re.compile(r'github\.(com|io)|hub\.docker\.com')


def _offences(text, where, allowed_spans=()):
    """Each "Speakr" that is not part of a longer name, and each link to GitHub, outside ``allowed_spans``."""
    found = []
    for pattern in (WORD, LINK):
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in allowed_spans):
                continue
            if pattern is WORD:
                before = text[match.start() - 1] if match.start() else ''
                if before in ('.', '$') or ALLOWED_AFTER.match(text, match.end()):
                    continue  # window.Speakr, window.SpeakrOrganizer, Speakr-Signature, Speakr-cache-
            found.append(f'{where}: …{text[max(0, match.start() - 60):match.end() + 40]!r}…')
    return found


def _element_spans(html, marker):
    """The span of each element carrying ``marker`` (a data attribute), to its closing tag."""
    spans = []
    for match in re.finditer(r'<(\w+)[^>]*\b' + re.escape(marker) + r'\b[^>]*>', html):
        end = html.find(f'</{match.group(1)}>', match.end())
        spans.append((match.start(), end + len(match.group(1)) + 3))
    return spans


@pytest.fixture
def world():
    with app.app_context():
        w = World()
        try:
            yield w
        finally:
            db.session.rollback()
            Share.query.filter(Share.user_id.in_(w.users or [-1])).delete(synchronize_session=False)
            db.session.commit()
            w.cleanup()


def _pages(world):
    admin = world.user(admin=True)
    client = world.client(admin)
    project_id = world.project(admin, name='A film')
    recording_id = world.recording(admin, title='A recording')
    recording = db.session.get(Recording, recording_id)
    recording.transcription = 'Hello there.'
    recording.summary = '## Summary\n\nA made-up summary.'
    db.session.commit()
    share = Share(public_id=uuid.uuid4().hex, recording_id=recording_id, user_id=admin,
                  share_summary=True, share_notes=True)
    db.session.add(share)
    db.session.commit()
    signed_in = ['/', '/account', '/admin', '/group-management', '/inquire', '/studio', f'/studio/projects/{project_id}',
                 '/studio/settings', '/docs/transcript-templates-guide', '/api/v1/docs', f'/share/{share.public_id}']
    signed_out = ['/login', '/register', '/forgot-password', '/reset-password/not-a-token', '/verify-email/not-a-token']
    return client, world.client(), signed_in, signed_out, recording_id


def test_every_page_says_the_name_and_links_to_github_only_in_the_licence_line(world):
    client, anonymous, signed_in, signed_out, _recording_id = _pages(world)
    seen, offences = [], []
    for who, paths in ((client, signed_in), (anonymous, signed_out)):
        for path in paths:
            response = who.get(path, follow_redirects=True)
            if response.status_code != 200 or 'html' not in (response.content_type or ''):
                continue
            html = response.get_data(as_text=True)
            seen.append(path)
            allowed = _element_spans(html, 'data-studio-licence') + _element_spans(html, 'data-studio-share-foot')
            offences += _offences(html, path, allowed)
            title = re.search(r'<title>(.*?)</title>', html, re.S)
            if title and path not in ('/api/v1/docs', '/docs/transcript-templates-guide'):
                assert 'ScriptBot' in title.group(1) or '{{' in title.group(1), (path, title.group(1))
    assert offences == []
    for path in ('/', '/account', '/admin', '/studio', '/login', '/register'):
        assert path in seen


def test_the_about_tab_holds_the_name_the_release_and_one_licence_line(world, monkeypatch):
    monkeypatch.setenv('STUDIO_RELEASE', '0.11.0-studio.16')
    admin = world.user(admin=True)
    html = world.client(admin).get('/account').get_data(as_text=True)
    licence = _element_spans(html, 'data-studio-licence')
    assert len(licence) == 1
    line = html[licence[0][0]:licence[0][1]]
    assert line.count('Speakr') == 1
    assert re.findall(r'href="([^"]+)"', line) == ['https://github.com/Blake-DK/speakr/tree/v0.11.0-studio.16']
    assert '"0.11.0-studio.16" || data.version' in html
    assert '>ScriptBot</h2>' in html


def test_the_share_page_keeps_one_source_link(world):
    _client, anonymous, signed_in, _signed_out, _recording_id = _pages(world)
    share_path = next(path for path in signed_in if path.startswith('/share/'))
    html = anonymous.get(share_path).get_data(as_text=True)
    foot = _element_spans(html, 'data-studio-share-foot')
    assert len(foot) == 1
    assert len(re.findall(r'href="https://github\.com', html)) == 1
    assert _offences(html, share_path, foot) == []


def test_the_static_files_as_served(world):
    client = world.client()
    paths = ['/static/manifest.json', '/static/sw.js']
    for top in ('locales', 'js', 'css'):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, 'static', top)):
            for name in files:
                if name.endswith(('.json', '.js', '.css')) and not name.endswith('.test.js'):
                    paths.append('/' + os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, '/'))
    offences = []
    for path in paths:
        if path.startswith('/static/vendor/'):
            continue
        response = client.get(path)
        assert response.status_code == 200, path
        text = response.get_data(as_text=True)
        response.close()
        if path.endswith('.js'):
            # Comments are not shown to anyone; what a script shows is in its strings.
            text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
            text = re.sub(r'(?m)^\s*//.*$', '', text)
        offences += _offences(text, path)
    assert offences == []
    manifest = client.get('/static/manifest.json').get_json()
    assert manifest['name'] == manifest['short_name'] == 'ScriptBot' and 'screenshots' not in manifest
    offline = client.get('/static/sw.js').get_data(as_text=True)
    assert '<title>Offline - ScriptBot</title>' in offline and 'ScriptBot cannot be reached.' in offline


def test_an_export_and_a_mail(world):
    from src.file_exporter import generate_markdown_content
    from src.models import User
    from src.services import email
    _client, _anonymous, _signed_in, _signed_out, recording_id = _pages(world)
    recording = db.session.get(Recording, recording_id)
    with app.test_request_context('/'):
        markdown = generate_markdown_content(recording, db.session.get(User, recording.user_id))
        html, text = email._get_email_template('<p>Body</p>', 'Body', 'Subject')
    assert 'Generated with ScriptBot' in markdown
    for where, content in (('export', markdown), ('mail html', html), ('mail text', text)):
        assert _offences(content, where) == []
    assert '>ScriptBot</h1>' in html
    assert email.get_email_config()['from_name'] == 'ScriptBot' or os.environ.get('SMTP_FROM_NAME')


def test_whats_new_follows_the_setting(world):
    admin = world.user(admin=True)
    setting = SystemSetting.query.filter_by(key='show_whats_new').first()
    old = setting.value if setting else None
    try:
        if setting is None:
            setting = SystemSetting(key='show_whats_new', value='false', setting_type='boolean')
            db.session.add(setting)
        setting.value = 'false'
        db.session.commit()
        config = world.client(admin).get('/api/config').get_json()
        assert config['whats_new']['enabled'] is False
    finally:
        if old is None:
            db.session.delete(setting)
        else:
            setting.value = old
        db.session.commit()
