"""src/studio/rebrand.py on a copy of this repository's upstream files, as the
image build runs it; and the name, release and source link of
src/studio/brand.py. Hermetic and offline: nothing in the checkout is written.

tests/studio/test_served_name.py checks the image itself, as it is served.
"""

import json
import os
import re
import shutil

import pytest

from src.app import app
from src.studio import brand
from src.studio import rebrand as rb

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _copy(dest, rels=()):
    for top in ('templates', 'static/locales', 'static/js', 'static/css', 'static/img'):
        shutil.copytree(os.path.join(ROOT, top), os.path.join(dest, top))
    for rel in ('static/manifest.json', 'static/sw.js') + rb.PYTHON_FILES + tuple(rels):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), os.path.join(dest, rel))
    return str(dest)


@pytest.fixture(scope='module')
def image(tmp_path_factory):
    dest = _copy(tmp_path_factory.mktemp('image'))
    report = rb.rebrand(dest, name='ScriptBot')
    return dest, report


def _read(root, rel):
    with open(os.path.join(root, rel), encoding='utf-8') as handle:
        return handle.read()


@pytest.mark.parametrize('before, after', [
    ('Back to Speakr', 'Back to ScriptBot'),
    ("Speakr's upload limit.", "ScriptBot's upload limit."),
    ('<title>Offline - Speakr</title>', '<title>Offline - ScriptBot</title>'),
    ('{{title}} · Speakr', '{{title}} · ScriptBot'),
    ('artist: metadata.artist || \'Speakr\',', 'artist: metadata.artist || \'ScriptBot\','),
    ('a Speakr account.', 'a ScriptBot account.'),
    ('Ihr Speakr-Konto', 'Ihr ScriptBot-Konto'),
    ('die Speakr-Oberfläche', 'die ScriptBot-Oberfläche'),
    ('des Speakrs', 'des ScriptBots'),
])
def test_the_word_is_replaced_where_a_person_reads_it(before, after):
    assert rb.NAME_RE.sub('ScriptBot', before) == after


@pytest.mark.parametrize('text', [
    'window.SpeakrOrganizer', 'window.Speakr', 'SpeakrASRLanguages', 'Speakr-Signature: sha256=<hex>',
    'speakr_loopback', 'https://github.com/murtaza-nasir/speakr', 'X-Speakr-Event', 'Speakr.version', '$Speakr',
    'com.speakr.app', '/Speakr/', 'Speakr-Signature-V2', 'Speakr-Delivery-Id', "'Speakr-cache-'",
    'SpeakrFailedUploads', 'SpeakrRecordings',
])
def test_names_in_code_addresses_and_keys_are_left_alone(text):
    assert rb.NAME_RE.sub('ScriptBot', text) == text


def test_every_edit_matched_as_often_as_listed(image):
    _root, report = image
    assert len(report['edits']) == len(rb.EDITS)
    for edit, done in zip(rb.EDITS, report['edits']):
        assert done['files'] and all(count == edit.count for count in done['files'].values()), edit.why


def test_no_word_speakr_is_left_in_the_rewritten_files(image):
    root, report = image
    for rel in rb.name_files(root):
        assert not rb.NAME_RE.search(_read(root, rel)), rel
    # What is left is part of a longer name in code (window.SpeakrOrganizer, the IndexedDB names
    # SpeakrRecordings and SpeakrFailedUploads, the cache prefix Speakr-cache-) or a webhook header
    # name (Speakr-Signature), never the word a person reads.
    for rel, forms in report['left'].items():
        for form in forms:
            assert LEFTOVER.search(form), (rel, form)


LEFTOVER = re.compile(r'Speakr[A-Z][A-Za-z]*$|Speakr-(Signature|Attempt|Delivery|Event|Timestamp)\b|Speakr-cache-$')


def test_no_link_to_github_or_speakrs_documentation_is_left(image):
    root, _report = image
    for rel in rb.name_files(root):
        if rel.endswith('.py'):
            continue  # request headers to the providers keep upstream's address (src/studio/rebrand.py)
        text = _read(root, rel)
        assert 'github.com' not in text and 'github.io' not in text and 'hub.docker.com' not in text, rel


def test_the_about_tab_share_page_and_page_feet(image):
    root, _report = image
    account = _read(root, 'templates/account.html')
    assert account.count("{% include 'studio/about_licence.html' %}") == 1
    assert 'aboutPage.projectLinks' not in account and 'aboutPage.githubRepository' not in account
    assert account.count('`ScriptBot © {{ now.year }}`') == 1
    assert "textContent = {{ studio_release|tojson }} || data.version;" in account
    assert '`ScriptBot © {{ now.year }}`' in _read(root, 'templates/admin.html')
    share = _read(root, 'templates/share.html')
    assert share.count("{% include 'studio/share_foot.html' %}") == 1
    assert 'sharePage.builtWith' not in share and 'fa-github' not in share


def test_the_export_foot_has_the_name_and_no_link(image):
    root, _report = image
    for rel in sorted(os.listdir(os.path.join(root, 'static', 'locales'))):
        footer = json.loads(_read(root, f'static/locales/{rel}'))['exportLabels']['footer']
        assert 'ScriptBot' in footer and '](' not in footer, rel
    assert _read(root, 'src/file_exporter.py').count("'Generated with ScriptBot'") == 2


def test_the_locale_files_keep_their_keys(image):
    root, _report = image

    def keys(node, prefix=''):
        if isinstance(node, dict):
            out = set()
            for key, value in node.items():
                out |= {f'{prefix}{key}'} | keys(value, f'{prefix}{key}.')
            return out
        return set()

    for name in sorted(os.listdir(os.path.join(ROOT, 'static', 'locales'))):
        before = json.loads(_read(ROOT, f'static/locales/{name}'))
        after = json.loads(_read(root, f'static/locales/{name}'))
        assert keys(before) == keys(after), name


def test_the_manifest(image):
    root, _report = image
    before = json.loads(_read(ROOT, 'static/manifest.json'))
    after = json.loads(_read(root, 'static/manifest.json'))
    assert after['name'] == after['short_name'] == 'ScriptBot'
    assert 'Speakr' not in after['description']
    assert 'screenshots' not in after
    # The installed app stays the same app: its id and icons are where they were.
    assert after['id'] == before['id'] and after['icons'] == before['icons']


def test_the_icons_are_the_studios(image):
    root, report = image
    assert len(report['icons']) == len(rb.ICONS)
    for icon in rb.ICONS:
        with open(os.path.join(root, 'static', 'img', icon), 'rb') as ours, \
                open(os.path.join(rb.ICON_DIR, icon), 'rb') as expected:
            assert ours.read() == expected.read(), icon


def test_rewritten_templates_and_python_still_parse(image):
    root, report = image
    for rel in report['name_replaced']:
        text = _read(root, rel)
        if rel.endswith('.html'):
            app.jinja_env.parse(text)
        elif rel.endswith('.py'):
            compile(text, rel, 'exec')


def test_an_edit_that_no_longer_matches_stops_the_build(tmp_path):
    dest = _copy(tmp_path / 'image')
    path = os.path.join(dest, 'templates', 'share.html')
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text.replace('https://github.com/murtaza-nasir/speakr', 'https://example.com/speakr'))
    with pytest.raises(rb.RebrandError, match='Share page'):
        rb.rebrand(dest, name='ScriptBot')


def test_a_dry_run_writes_nothing(tmp_path):
    dest = _copy(tmp_path / 'image')
    before = _read(dest, 'templates/login.html')
    report = rb.rebrand(dest, name='ScriptBot', write=False)
    assert report['name_replaced']['templates/login.html'] > 0
    assert _read(dest, 'templates/login.html') == before
    assert not os.path.exists(os.path.join(dest, rb.REPORT_NAME))


def test_a_name_that_would_need_escaping_is_refused(tmp_path):
    with pytest.raises(rb.RebrandError):
        rb.rebrand(str(tmp_path), name='Script"Bot')


# --- src/studio/brand.py -----------------------------------------------------

def test_the_name_defaults_to_scriptbot(monkeypatch):
    monkeypatch.delenv('STUDIO_APP_NAME', raising=False)
    assert brand.app_name() == 'ScriptBot'
    monkeypatch.setenv('STUDIO_APP_NAME', 'Studio Notes')
    assert brand.app_name() == 'Studio Notes'
    monkeypatch.setenv('STUDIO_APP_NAME', '<b>x</b>')
    assert brand.app_name() == 'ScriptBot'


def test_the_source_link_points_at_the_release_that_runs(monkeypatch):
    monkeypatch.delenv('STUDIO_SOURCE_URL', raising=False)
    monkeypatch.setenv('STUDIO_RELEASE', '0.11.0-studio.16')
    assert brand.source_link() == 'https://github.com/Blake-DK/speakr/tree/v0.11.0-studio.16'
    monkeypatch.setenv('STUDIO_RELEASE', 'sha-abc1234')  # a build that is not a release
    assert brand.source_link() == brand.DEFAULT_SOURCE_URL
    monkeypatch.setenv('STUDIO_RELEASE', '0.11.0-studio.16')
    monkeypatch.setenv('STUDIO_SOURCE_URL', 'https://example.com/source.git')
    assert brand.source_link() == 'https://example.com/source.git'


def _render(template, **context):
    from flask import render_template
    with app.test_request_context('/'):
        return render_template(template, **context)


def test_the_licence_line(monkeypatch):
    monkeypatch.delenv('STUDIO_SOURCE_URL', raising=False)
    monkeypatch.delenv('STUDIO_APP_NAME', raising=False)
    monkeypatch.setenv('STUDIO_RELEASE', '0.11.0-studio.16')
    html = _render('studio/about_licence.html')
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.split('#}')[-1])).strip()
    assert text == ('ScriptBot is built on Speakr, free software under the GNU Affero General Public License v3. '
                    'Source code of this version .'
                    ' The administrators of this site can open and change every project and recording.')
    links = re.findall(r'href="([^"]+)"', html)
    assert links == ['https://github.com/Blake-DK/speakr/tree/v0.11.0-studio.16']


def test_the_share_foot_has_one_source_link_and_no_name(monkeypatch):
    monkeypatch.delenv('STUDIO_SOURCE_URL', raising=False)
    monkeypatch.setenv('STUDIO_RELEASE', '0.11.0-studio.16')
    html = _render('studio/share_foot.html')
    assert re.findall(r'href="([^"]+)"', html) == ['https://github.com/Blake-DK/speakr/tree/v0.11.0-studio.16']
    assert 'Speakr' not in html.split('#}')[-1]
    assert '>Source code</a>' in html


def test_the_clapperboard_keeps_its_source_and_licence():
    import hashlib
    folder = os.path.join(rb.ICON_DIR, 'noto-emoji')
    with open(os.path.join(folder, 'emoji_u1f3ac.png'), 'rb') as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    readme = _read(folder, 'README.md')
    assert digest in readme and 'v2026-09-24-unicode18_0' in readme and '2D/png/512/emoji_u1f3ac.png' in readme
    licence = _read(folder, 'LICENSE')
    assert licence.startswith('Copyright 2013 Google LLC') and 'SIL OPEN FONT LICENSE Version 1.1' in licence
