"""The project page's lists and document editor at phone width: the markup
that keeps a title readable below the sm breakpoint and the editor's buttons
in view. Hermetic and offline: the template is read as a file."""

import os
import re
from html.parser import HTMLParser

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_PAGE = os.path.join(ROOT, 'templates', 'studio', 'project.html')
COMPILED_CSS = os.path.join(ROOT, 'static', 'css', 'tailwind.css')
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []

    @property
    def classes(self):
        return (self.attrs.get('class') or '').split()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, predicate):
        return [node for node in self.walk() if predicate(node)]

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Node('#root', {}, None)
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, dict(attrs), self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent


@pytest.fixture(scope='module')
def page():
    tree = _Tree()
    with open(PROJECT_PAGE, encoding='utf-8') as handle:
        tree.feed(handle.read())
    return tree.root


def _list_item(page, marker):
    lists = page.find(lambda n: marker in n.attrs)
    assert len(lists) == 1, marker
    items = [child for child in lists[0].children if child.tag == 'li']
    assert items, marker
    return items[0]


def test_a_recording_title_takes_the_whole_row_below_sm(page):
    item = _list_item(page, 'data-studio-recording-list')
    title = next(child for child in item.children if child.tag == 'a')
    assert {'grow', 'basis-full', 'sm:basis-0', 'min-w-0'} <= set(title.classes)
    assert 'flex-1' not in title.classes  # its basis of 0 squeezed the title beside the badge and button
    assert {'flex', 'flex-wrap'} <= set(item.classes)


@pytest.mark.parametrize('marker', ['data-studio-voice-list', 'data-studio-document-list'])
def test_the_buttons_of_a_row_go_under_the_title_below_sm(page, marker):
    item = _list_item(page, marker)
    buttons = [node for node in item.walk() if node.tag == 'div' and {'flex', 'flex-wrap', 'gap-2'} <= set(node.classes)
               and any(child.tag == 'button' for child in node.children)]
    assert len(buttons) == 1, marker
    assert {'w-full', 'sm:w-auto'} <= set(buttons[0].classes)


def test_the_editors_buttons_sit_outside_its_scrolling_body(page):
    editor = page.find(lambda n: 'data-studio-document-editor' in n.attrs)[0]
    form = editor.find(lambda n: n.tag == 'form')[0]
    assert 'modal-body' not in form.classes
    assert {'flex', 'flex-col', 'min-h-0'} <= set(form.classes)
    body = [child for child in form.children if 'modal-body' in child.classes]
    footer = [child for child in form.children if 'modal-footer' in child.classes]
    assert len(body) == 1 and len(footer) == 1
    save = page.find(lambda n: 'data-studio-document-save' in n.attrs)[0]
    assert footer[0] in list(save.ancestors()) and body[0] not in list(save.ancestors())
    textarea = body[0].find(lambda n: n.tag == 'textarea')[0]
    assert {'flex-auto', 'min-h-32'} <= set(textarea.classes)
    assert {'flex', 'flex-col', 'flex-auto', 'min-h-0'} <= set(textarea.parent.classes)


@pytest.mark.skipif(not os.path.isfile(COMPILED_CSS), reason='static/css/tailwind.css is compiled by scripts/build_css.sh or the image build')
def test_the_compiled_stylesheet_has_the_classes_for_phone_width():
    with open(COMPILED_CSS, encoding='utf-8') as handle:
        css = re.sub(r'\s+', '', handle.read())
    # The minifier may drop the unit of a zero (0px or 0).
    for rule in (r'\.basis-full\{flex-basis:100%\}', r'\.sm\\:basis-0\{flex-basis:0(px)?\}', r'\.sm\\:w-auto\{width:auto\}',
                 r'\.min-h-32\{min-height:8rem\}', r'\.flex-auto\{flex:11auto\}', r'\.min-h-0\{min-height:0(px)?\}'):
        assert re.search(rule, css), rule
