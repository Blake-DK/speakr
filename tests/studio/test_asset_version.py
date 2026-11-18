"""The asset version (src/studio/asset_version.py) and the project page's
markup that UI themes must not undo. Hermetic and offline."""

import os
import re
from html.parser import HTMLParser
from urllib.parse import quote

import pytest

from src.app import app
from src.config.version import get_version
from src.studio import asset_version as av

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_PAGE = os.path.join(ROOT, 'templates', 'studio', 'project.html')


def _tree(base, files):
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return str(base)


FILES = {'js/a.js': b'console.log(1);\n', 'css/b.css': b'body{}\n', 'locales/en.json': b'{}'}


def test_the_same_tree_gives_the_same_version(tmp_path):
    one = _tree(tmp_path / 'one', FILES)
    two = _tree(tmp_path / 'two', FILES)
    assert av.asset_version(one, 'v1.2.3') == av.asset_version(two, 'v1.2.3') == av.asset_version(one, 'v1.2.3')


def test_one_byte_changes_the_version(tmp_path):
    one = _tree(tmp_path / 'one', FILES)
    two = _tree(tmp_path / 'two', {**FILES, 'js/a.js': b'console.log(2);\n'})
    assert av.static_digest(one) != av.static_digest(two)


def test_a_renamed_or_added_file_changes_the_version(tmp_path):
    base = _tree(tmp_path / 'base', FILES)
    renamed = _tree(tmp_path / 'renamed', {'js/c.js' if k == 'js/a.js' else k: v for k, v in FILES.items()})
    added = _tree(tmp_path / 'added', {**FILES, 'img/x.png': b''})
    digests = {av.static_digest(base), av.static_digest(renamed), av.static_digest(added)}
    assert len(digests) == 3


def test_the_version_is_upstreams_followed_by_the_hash(tmp_path):
    version = av.asset_version(_tree(tmp_path / 's', FILES), 'v0.11.0')
    assert re.fullmatch(r'v0\.11\.0-[0-9a-f]{%d}' % av.DIGEST_LENGTH, version)


def test_an_unreadable_tree_falls_back_to_upstreams_version(tmp_path, monkeypatch):
    def refuse(_static_dir):
        raise PermissionError('denied')
    monkeypatch.setattr(av, 'static_digest', refuse)
    assert av.asset_version(str(tmp_path), 'v0.11.0') == 'v0.11.0'


def test_the_app_uses_the_hash_of_its_own_static_folder():
    import src.app as speakr_app
    expected = f'{get_version()}-{av.static_digest(app.static_folder)[:av.DIGEST_LENGTH]}'
    assert speakr_app._app_version == expected
    with app.test_request_context('/'):
        context = {}
        app.update_template_context(context)
    assert context['app_version'] == expected


def test_a_page_carries_the_asset_version():
    import src.app as speakr_app
    response = app.test_client().get('/login')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "service-worker-client.js?v=" + quote(speakr_app._app_version, safe='') in html


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _project_page_tags():
    parser = _Tags()
    with open(PROJECT_PAGE, encoding='utf-8') as handle:
        parser.feed(handle.read())
    return parser.tags


def test_every_file_chooser_is_hidden_with_important():
    """UI themes (cards, focus, workspace) give every input in the page
    display:block; only an !important display:none keeps a chooser hidden."""
    choosers = [attrs for tag, attrs in _project_page_tags() if tag == 'input' and attrs.get('type') == 'file']
    assert len(choosers) == 3
    for attrs in choosers:
        classes = (attrs.get('class') or '').split()
        assert '!hidden' in classes and 'hidden' not in classes, attrs


def test_the_project_name_wraps_only_when_it_does_not_fit():
    tags = _project_page_tags()
    button = next(attrs for tag, attrs in tags if 'data-studio-switcher-button' in attrs)
    name = next(attrs for tag, attrs in tags if 'data-studio-project-name' in attrs)
    assert 'max-w-full' not in button['class'].split()
    assert '[overflow-wrap:anywhere]' in name['class'].split()


COMPILED_CSS = os.path.join(ROOT, 'static', 'css', 'tailwind.css')


@pytest.mark.skipif(not os.path.isfile(COMPILED_CSS), reason='static/css/tailwind.css is compiled when the image is built')
def test_the_compiled_stylesheet_has_the_classes_the_page_needs():
    with open(COMPILED_CSS, encoding='utf-8') as handle:
        css = re.sub(r'\s+', '', handle.read())
    assert r'.\!hidden{display:none!important}' in css
    assert r'.\[overflow-wrap\:anywhere\]{overflow-wrap:anywhere}' in css
