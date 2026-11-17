"""The document exports: the standalone app's slide test ported, and a PDF that never fetches anything.

The slide tests need python-pptx and the PDF tests WeasyPrint (requirements-studio.txt, with the
system libraries of Dockerfile.studio); each is skipped without them, as upstream's test run does
not install them. The studio image's test run does.
"""

import io

import pytest

from src.studio import export

MD = """Intro line before any heading.

## The Hook
- One night. One town. **No way out.**
- Elevated horror, contained budget

## The Ask
Some plain paragraph line.
- £250k production budget
  - £50k already soft-committed
1. Numbered point

| a table | dropped |
|---------|---------|
| yes     | dropped |
"""


# --- slides: the standalone app's test ------------------------------------------------

def test_parse_slides():
    slides = export._parse_slides("My Film", MD)
    assert [s["title"] for s in slides] == ["My Film", "The Hook", "The Ask"]
    assert slides[0]["items"] == [(0, "Intro line before any heading.")]
    assert (0, "One night. One town. No way out.") in slides[1]["items"], "inline md must be stripped"
    assert (1, "£50k already soft-committed") in slides[2]["items"], "indent must become sub-bullet"
    assert (0, "Numbered point") in slides[2]["items"]
    assert not any("table" in t for s in slides for _, t in s["items"]), "tables must be dropped"


def test_to_pptx():
    pptx = pytest.importorskip("pptx")
    blob = export.to_pptx("My Film", MD)
    assert blob[:2] == b"PK", "pptx must be a zip"
    prs = pptx.Presentation(io.BytesIO(blob))
    assert len(prs.slides) == 3
    assert prs.slides[1].shapes.title.text == "The Hook"

    # A doc with no headings still produces usable chunked slides.
    prs2 = pptx.Presentation(io.BytesIO(export.to_pptx("Flat", "- a\n- b\n" * 8)))
    assert len(prs2.slides) == 4  # title + 16 bullets / 7 per slide


def test_an_exported_deck_reads_back_as_a_material_does():
    """The round trip: a deck the studio writes is read by the same code that reads an uploaded deck."""
    pytest.importorskip("pptx")
    import os
    import tempfile

    from src.studio.materials import pptx_text
    path = os.path.join(tempfile.mkdtemp(), "deck.pptx")
    with open(path, "wb") as fh:
        fh.write(export.to_pptx("My Film", MD))
    text = pptx_text(path, "deck.pptx")
    assert "--- Slide 2 ---" in text and "The Hook" in text
    assert "£250k production budget" in text
    assert 'slide deck "deck.pptx"' in text


def test_images_are_taken_out_of_every_conversion():
    body = 'See ![the poster](http://192.0.2.7/poster.png) and <img src="file:///etc/passwd" onerror="x()"> here.'
    assert export.strip_images(body) == 'See the poster and  here.'
    slides = export._parse_slides("T", "## A\n" + body)
    assert not any("192.0.2.7" in t or "passwd" in t for s in slides for _, t in s["items"])


def test_file_names():
    assert export.filenames("Pitch Deck: v2!", "pdf") == ("pitch-deck-v2.pdf", "pitch-deck-v2.pdf")
    assert export.filenames("", "md") == ("document.md", "document.md")
    assert export.filenames("../../etc/passwd", "md") == ("etc-passwd.md", "etc-passwd.md")
    name, fallback = export.filenames("Café Noir", "docx")
    assert name == "café-noir.docx" and fallback == "caf-noir.docx"


# --- the HTML that goes into a PDF ----------------------------------------------------

def test_the_html_allows_nothing_that_loads_or_runs():
    dirty = ('<p onclick="x()">Hi <a href="javascript:alert(1)">bad</a> <a href="https://example.com">ok</a></p>'
             '<script>alert(1)</script><style>body{background:url(file:///etc/passwd)}</style>'
             '<link rel="stylesheet" href="file:///etc/passwd"><iframe src="http://192.0.2.1/"></iframe>'
             '<img src="file:///etc/passwd"><svg><image href="file:///etc/passwd"/></svg>'
             '<p style="background:url(http://192.0.2.1/x)">styled</p><object data="file:///etc/passwd"></object>')
    clean = export.clean_html(dirty)
    for bad in ('<script', '<style', '<link', '<iframe', '<img', '<svg', '<image', '<object', 'onclick', 'style=',
                'javascript:', 'href="file:', 'src='):
        assert bad not in clean, bad
    assert '<a href="https://example.com">ok</a>' in clean


def test_markdown_tables_and_code_survive_the_cleaning():
    html = export.clean_html(export.markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n"))
    assert '<table>' in html and '<td>1</td>' in html and '<pre><code>code' in html


# --- PDF: rendered, and nothing fetched -------------------------------------------------

def test_a_pdf_is_rendered_and_a_document_asks_for_nothing():
    pytest.importorskip("weasyprint")
    fetcher = export._no_fetch_class()()
    body = (MD + '\n![x](file:///etc/passwd)\n<img src="file:///etc/passwd">\n'
            '<link rel="stylesheet" href="file:///etc/passwd">\n'
            '<style>body { background: url(file:///etc/passwd) }</style>\n'
            '<p style="background:url(http://192.0.2.1/x.png)">styled</p>\n')
    pdf = export.to_pdf("Pitch", body, fetcher=fetcher)
    assert pdf[:5] == b"%PDF-"
    assert fetcher.refused == [], "the cleaned document must not even ask for an address"


def test_the_fetcher_refuses_every_address_the_html_asks_for():
    """The second wall: HTML that still asks for files and addresses gets none of them."""
    pytest.importorskip("weasyprint")
    fetcher = export._no_fetch_class()()
    page = ('<html><head><link rel="stylesheet" href="file:///etc/passwd">'
            '<style>body { background: url(file:///etc/hostname) }</style></head>'
            '<body><p>Hi</p><img src="file:///etc/passwd"><img src="http://192.0.2.1/x.png"></body></html>')
    pdf = export.html_to_pdf(page, fetcher=fetcher)
    assert pdf[:5] == b"%PDF-"
    assert 'file:///etc/passwd' in fetcher.refused and 'http://192.0.2.1/x.png' in fetcher.refused
    assert 'file:///etc/hostname' in fetcher.refused


def test_the_default_fetcher_refuses_too():
    pytest.importorskip("weasyprint")
    fetcher = export._no_fetch_class()()
    with pytest.raises(ValueError):
        fetcher.fetch('file:///etc/passwd')
    with pytest.raises(ValueError):
        fetcher('https://example.com/')
