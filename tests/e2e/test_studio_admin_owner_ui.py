"""Browser test of "an administrator acts as the owner" (src/studio/admin_owner.py) on the
instance of the browser smoke test, with two made-up accounts: an owner with a recording in
no project and a project, and a third person with no tie to either.

The seeded administrator finds the recording under "From others" with its owner's name,
opens it, sees edit and delete but no folder, tag or speaker controls; changes its title and
notes, shares it inside the site, deletes it; and in the owner's project has every capability
of the owner and renames it. The audit log names the administrator as the actor and the owner
each time. The third person sees neither, before and after. Both accounts are deleted
afterwards. Runs only with SPEAKR_BROWSER_SMOKE=1; screenshots with SPEAKR_E2E_SHOTS.
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
from seed_recordings import write_tone  # noqa: E402
from smoke_env import read_example  # noqa: E402

BASE = os.environ.get("SPEAKR_BASE_URL", "http://127.0.0.1:8899").rstrip("/")
_example = read_example()
ADMIN_EMAIL = os.environ.get("SPEAKR_SMOKE_USER", _example.get("ADMIN_EMAIL", "admin@example.com"))
ADMIN_PASSWORD = os.environ.get("SPEAKR_SMOKE_PASSWORD", _example.get("ADMIN_PASSWORD", "changeme"))
SHOTS = os.environ.get("SPEAKR_E2E_SHOTS")
RUN = secrets.token_hex(3)


class Session:
    def __init__(self, browser, email, password):
        self.context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
        self.page = self.context.new_page()
        self.problems = PageProblems(self.page, BASE)
        login(self.page, BASE, email, password)
        self.problems.reset()

    def open(self, path):
        return open_page(self.page, self.problems, BASE, path)

    def token(self):
        if not self.page.url.startswith(BASE) or "/login" in self.page.url:
            self.open("/")
        return self.page.locator("meta[name=csrf-token]").first.get_attribute("content")

    def api(self, method, path, data=None, multipart=None, headers=None):
        return self.page.request.fetch(f"{BASE}{path}", method=method, headers={"X-CSRFToken": self.token(), **(headers or {})},
                                       data=data, multipart=multipart, timeout=120000)

    def shot(self, name):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            self.page.screenshot(path=os.path.join(SHOTS, f"{name}.png"))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        admin = Session(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
        admin.open("/admin")
        made = {}
        try:
            r = admin.api("POST", "/admin/settings", {"key": "enable_internal_sharing", "value": "true", "setting_type": "boolean"})
            assert r.ok, r.text()
            for who in ("owner", "third"):
                username = f"ao{RUN}{who}"
                user = {"username": username, "email": f"{username}@example.com", "password": secrets.token_urlsafe(16)}
                r = admin.api("POST", "/admin/users", user)
                assert r.status == 201, f"{who}: {r.status} {r.text()}"
                user["id"] = r.json()["id"]
                made[who] = user
            owner = Session(browser, made["owner"]["email"], made["owner"]["password"])
            third = Session(browser, made["third"]["email"], made["third"]["password"])
            audio = tmp_path_factory.mktemp("audio") / "made-up.wav"
            write_tone(str(audio), seconds=6)
            r = owner.api("POST", "/upload", multipart={"file": {"name": "made-up.wav", "mimeType": "audio/wav",
                                                                  "buffer": audio.read_bytes()}})
            assert r.ok, f"upload: {r.status} {r.text()[:200]}"
            body = r.json()
            recording_id = body.get("id") or (body.get("recording") or {}).get("id")
            r = owner.api("POST", "/api/studio/projects", {"name": f"Made-up film {RUN}"})
            assert r.status == 201, r.text()
            yield {"admin": admin, "owner": owner, "third": third, "users": made, "recording": recording_id,
                   "project": r.json()["id"]}
        finally:
            admin.open("/admin")
            for user in made.values():
                admin.api("DELETE", f"/admin/users/{user['id']}")
            admin.api("POST", "/admin/settings", {"key": "enable_internal_sharing", "value": "", "setting_type": "boolean"})
            browser.close()


def audit_rows(admin, event):
    admin.page.wait_for_timeout(2000)  # the log is written by a background thread
    r = admin.api("GET", "/admin/audit-log?limit=200")
    assert r.ok, r.text()
    return [row for row in r.json()["events"] if row.get("event") == event]


def test_the_third_person_sees_nothing(world):
    third = world["third"]
    assert third.api("GET", f"/api/recordings/{world['recording']}").status == 404
    assert third.api("GET", f"/api/studio/projects/{world['project']}").status == 404
    others = third.api("GET", "/api/recordings?per_page=100&shared=true").json()["recordings"]
    assert world["recording"] not in {r["id"] for r in others}


def test_the_admin_finds_the_recording_under_from_others_and_sees_the_owners_controls(world):
    admin, rid = world["admin"], world["recording"]
    rows = {r["id"]: r for r in admin.api("GET", "/api/recordings?per_page=100&shared=true").json()["recordings"]}
    assert rid in rows and rows[rid]["owner_username"] and rows[rid]["can_edit"] and rows[rid]["can_delete"]
    mine = {r["id"] for r in admin.api("GET", "/api/recordings?per_page=100").json()["recordings"]}
    assert rid not in mine  # the default list is unchanged
    admin.open(f"/recordings/{rid}")
    admin.page.wait_for_timeout(1500)
    page = admin.page
    assert page.evaluate("() => document.documentElement.classList.contains('studio-admin-as-owner')")
    for selector in (".relative:has(> [data-header-folder-toggle])", "button:has(> i.fa-tags):not(aside *)",
                     "button:has(> i.fa-user-tag):not(aside *)"):
        assert all(not el.is_visible() for el in page.locator(selector).all()), selector
    admin.shot("admin-on-someone-elses-recording")


def test_the_admin_changes_shares_and_deletes_and_the_log_names_both(world):
    admin, rid, owner_id = world["admin"], world["recording"], world["users"]["owner"]["id"]
    assert admin.api("POST", "/save", {"id": rid, "title": "Retitled by the administrator", "notes": "Notes by the administrator"}).ok
    seen = world["owner"].api("GET", f"/api/recordings/{rid}").json()
    seen = seen.get("recording", seen)
    assert seen["title"] == "Retitled by the administrator" and "Notes by the administrator" in (seen.get("notes") or "")
    r = admin.api("POST", f"/recording/{rid}/update_speakers", {"speaker_map": {"SPEAKER_00": "X"}})
    assert r.status == 403
    r = admin.api("POST", f"/api/recordings/{rid}/share-internal", {"user_ids": [world["users"]["third"]["id"]], "can_edit": False})
    assert r.ok, r.text()
    assert world["third"].api("GET", f"/api/recordings/{rid}").status == 200
    https = {"X-Forwarded-Proto": "https", "Referer": BASE.replace("http://", "https://") + "/"}
    r = admin.api("POST", f"/api/recording/{rid}/share", {"share_summary": True, "force_new": True}, headers=https)
    assert r.status in (403, 404), f"a public link by the administrator: {r.status} {r.text()[:200]}"
    assert admin.api("DELETE", f"/recording/{rid}").ok
    assert world["owner"].api("GET", f"/api/recordings/{rid}").status == 404
    edited = [row for row in audit_rows(admin, "recording.edited") if row.get("recording_id") == rid]
    deleted = [row for row in audit_rows(admin, "recording.deleted") if row.get("recording_id") == rid]
    for rows in (edited, deleted):
        assert rows and rows[0].get("recording_owner_id") == owner_id and rows[0].get("actor_user_id") != owner_id


def test_in_the_owners_project_the_admin_has_every_capability_and_it_is_said(world):
    admin, pid, owner_id = world["admin"], world["project"], world["users"]["owner"]["id"]
    detail = admin.api("GET", f"/api/studio/projects/{pid}").json()
    assert detail["role"] == "admin" and all(detail["can"].values()), detail["can"]
    admin.open(f"/studio/projects/{pid}")
    admin.page.locator("[data-studio-admin-note]").wait_for(state="visible", timeout=30000)
    assert "You can change everything in it that its owner can" in admin.page.locator("[data-studio-admin-note]").inner_text()
    admin.shot("admin-in-someone-elses-project")
    r = admin.api("PUT", f"/api/studio/projects/{pid}", {"name": f"Renamed by the administrator {RUN}"})
    assert r.ok, r.text()
    owner_view = world["owner"].api("GET", f"/api/studio/projects/{pid}").json()
    assert owner_view["role"] == "owner" and owner_view["name"].startswith("Renamed by the administrator")
    rows = [row for row in audit_rows(admin, "studio.project.changed") if (row.get("details") or {}).get("project_id") == pid]
    assert rows and rows[0].get("target_user_id") == owner_id
