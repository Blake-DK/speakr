"""ScriptBot instead of Speakr, in the files of the image.

Run once by Dockerfile.studio over /app, as a file, so that nothing of the app
is imported:

    python -I src/studio/rebrand.py /app            rewrite in place, print the report and keep
                                                    it in /app/studio-rebrand.json
    python -I src/studio/rebrand.py --dry-run DIR   the same report, nothing written

Upstream's files in this repository stay as upstream wrote them: every upstream
release merges, and upstream's own tests keep reading the text they expect. The
rewrite runs over the copy in the image, so the asset version (a hash of
static/, src/studio/asset_version.py) changes with it, and the tests inside the
"Studio images" workflow see the files as they are served.

Two kinds of change:

* EDITS, below: the few places where something goes or changes shape (the About
  tab's links, the help link into Speakr's documentation, the share page's foot,
  the version line in page feet, the About tab's version, the export foot's
  link, the manifest's screenshots). Each must match exactly as often as listed,
  or the build stops: an upstream release that moves one of them is noticed
  instead of a link to GitHub coming back unseen.
* The name: the word Speakr where a person reads it, in upstream's templates,
  locale files, manifest, service worker, scripts and stylesheets, and in the
  few Python files that write text for people (mail, exports, the assistant's
  introduction, the API's documentation page, two error texts). Never part of a
  longer word or name (window.SpeakrOrganizer, Speakr-Signature,
  speakr_loopback), of an address (github.com/.../speakr) or of a key, which
  keep working as upstream wrote them.

The studio's own files (templates/studio/, static/js/studio/,
static/locales-studio/, src/studio/) are not rewritten; they take the name from
src/studio/brand.py. The icons in src/studio/icons/ replace Speakr's in
static/img/.
"""

import fnmatch
import json
import os
import re
import shutil
import sys

if __package__:
    from src.studio.brand import app_name, valid_name
else:  # run as a file: brand.py by its path, without src.studio's __init__ (which imports the app)
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        'studio_brand', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brand.py'))
    _brand = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_brand)
    app_name, valid_name = _brand.app_name, _brand.valid_name

REPORT_NAME = 'studio-rebrand.json'

# The word Speakr, not after a letter, digit, _, ., $, / or -: on its own (not before a
# letter, digit, _, - or a property ".x"); in a German compound noun (Speakr-Konto, but not
# the webhook header names Speakr-Signature and the like, nor Speakr-cache-); in the German
# genitive (Speakrs). The compound and the genitive keep their ending: ScriptBot-Konto.
HEADER_WORDS = ('Signature', 'Attempt', 'Delivery', 'Event', 'Timestamp')
NAME_RE = re.compile(
    r'(?<![\w.$/-])Speakr'
    r'(?:(?![\w-])(?!\.\w)'
    r'|(?=-[A-ZÄÖÜ])(?!-(?:' + '|'.join(HEADER_WORDS) + r')\b)'
    r'|(?=s\b))')
ANY_RE = re.compile(r'Speakr')

# Upstream's Python files that write text for people. The others that name
# Speakr do so in request headers to the model and speech providers (User-Agent,
# X-Title, HTTP-Referer), webhook header names, a calendar file's PRODID and log
# lines: nobody reads them in the app, and they stay as upstream wrote them.
PYTHON_FILES = (
    'src/services/email.py',
    'src/utils/assistant_name.py',
    'src/api/api_v1.py',
    'src/api/auth.py',
    'src/services/voice_embedding_check.py',
    'src/file_exporter.py',
    'src/utils/localization.py',
)

_OURS = ('templates/studio/', 'static/js/studio/')


def name_files(root):
    """The files the name is replaced in, relative to ``root``, sorted."""
    found = []
    for top, pattern in (('templates', '*.html'), ('static/locales', '*.json'), ('static/js', '*.js'),
                         ('static/css', '*.css')):
        for dirpath, _dirs, files in os.walk(os.path.join(root, top)):
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, '/')
                if fnmatch.fnmatch(name, pattern) and not rel.startswith(_OURS) and not name.endswith('.test.js'):
                    found.append(rel)
    for rel in ('static/manifest.json', 'static/sw.js') + PYTHON_FILES:
        if os.path.isfile(os.path.join(root, rel)):
            found.append(rel)
    return sorted(set(found))


class Edit:
    """One change of shape: ``pattern`` in the files ``where`` (paths or globs), replaced by ``new``
    (``{name}`` is the app's name), exactly ``count`` times in each file."""

    def __init__(self, why, where, pattern, new, count=1):
        self.why, self.where, self.new, self.count = why, where, new, count
        self.pattern = re.compile(pattern, re.S)

    def files(self, root):
        out = []
        for spec in self.where:
            if any(ch in spec for ch in '*?['):
                folder = os.path.dirname(spec)
                names = sorted(os.listdir(os.path.join(root, folder))) if os.path.isdir(os.path.join(root, folder)) else []
                matched = [f'{folder}/{n}' for n in names if fnmatch.fnmatch(n, os.path.basename(spec))]
                if not matched:
                    raise RebrandError(f'{self.why}: no file matches {spec}')
                out.extend(matched)
            else:
                out.append(spec)
        return out


class RebrandError(Exception):
    pass


EDITS = (
    Edit("About: Speakr's GitHub, Docker Hub and documentation links give way to the licence line",
         ['templates/account.html'],
         r'<!-- Project Links -->\s*<div class="bg-\[var\(--bg-secondary\)\][^"]*">\s*'
         r'<h3[^>]*data-i18n="aboutPage\.projectLinks"[^>]*>.*?</h3>\s*<div class="grid[^"]*">.*?</a>\s*</div>\s*</div>',
         "{% include 'studio/about_licence.html' %}"),
    Edit("Transcript templates: the help link into Speakr's documentation site goes",
         ['templates/account.html'],
         r'<!-- Documentation Link -->\s*<div class="mt-8 text-center">\s*'
         r'<a href="https://murtaza-nasir\.github\.io/speakr/user-guide/transcript-templates/".*?</a>\s*</div>',
         ''),
    Edit("Page feet: Speakr's version goes from the version line",
         ['templates/account.html', 'templates/admin.html'],
         r'`Speakr \$\{data\.version\} © \{\{ now\.year \}\}`',
         '`{name} © {{ now.year }}`'),
    Edit('About: the studio release that runs instead of Speakr\'s version',
         ['templates/account.html'],
         r"document\.getElementById\('system-version'\)\.textContent = data\.version;",
         "document.getElementById('system-version').textContent = {{ studio_release|tojson }} || data.version;"),
    Edit("Share page: the foot holds one small Source code link and nothing else of Speakr's",
         ['templates/share.html'],
         r'(<footer class="[^"]*">)\s*<div class="max-w-4xl mx-auto flex[^"]*">\s*<div class="flex items-center gap-2">\s*'
         r'<span>&copy; \{\{ now\.year \}\} Speakr</span>.*?href="https://github\.com/murtaza-nasir/speakr".*?</a>\s*</div>\s*</div>\s*(</footer>)',
         "\\1{% include 'studio/share_foot.html' %}\\2"),
    Edit('Exports: "Generated with" the name, without the link to GitHub',
         ['static/locales/*.json', 'src/utils/localization.py'],
         r'\[Speakr\]\(https://github\.com/(?:learnedmachine|murtaza-nasir)/speakr\)',
         '{name}'),
    Edit('Exports: the same foot where no locale file has one',
         ['src/file_exporter.py'],
         r'\[Speakr\]\(https://github\.com/(?:learnedmachine|murtaza-nasir)/speakr\)',
         '{name}', count=2),
    Edit("Manifest: Speakr's screenshots are not offered when the app is installed",
         ['static/manifest.json'],
         r',\s*"screenshots"\s*:\s*\[.*?\]',
         ''),
)

ICONS = ('favicon.ico', 'icon-16x16.png', 'icon-32x32.png', 'icon-64.png', 'icon-128.png', 'icon-180x180.png',
         'icon-192x192.png', 'icon-512x512.png', 'icon-maskable-512x512.png')
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _left(text):
    """What is left of "Speakr" after the rewrite, by form (window.SpeakrOrganizer, Speakr-Signature, ...)."""
    forms = {}
    for match in ANY_RE.finditer(text):
        start, end = match.start(), match.end()
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] in '_.$'):
            start -= 1
        while end < len(text) and (text[end].isalnum() or text[end] in '_-'):
            end += 1
        form = text[start:end]
        forms[form] = forms.get(form, 0) + 1
    return forms


def rebrand(root, name=None, write=True):
    """Rewrite the tree at ``root``; returns the report. Raises RebrandError when an edit does not match."""
    name = name or app_name()
    if not valid_name(name):
        raise RebrandError(f'not a plain name: {name!r}')
    texts = {}

    def text(rel):
        if rel not in texts:
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                raise RebrandError(f'missing: {rel}')
            texts[rel] = _read(path)
        return texts[rel]

    report = {'name': name, 'edits': [], 'name_replaced': {}, 'left': {}, 'icons': []}
    for edit in EDITS:
        counts = {}
        for rel in edit.files(root):
            new_text, count = edit.pattern.subn(edit.new.replace('{name}', name), text(rel))
            if count != edit.count:
                raise RebrandError(f'{edit.why}: {rel} matched {count} times, expected {edit.count}')
            texts[rel] = new_text
            counts[rel] = count
        report['edits'].append({'why': edit.why, 'files': counts})

    for rel in name_files(root):
        new_text, count = NAME_RE.subn(name, text(rel))
        texts[rel] = new_text
        if count:
            report['name_replaced'][rel] = count
        left = _left(new_text)
        if left:
            report['left'][rel] = left

    for rel in ('static/manifest.json',) + tuple(r for r in texts if r.startswith('static/locales/')):
        try:
            json.loads(texts[rel])
        except ValueError as error:
            raise RebrandError(f'{rel} is no longer valid JSON: {error}')

    upstream_icons = sorted(n for n in os.listdir(os.path.join(root, 'static', 'img'))
                            if n == 'favicon.ico' or fnmatch.fnmatch(n, 'icon-*.png'))
    if upstream_icons != sorted(ICONS):
        raise RebrandError(f'static/img has {upstream_icons}, the studio replaces {sorted(ICONS)}')
    for icon in ICONS:
        if not os.path.isfile(os.path.join(ICON_DIR, icon)):
            raise RebrandError(f'missing src/studio/icons/{icon}')
    report['icons'] = [f'static/img/{icon}' for icon in ICONS]

    if write:
        for rel, new_text in texts.items():
            if new_text != _read(os.path.join(root, rel)):
                with open(os.path.join(root, rel), 'w', encoding='utf-8') as handle:
                    handle.write(new_text)
        for icon in ICONS:
            shutil.copyfile(os.path.join(ICON_DIR, icon), os.path.join(root, 'static', 'img', icon))
        with open(os.path.join(root, REPORT_NAME), 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=1, sort_keys=True)
    return report


def main(argv):
    write = '--dry-run' not in argv
    args = [a for a in argv if a != '--dry-run']
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        report = rebrand(args[0], write=write)
    except RebrandError as error:
        print(f'rebrand: {error}', file=sys.stderr)
        return 1
    total = sum(report['name_replaced'].values())
    print(f"rebrand: the name {report['name']!r} in {len(report['name_replaced'])} files ({total} places); "
          f"{len(report['edits'])} edits; {len(report['icons'])} icons" + ('' if write else ' (dry run)'))
    for edit in report['edits']:
        print(f"  edit: {edit['why']}: " + ', '.join(f'{k} {v}' for k, v in edit['files'].items()))
    for rel, count in report['name_replaced'].items():
        print(f'  name: {rel} {count}')
    for rel, forms in report['left'].items():
        print(f'  left: {rel} ' + ', '.join(f'{k} {v}' for k, v in sorted(forms.items())))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
