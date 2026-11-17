"""Downloads of a saved document: Markdown, Word, PDF and PowerPoint slides.

- Markdown: the document's text as it is saved.
- Word: upstream's own Markdown converter (src/services/document.py), the
  one Speakr's summary downloads use: the title as the heading, then the text.
- PDF: WeasyPrint, with the standalone app's style sheet. The Markdown
  becomes HTML (``markdown``), the HTML goes through ``bleach`` with an
  allow-list (no style, script, link, image or frame survives), and WeasyPrint
  renders it with a URL fetcher that refuses every address, ``file:``
  included. Nothing legitimate needs the network: the style is inline and the
  fonts are the system's.
- Slides: the standalone app's code, unchanged: every ``#`` or ``##`` heading
  starts a slide, list items become bullets (two or more leading spaces make
  a sub-bullet), other lines become plain paragraphs, tables are dropped.

Images are taken out of the text first in every format but Markdown
(``strip_images``): a converter would otherwise fetch their addresses while
rendering, and a document's text can hold any address, written by a model
or by a person.

WeasyPrint and its system libraries are installed by Dockerfile.studio; the
PDF export imports it on first use, so everything else works without it.
"""

import html
import re

FORMATS = ('md', 'docx', 'pdf', 'pptx')

MIMETYPES = {
    'md': 'text/markdown; charset=utf-8',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pdf': 'application/pdf',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}

_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_HTML_IMG = re.compile(r"<img[^>]*>", re.IGNORECASE)


def strip_images(body: str) -> str:
    """Replace images with their alt text. Converters would otherwise fetch
    image URLs during rendering — an SSRF vector, since document bodies can
    contain arbitrary URLs (written by a model or edited by a person)."""
    return _HTML_IMG.sub("", _MD_IMAGE.sub(r"\1", body))


PDF_CSS = """
@page { margin: 2.2cm; }
body { font-family: Georgia, serif; font-size: 11pt; line-height: 1.55; color: #1a1a1a; }
h1, h2, h3 { line-height: 1.25; }
h1 { font-size: 20pt; border-bottom: 1px solid #ccc; padding-bottom: 6pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 1px solid #999; padding: 4pt 8pt; text-align: left; font-size: 10pt; }
th { background: #f0ede6; }
code { background: #f2f0eb; padding: 1pt 3pt; font-size: 9.5pt; }
pre { background: #f2f0eb; padding: 8pt; font-size: 9.5pt; white-space: pre-wrap; }
a { color: #8a5a1c; }
blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 10pt; color: #444; }
"""

# What the Markdown converter makes, and nothing that loads or runs anything.
ALLOWED_TAGS = {
    'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 'u', 's', 'del', 'ins',
    'sub', 'sup', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'a', 'abbr',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
}
ALLOWED_ATTRIBUTES = {'a': ['href', 'title'], 'abbr': ['title'], 'ol': ['start'], 'th': ['align'], 'td': ['align']}
ALLOWED_PROTOCOLS = {'http', 'https', 'mailto'}


def markdown_to_html(body: str) -> str:
    import markdown as md_lib

    return md_lib.markdown(body, extensions=["tables", "fenced_code", "sane_lists"])


def clean_html(fragment: str) -> str:
    """The HTML with only ALLOWED_TAGS and their attributes; anything else is dropped, its text kept."""
    import bleach

    return bleach.clean(fragment, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES,
                        protocols=ALLOWED_PROTOCOLS, strip=True, strip_comments=True)


def _no_fetch_class():
    from weasyprint.urls import URLFetcher

    class NoFetch(URLFetcher):
        """Refuses every address: nothing is fetched while a PDF is rendered."""

        def __init__(self):
            super().__init__(allowed_protocols=(), allow_redirects=False)
            self.refused = []

        def fetch(self, url, headers=None):
            self.refused.append(url)
            raise ValueError('External resources are not loaded in exports')

    return NoFetch


def html_to_pdf(document_html: str, fetcher=None) -> bytes:
    """Render a whole HTML document as a PDF without fetching anything."""
    from weasyprint import HTML

    fetcher = fetcher or _no_fetch_class()()
    return HTML(string=document_html, base_url=None, url_fetcher=fetcher).write_pdf()


def pdf_document_html(title: str, body: str) -> str:
    return (
        f"<html><head><meta charset='utf-8'><style>{PDF_CSS}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>{clean_html(markdown_to_html(strip_images(body)))}</body></html>"
    )


def to_pdf(title: str, body: str, fetcher=None) -> bytes:
    return html_to_pdf(pdf_document_html(title, body), fetcher)


def _parse_slides(title: str, body: str) -> list[dict]:
    """Markdown -> slide dicts: every '#'/'##' heading starts a slide, list
    items become bullets (2+ leading spaces = sub-bullet), other lines become
    plain paragraphs. Table rows/rules are dropped — tables don't fit slides."""

    def clean(s: str) -> str:
        return re.sub(r"\*\*?|`+|^#+\s*", "", s).strip()

    slides = [{"title": title, "items": []}]
    for raw in strip_images(body).splitlines():
        s = raw.strip()
        if not s or s.startswith("|") or s.startswith("---"):
            continue
        if re.match(r"^#{1,2}\s", s):
            slides.append({"title": clean(s), "items": []})
            continue
        m = re.match(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)", raw)
        if m:
            slides[-1]["items"].append((1 if len(m.group(1)) >= 2 else 0, clean(m.group(2))))
        else:
            slides[-1]["items"].append((0, clean(s)))
    return slides


def to_pptx(title: str, body: str) -> bytes:
    import io

    from pptx import Presentation
    from pptx.util import Inches

    slides = _parse_slides(title, body)
    first, *rest = slides
    if not rest and first["items"]:
        # No headings to split on: chunk the content into slides of 7 bullets.
        items = first["items"]
        rest = [{"title": title, "items": items[i:i + 7]} for i in range(0, len(items), 7)]
        first = {"title": title, "items": []}

    prs = Presentation()
    prs.slide_width = Inches(13.333)  # 16:9
    prs.slide_height = Inches(7.5)

    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = first["title"]
    if first["items"]:
        slide.placeholders[1].text = "\n".join(t for _, t in first["items"][:4])

    for sd in rest:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = sd["title"]
        tf = slide.placeholders[1].text_frame
        tf.word_wrap = True
        for i, (level, text) in enumerate(sd["items"]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = text
            p.level = level

    # No text autofit or overflow handling: very long slides overflow visibly
    # and are fixed in PowerPoint, where the deck is opened anyway.
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def to_docx(title: str, body: str):
    """A python-docx Document: the title, then the text through upstream's converter."""
    from src.services.document import add_document_header, new_document, process_markdown_to_docx

    doc = new_document()
    add_document_header(doc, title, [])
    # No base address: a link to a path inside the app stays plain text.
    process_markdown_to_docx(doc, strip_images(body), None)
    return doc


# --- the download -----------------------------------------------------------

def filenames(title: str, ext: str):
    """(file name, ASCII fallback): the title's slug, letters of any script kept in the first."""
    name = re.sub(r"[^\w-]+", "-", title or "", flags=re.UNICODE).strip("-_").lower()[:80].strip("-_") or "document"
    fallback = re.sub(r"[^a-zA-Z0-9-]+", "-", title or "").strip("-").lower()[:80].strip("-") or "document"
    return f"{name}.{ext}", f"{fallback}.{ext}"


def _attachment(content, mimetype, filename, fallback):
    """content as a download named filename, with upstream's handling of names that are not ASCII."""
    from urllib.parse import quote

    from flask import make_response

    response = make_response(content)
    response.headers['Content-Type'] = mimetype
    try:
        filename.encode('ascii')
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        response.headers['Content-Disposition'] = (
            f'attachment; filename="{fallback}"; ' + "filename*=UTF-8''" + quote(filename, safe='')
        )
    return response


def download(document, fmt):
    """The document as a download in one of FORMATS. Raises ValueError for another format."""
    from src.services.document import docx_attachment_response, markdown_attachment_response

    if fmt not in FORMATS:
        raise ValueError(fmt)
    title = document.title or ''
    body = document.body or ''
    filename, fallback = filenames(title, fmt)
    if fmt == 'md':
        response = markdown_attachment_response(body, filename, fallback)
    elif fmt == 'docx':
        response = docx_attachment_response(to_docx(title, body), filename, fallback)
    elif fmt == 'pdf':
        response = _attachment(to_pdf(title, body), MIMETYPES['pdf'], filename, fallback)
    else:
        response = _attachment(to_pptx(title, body), MIMETYPES['pptx'], filename, fallback)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-cache'
    return response
