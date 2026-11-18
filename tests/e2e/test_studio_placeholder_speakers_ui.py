"""Browser test of the studio's "no placeholder speaker" (src/studio/speaker_view.py,
static/js/studio/speaker-view.js) on the instance of the browser smoke test.

The seeded meeting recording (seed_recordings.py) gets a made-up transcript whose
every segment is SPEAKER_00, as this site's transcriber writes them, and a
made-up translation. In a real browser:

- the transcript, the translation, the phone layout and two UI themes show no
  speaker anywhere, keep their times, and a click on a line seeks the player;
  the Follow player button is there; "Copy" copies no label;
- (the "Needs speakers" filter stays: the instance's other recordings have two voices;
  tests/studio/test_speaker_view.py covers when it hides)
- the segment editor offers no speaker, and one real edit and save keeps every
  segment's stored speaker and the segment count;
- the share page and the downloads show no label;
- the seeded transcript with two unnamed voices still shows both labels.

The seeded transcript is stored back afterwards and the translation emptied.
Runs only with SPEAKR_BROWSER_SMOKE=1 against a running Speakr at SPEAKR_BASE_URL.
With SPEAKR_E2E_SHOTS set, screenshots are saved in that directory.
"""

import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(os.environ.get("SPEAKR_BROWSER_SMOKE") != "1",
                                reason="browser test; set SPEAKR_BROWSER_SMOKE=1 with Speakr running")
playwright_api = pytest.importorskip("playwright.sync_api") if os.environ.get("SPEAKR_BROWSER_SMOKE") == "1" else None

from browser_checks import PageProblems, check_common, en, login, open_page  # noqa: E402
from seed_recordings import TITLES, meeting_segments  # noqa: E402
from smoke_env import read_example  # noqa: E402

BASE = os.environ.get("SPEAKR_BASE_URL", "http://127.0.0.1:8899").rstrip("/")
_example = read_example()
ADMIN_EMAIL = os.environ.get("SPEAKR_SMOKE_USER", _example.get("ADMIN_EMAIL", "admin@example.com"))
ADMIN_PASSWORD = os.environ.get("SPEAKR_SMOKE_PASSWORD", _example.get("ADMIN_PASSWORD", "changeme"))
SHOTS = os.environ.get("SPEAKR_E2E_SHOTS")
MEETING_TITLE = TITLES[1]
MEDIA = "document.getElementById('mainPlayerMedia') || document.querySelector('audio, video')"
SEGMENT = ".transcript-segment[data-start-time]:visible"


def placeholder_segments():
    """Twelve made-up segments, six seconds each, all of them SPEAKER_00."""
    return [{"speaker": "SPEAKER_00", "sentence": f"Made-up line number {i + 1}.", "start_time": i * 6.0, "end_time": i * 6.0 + 5.5}
            for i in range(12)]


def translated(segments):
    return [{**seg, "sentence": f"Oversat linje {i + 1}."} for i, seg in enumerate(segments)]


class Session:
    def __init__(self, browser, viewport):
        self.context = browser.new_context(viewport=viewport, locale="en-US")
        self.page = self.context.new_page()
        self.problems = PageProblems(self.page, BASE)
        login(self.page, BASE, ADMIN_EMAIL, ADMIN_PASSWORD)
        self.problems.reset()
        listing = self.page.request.get(f"{BASE}/api/recordings?per_page=50")
        items = listing.json().get("recordings", []) if listing.ok else []
        self.ids = {r["title"]: r["id"] for r in items if r.get("title") in TITLES}

    def close(self):
        self.context.close()

    def csrf(self):
        if not self.page.url.startswith(BASE) or "/login" in self.page.url:
            self.page.goto(f"{BASE}/")
            self.page.wait_for_load_state("load")
        return self.page.locator('meta[name="csrf-token"]').first.get_attribute("content")

    def post(self, path, body, headers=None):
        response = self.page.request.post(f"{BASE}{path}", headers={"X-CSRFToken": self.csrf(), "Content-Type": "application/json",
                                                                    **(headers or {})},
                                          data=json.dumps(body), timeout=120000)
        assert response.ok, f"{path}: HTTP {response.status} {response.text()[:200]}"
        return response

    def store(self, recording_id, transcription=None, translation=None):
        body = {}
        if transcription is not None:
            body["transcription"] = json.dumps(transcription)
        if translation is not None:
            body["transcription_translation"] = json.dumps(translation) if translation else ""
        self.post(f"/recording/{recording_id}/update_transcription", body)

    def stored(self, recording_id):
        response = self.page.request.get(f"{BASE}/api/recordings/{recording_id}", timeout=120000)
        assert response.ok
        return json.loads(response.json()["transcription"])

    def open(self, recording_id, phone=False):
        self.page.goto("about:blank")
        open_page(self.page, self.problems, BASE, f"/recordings/{recording_id}")
        if phone:
            tab = re.compile(rf"^\s*{re.escape(en('transcription.title'))}\s*$")
            self.page.locator("button:visible").filter(has_text=tab).first.click()
        self.page.locator(SEGMENT).first.wait_for(state="visible", timeout=60000)
        self.page.wait_for_timeout(800)

    def visible_text(self):
        return self.page.evaluate("() => document.body.innerText")

    def seek_check(self, index, seconds):
        self.page.locator(SEGMENT).nth(index).click()
        self.page.wait_for_function(f"() => {{ const m = {MEDIA}; return m && Math.abs(m.currentTime - {seconds}) < 0.6; }}", timeout=10000)

    def check(self, label):
        check_common(self.page, self.problems, label)

    def shot(self, name):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            self.page.screenshot(path=os.path.join(SHOTS, f"{name}.png"))


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def desk(browser):
    session = Session(browser, {"width": 1400, "height": 900})
    if MEETING_TITLE not in session.ids:
        session.close()
        pytest.skip("no seeded recordings on this instance (tests/e2e/seed_recordings.py)")
    rid = session.ids[MEETING_TITLE]
    session.store(rid, placeholder_segments(), translated(placeholder_segments()))
    yield session, rid
    session.store(rid, meeting_segments(), [])
    session.close()


def assert_no_label(session, where):
    text = session.visible_text()
    assert "SPEAKER_00" not in text, f"{where}: the placeholder is shown"
    assert all(not el.is_visible() for el in session.page.locator(f"button[title='{en('buttons.identifySpeakers')}']").all()), \
        f"{where}: Identify speakers is offered"


def test_the_transcript_shows_no_speaker_and_still_seeks(desk):
    session, rid = desk
    for theme in ("default", "cards", "desk"):
        session.page.goto(f"{BASE}/")
        session.page.evaluate("(id) => window.speakrApplyUiTheme && window.speakrApplyUiTheme(id)", theme)
        session.open(rid)
        assert session.page.locator(SEGMENT).count() == 12, theme
        assert_no_label(session, f"transcript, {theme}")
        assert session.page.locator(f"button[title='{en('tooltips.followPlayerDisabled')}']:visible, "
                                    f"button[title='{en('tooltips.followPlayerEnabled')}']:visible").count() >= 1, theme
        session.seek_check(4, 24.0)
        session.shot(f"placeholder-transcript-{theme}")
        session.check(f"transcript, {theme}")
    session.page.evaluate("() => window.speakrApplyUiTheme && window.speakrApplyUiTheme('default')")


def test_copy_copies_no_label(desk):
    session, rid = desk
    session.open(rid)
    session.page.evaluate("() => { window.__copied = null; navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); }; }")
    session.page.locator(f"button[title='{en('tooltips.copyTranscript')}']:visible").first.click()
    session.page.wait_for_function("() => window.__copied !== null", timeout=10000)
    copied = session.page.evaluate("() => window.__copied")
    assert copied.splitlines() == [s["sentence"] for s in placeholder_segments()]


def test_the_translation_shows_no_speaker(desk):
    session, rid = desk
    session.open(rid)
    session.page.locator("button.tab:visible").filter(has_text=en("translation.title")).first.click()
    session.page.get_by_text("Oversat linje 3.").first.wait_for(state="visible", timeout=30000)
    assert_no_label(session, "translation")
    session.seek_check(2, 12.0)
    session.shot("placeholder-translation")
    session.check("translation")


def test_the_editor_offers_no_speaker_and_a_save_keeps_them(desk):
    session, rid = desk
    before = session.stored(rid)
    session.open(rid)
    session.page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('speakr.asrDraft.')).forEach(k => localStorage.removeItem(k))")
    session.page.locator(f"button[title='{en('tooltips.editTranscript')}']:visible").first.click()
    rows = session.page.locator(".asr-seg")
    rows.first.wait_for(state="visible", timeout=60000)
    session.page.wait_for_timeout(1200)
    modal = session.page.locator(".asr-editor-modal")
    assert "SPEAKER_00" not in modal.inner_text()
    for selector in (".asr-seg__chip", "[data-asr-action='set-speaker']", ".asr-tl__lane [data-asr-action='lane-rename']",
                     ".asr-tl__lane [data-asr-lane-swatch]"):
        assert all(not el.is_visible() for el in session.page.locator(selector).all()), selector
    session.shot("placeholder-editor")
    box = session.page.locator(".asr-seg[data-index='1'] textarea")
    box.fill("Made-up line number 2, edited in the browser.")
    session.page.locator("[data-asr-action='save']:visible").first.click()
    session.page.locator(".asr-editor-modal .pill-status--success").wait_for(state="visible", timeout=60000)
    after = session.stored(rid)
    assert len(after) == len(before) == 12
    assert [s.get("speaker") for s in after] == ["SPEAKER_00"] * 12
    assert [s["sentence"] for s in after] == [s["sentence"] for s in before[:1]] + ["Made-up line number 2, edited in the browser."] + \
        [s["sentence"] for s in before[2:]]
    assert [(s["start_time"], s["end_time"]) for s in after] == [(s["start_time"], s["end_time"]) for s in before]
    session.page.keyboard.press("Escape")


def test_phone_width(browser, desk):
    _session, rid = desk
    phone = Session(browser, {"width": 390, "height": 844})
    try:
        phone.open(rid, phone=True)
        assert_no_label(phone, "phone")
        assert phone.page.locator("[data-phone-follow]:visible").count() == 1, "phone: no Follow player button"
        phone.seek_check(3, 18.0)
        phone.shot("placeholder-phone")
        phone.check("phone")
    finally:
        phone.close()


def test_the_share_page_and_the_downloads(browser, desk):
    session, rid = desk
    for fmt in ("txt", "md"):
        body = session.page.request.get(f"{BASE}/recording/{rid}/download/transcript?format={fmt}").text()
        assert "SPEAKER_00" not in body and "Made-up line number 1." in body, fmt
    # A link is made only over https; the instance of the browser tests is behind none (as test_share_page_ui.py).
    https = {"X-Forwarded-Proto": "https", "Referer": BASE.replace("http://", "https://") + "/"}
    shared = session.post(f"/api/recording/{rid}/share", {"share_summary": True, "share_notes": True, "force_new": True},
                          headers=https).json()
    url = BASE + "/share/" + shared["share_url"].rsplit("/share/", 1)[1]
    visitor = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
    try:
        page = visitor.new_page()
        page.goto(url)
        page.get_by_text("Made-up line number 5.").first.wait_for(state="visible", timeout=30000)
        text = page.evaluate("() => document.body.innerText")
        assert "SPEAKER_00" not in text
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, "placeholder-share.png"))
    finally:
        visitor.close()
        share_id = (shared.get("share") or {}).get("id")
        if share_id:
            session.page.request.delete(f"{BASE}/api/share/{share_id}", headers={"X-CSRFToken": session.csrf()})


def test_two_unnamed_voices_still_show_their_labels(desk):
    session, rid = desk
    session.store(rid, meeting_segments())
    try:
        session.page.goto("about:blank")
        open_page(session.page, session.problems, BASE, f"/recordings/{rid}")
        session.page.wait_for_timeout(1500)
        text = session.visible_text()
        assert "SPEAKER_00" in text and "SPEAKER_01" in text
        session.shot("two-voices")
    finally:
        session.store(rid, placeholder_segments())
