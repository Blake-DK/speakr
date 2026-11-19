"""Browser test: no studio text leaves its box and no two items of a list overlap
(STUDIO.md's pass, phase 5i), at about 1700, 1270 and 390 px wide.

On the instance of the browser smoke test the seeded administrator makes a made-up project
with seven documents whose long titles begin alike (as the context items of a real project
do), a chat, and the seeded meeting recording in the project's folder under a long title. In
the chat: the context bar shows every title in full without overlaps and scrolls inside
itself when tall; the recording, attached to the unsent message, shows its title in full; it
is taken off again and nothing is sent. Then the projects page and every tab of the project
page are swept for an element whose text is wider than its box. Everything made is deleted
and the recording's title and folder are put back.
Runs only with SPEAKR_BROWSER_SMOKE=1; screenshots with SPEAKR_E2E_SHOTS.
"""

import os
import secrets
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(os.environ.get("SPEAKR_BROWSER_SMOKE") != "1",
                                reason="browser test; set SPEAKR_BROWSER_SMOKE=1 with Speakr running")
playwright_api = pytest.importorskip("playwright.sync_api") if os.environ.get("SPEAKR_BROWSER_SMOKE") == "1" else None

from browser_checks import PageProblems, login, open_page  # noqa: E402
from seed_recordings import TITLES  # noqa: E402
from smoke_env import read_example  # noqa: E402

BASE = os.environ.get("SPEAKR_BASE_URL", "http://127.0.0.1:8899").rstrip("/")
_example = read_example()
ADMIN_EMAIL = os.environ.get("SPEAKR_SMOKE_USER", _example.get("ADMIN_EMAIL", "admin@example.com"))
ADMIN_PASSWORD = os.environ.get("SPEAKR_SMOKE_PASSWORD", _example.get("ADMIN_PASSWORD", "changeme"))
SHOTS = os.environ.get("SPEAKR_E2E_SHOTS")
RUN = secrets.token_hex(3)
PREFIX = "Script notes for the long documentary about the harbour, version "
LONG_RECORDING = "Interview with the harbour master about the night shift, the cranes and the ships that come in late, take two"

# Every element under <main> with text of its own that is wider than its box, unless an
# ancestor or the element itself cuts it off on purpose (overflow other than visible, or an ellipsis).
SPILLS = """() => {
    const out = [];
    for (const el of document.querySelectorAll('main *')) {
        if (!el.offsetParent) continue;
        if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
        const cs = getComputedStyle(el);
        if (cs.textOverflow === 'ellipsis' || cs.overflowX !== 'visible') continue;
        if (el.scrollWidth > el.clientWidth + 2) out.push((el.className || el.tagName).toString().slice(0, 60) + ' ' + el.clientWidth + '/' + el.scrollWidth);
    }
    return out;
}"""

# Pairs of items of one list whose boxes overlap.
OVERLAPS = """(selector) => {
    const boxes = [...document.querySelectorAll(selector)].filter((e) => e.offsetParent).map((e) => e.getBoundingClientRect());
    let n = 0;
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) n++;
    }
    return n;
}"""


class Admin:
    def __init__(self, browser):
        self.browser = browser
        self.context = browser.new_context(viewport={"width": 1700, "height": 1000}, locale="en-US")
        self.page = self.context.new_page()
        self.problems = PageProblems(self.page, BASE)
        login(self.page, BASE, ADMIN_EMAIL, ADMIN_PASSWORD)
        self.problems.reset()

    def api(self, method, path, data=None):
        if not self.page.url.startswith(BASE) or "/login" in self.page.url:
            open_page(self.page, self.problems, BASE, "/")
        token = self.page.locator("meta[name=csrf-token]").first.get_attribute("content")
        return self.page.request.fetch(f"{BASE}{path}", method=method, headers={"X-CSRFToken": token}, data=data, timeout=120000)


@pytest.fixture(scope="module")
def made():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        admin = Admin(browser)
        state = {}
        try:
            ids = {r["title"]: r for r in admin.api("GET", "/api/recordings?per_page=50").json().get("recordings", [])}
            if TITLES[1] not in ids:
                pytest.skip("no seeded recordings on this instance (tests/e2e/seed_recordings.py)")
            recording = ids[TITLES[1]]
            state.update(recording_id=recording["id"], old_title=recording["title"], old_folder=recording.get("folder_id"))
            assert admin.api("POST", "/admin/settings", {"key": "enable_folders", "value": "true", "setting_type": "boolean"}).ok
            r = admin.api("POST", "/api/studio/projects", {"name": f"Layout check {RUN}"})
            assert r.status == 201, r.text()
            state["project_id"] = pid = r.json()["id"]
            folder = admin.api("POST", f"/api/studio/projects/{pid}/recording-folder").json()["folder_id"]
            assert admin.api("PUT", f"/api/recordings/{state['recording_id']}/folder", {"folder_id": folder}).ok
            assert admin.api("POST", "/save", {"id": state["recording_id"], "title": LONG_RECORDING}).ok
            for n in range(1, 8):
                r = admin.api("POST", f"/api/studio/projects/{pid}/documents", {"title": f"{PREFIX}{n}: draft for the edit", "body": "Made up."})
                assert r.status == 201, r.text()
            r = admin.api("POST", f"/api/studio/projects/{pid}/chats", {})
            assert r.status == 201, r.text()
            state["chat_id"] = r.json()["id"]
            yield admin, state
        finally:
            if state.get("project_id"):
                admin.api("DELETE", f"/api/studio/projects/{state['project_id']}")
            if state.get("recording_id"):
                admin.api("POST", "/save", {"id": state["recording_id"], "title": state["old_title"]})
                if state.get("old_folder"):
                    admin.api("PUT", f"/api/recordings/{state['recording_id']}/folder", {"folder_id": state["old_folder"]})
                else:
                    admin.api("DELETE", f"/api/recordings/{state['recording_id']}/folder")
            admin.api("POST", "/admin/settings", {"key": "enable_folders", "value": "", "setting_type": "boolean"})
            browser.close()


@pytest.mark.parametrize("width", [1700, 1270, 390])
def test_the_chats_context_bar_and_an_attached_recording_read_in_full(made, width):
    admin, state = made
    page = admin.page
    page.set_viewport_size({"width": width, "height": 900})
    open_page(page, admin.problems, BASE, f"/studio/projects/{state['project_id']}")
    page.locator('[data-studio-tab="chats"]').click()
    page.locator("[data-studio-chat-items] li button").first.click()
    page.wait_for_timeout(1500)
    page.locator("button:has(> i.fa-brain):visible").first.click()
    items = page.locator("[data-studio-context-items] button.chip")
    items.first.wait_for(state="visible", timeout=30000)
    assert items.count() == 7
    for i in range(7):
        assert items.nth(i).evaluate("(b) => b.scrollWidth <= b.clientWidth + 2"), f"item {i} leaves its box at {width}"
    assert page.evaluate(OVERLAPS, "[data-studio-context-items] button.chip") == 0
    bar = page.locator("[data-studio-context-items]").evaluate("(b) => [b.getBoundingClientRect().height, b.scrollHeight, innerHeight]")
    assert bar[0] <= bar[2] * 0.34 + 1, f"the bar is {bar[0]} px of {bar[2]}"
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"context-bar-{width}.png"))
    # One long-titled recording attached to the unsent message, then taken off again
    page.locator("[data-studio-attach-button]").click()
    page.locator(f"label:has-text('{LONG_RECORDING[:40]}') input[type=checkbox]").first.check()
    attached = page.locator("[data-studio-attached-items] button.chip")
    attached.first.wait_for(state="visible", timeout=10000)
    assert attached.first.evaluate("(b) => b.scrollWidth <= b.clientWidth + 2"), f"the attached recording leaves its box at {width}"
    assert LONG_RECORDING in attached.first.inner_text()
    assert page.evaluate(SPILLS) == [], page.evaluate(SPILLS)
    attached.first.click()  # takes it off; nothing is sent
    assert page.locator("[data-studio-attached-items] button.chip").count() == 0


@pytest.mark.parametrize("width", [1700, 1270, 390])
def test_no_studio_text_leaves_its_box(made, width):
    admin, state = made
    page = admin.page
    page.set_viewport_size({"width": width, "height": 900})
    open_page(page, admin.problems, BASE, "/studio")
    assert page.evaluate(SPILLS) == [], ("projects", width, page.evaluate(SPILLS))
    open_page(page, admin.problems, BASE, f"/studio/projects/{state['project_id']}")
    for tab in ("details", "recordings", "materials", "voice", "chats", "documents", "people"):
        page.locator(f'[data-studio-tab="{tab}"]').click()
        page.wait_for_timeout(500)
        assert page.evaluate(SPILLS) == [], (tab, width, page.evaluate(SPILLS))
        for selector in ("[data-studio-recording-list] > li", "[data-studio-document-list] > li", "[data-studio-chat-items] > li"):
            assert page.evaluate(OVERLAPS, selector) == 0, (tab, width, selector)
