"""End-to-end check of the live backend through the real UI, run by GitHub Actions.

Two throwaway members (one on a phone viewport, one on a desktop viewport) join, give verdicts,
message each other, upload a photo, and the Tab is checked for the public record. Screenshots go to
ops/e2e/*.png, the verdict to ops/e2e/result.json, and both QA accounts plus their data and photos
are deleted afterwards via the service role, so production holds only real members.

Env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SITE (base url to test, default local server).
"""
import io
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

from playwright.sync_api import sync_playwright

SITE = os.environ.get("SITE", "http://127.0.0.1:8765/")
URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RUN = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
OUT = "ops/e2e"
os.makedirs(OUT, exist_ok=True)
result = {"run": RUN, "site": SITE, "steps": [], "pass": False, "started": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
qa = {
    "a": {"name": "QA Carol", "email": f"hn-qa-a-{RUN}@example.com", "pw": "negroni-" + RUN[-6:], "drink": "Negroni, test batch"},
    "b": {"name": "QA Thomas", "email": f"hn-qa-b-{RUN}@example.com", "pw": "highball-" + RUN[-6:], "drink": "Highball, test batch"},
}


def step(name, ok, detail=""):
    result["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL ") + name + (": " + str(detail)[:200] if detail else ""), flush=True)
    return ok


def admin(method, path, body=None, ok=(200, 201, 204)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("apikey", SERVICE)
    req.add_header("Authorization", f"Bearer {SERVICE}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]


def png(size=256, color=(200, 55, 30)):
    """A tiny valid PNG (solid colour square) to use as a QA avatar."""
    row = b"\x00" + bytes(color) * size
    raw = row * size

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def join(page, who):
    page.goto(SITE + "#join", wait_until="networkidle")
    page.wait_for_selector("#join-form:not([hidden])", timeout=15000)
    page.fill("#join-name", who["name"])
    page.fill("#join-drink", who["drink"])
    page.fill("#join-email", who["email"])
    page.fill("#join-password", who["pw"])
    page.click("#join-form button[type=submit]")
    page.wait_for_selector("#app:not([hidden]) #view-root h2", timeout=30000)


def wait_text(page, selector, text, timeout=15000):
    page.wait_for_function("([s, t]) => { const el = document.querySelector(s); return !!el && el.textContent.includes(t); }", arg=[selector, text], timeout=timeout)


cleanup_ids = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ca = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        cb = browser.new_context(viewport={"width": 1440, "height": 900})
        A, B = ca.new_page(), cb.new_page()
        errors = {"a": [], "b": []}
        A.on("pageerror", lambda e: errors["a"].append(str(e)))
        B.on("pageerror", lambda e: errors["b"].append(str(e)))

        # landing renders on both viewports
        A.goto(SITE, wait_until="networkidle")
        step("landing loads (mobile)", "Negroni" in A.text_content("h1"))
        A.screenshot(path=f"{OUT}/mobile-landing.png", full_page=True)
        B.goto(SITE, wait_until="networkidle")
        step("landing loads (desktop)", "Negroni" in B.text_content("h1"))
        B.screenshot(path=f"{OUT}/desktop-landing.png", full_page=True)
        step("not in demo mode", A.evaluate("() => !!(window.HN_CONFIG && window.HN_CONFIG.url)"), "config.js has a backend url")

        # join
        join(A, qa["a"])
        step("A joins and lands in the app", True)
        join(B, qa["b"])
        step("B joins and lands in the app", True)

        # both see each other (B reloads to be sure)
        B.goto(SITE + "#people", wait_until="networkidle")
        wait_text(B, "#view-root", qa["a"]["name"])
        step("B sees A under People", True)
        A.goto(SITE + "#people", wait_until="networkidle")
        wait_text(A, "#view-root", qa["b"]["name"])
        step("A sees B under People", True)

        # ids via the UI-independent route: admin list of users by email
        st, users = admin("GET", "/auth/v1/admin/users?per_page=200")
        ulist = users.get("users", []) if isinstance(users, dict) else []
        ids = {k: next((u["id"] for u in ulist if u.get("email") == v["email"]), None) for k, v in qa.items()}
        cleanup_ids = [i for i in ids.values() if i]
        step("admin can see both QA users", all(ids.values()), ids)

        # realtime: B watches the Tab while A gives a verdict
        B.goto(SITE + "#tab", wait_until="networkidle")
        B.wait_for_timeout(1500)
        A.click(f"[data-verdict=round][data-target='{ids['b']}']")
        wait_text(A, "#toast", "on the Tab", 10000)
        try:
            wait_text(B, "#view-root", f"{qa['a']['name']}", 12000)
            step("realtime: B's Tab updated without reload", True)
        except Exception as e:
            step("realtime: B's Tab updated without reload", False, "fell back to polling: " + str(e)[:80])
            B.reload(wait_until="networkidle")
            wait_text(B, "#view-root", qa["a"]["name"])
        line = B.text_content("#view-root")
        step("Tab shows who wanted another round with whom", (qa["a"]["name"] in line) and ("another round" in line) and (qa["b"]["name"] in line))

        # B calls it a night with A; A's people card tally updates after reload
        B.goto(SITE + "#people", wait_until="networkidle")
        B.click(f"[data-verdict=night][data-target='{ids['a']}']")
        wait_text(B, "#toast", "on the Tab", 10000)
        A.goto(SITE + "#tab", wait_until="networkidle")
        wait_text(A, "#view-root", "calling it a night")
        step("Tab shows the call-it-a-night verdict too", True)
        A.screenshot(path=f"{OUT}/mobile-tab.png", full_page=True)
        B.goto(SITE + "#tab", wait_until="networkidle")
        B.wait_for_timeout(800)
        B.screenshot(path=f"{OUT}/desktop-tab.png", full_page=True)

        # messaging both ways
        A.goto(SITE + f"#chats/{ids['b']}", wait_until="networkidle")
        A.fill("[data-composer] input", "Table for two at the usual place?")
        A.click("[data-composer] button")
        wait_text(A, "[data-bubbles]", "Table for two")
        step("A sends a message", True)
        B.goto(SITE + "#chats", wait_until="networkidle")
        wait_text(B, "#view-root", "Table for two", 20000)
        step("B sees A's message in the chat list", True)
        B.click(f"[data-open-chat='{ids['a']}']")
        wait_text(B, "[data-bubbles]", "Table for two")
        B.fill("[data-composer] input", "Only if you are buying the Negronis.")
        B.click("[data-composer] button")
        wait_text(B, "[data-bubbles]", "buying the Negronis")
        try:
            wait_text(A, "[data-bubbles]", "buying the Negronis", 12000)
            step("realtime: A receives B's reply without reload", True)
        except Exception as e:
            step("realtime: A receives B's reply without reload", False, str(e)[:80])
            A.reload(wait_until="networkidle")
            wait_text(A, "[data-bubbles]", "buying the Negronis")
        step("A sees B's reply", True)
        A.screenshot(path=f"{OUT}/mobile-chat.png")
        B.screenshot(path=f"{OUT}/desktop-chat.png")

        # privacy: a third party must not be able to read that thread (anon key, no session)
        req = urllib.request.Request(URL + "/rest/v1/messages?select=id", headers={"apikey": ANON, "Authorization": f"Bearer {ANON}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
                step("anonymous visitor cannot read messages", body == [], f"got {len(body)} rows")
        except urllib.error.HTTPError as e:
            step("anonymous visitor cannot read messages", e.code in (401, 403), f"http {e.code}")
        req = urllib.request.Request(URL + "/rest/v1/profiles?select=id", headers={"apikey": ANON, "Authorization": f"Bearer {ANON}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
                step("anonymous visitor cannot list members", body == [], f"got {len(body)} rows")
        except urllib.error.HTTPError as e:
            step("anonymous visitor cannot list members", e.code in (401, 403), f"http {e.code}")

        # profile edit + photo upload (A, mobile)
        A.goto(SITE + "#me", wait_until="networkidle")
        A.fill("#me-bio", "Here for the ham. Staying for the Tab.")
        A.click("[data-me-form] button[type=submit]")
        wait_text(A, "#toast", "Saved", 10000)
        step("A saves profile text", True)
        A.set_input_files("#avatar-file", {"name": "qa.png", "mimeType": "image/png", "buffer": png()})
        wait_text(A, "#toast", "New photo is up", 30000)
        A.wait_for_selector("[data-me-form] img.avatar", timeout=15000)
        step("A uploads a photo", True)
        A.screenshot(path=f"{OUT}/mobile-me.png", full_page=True)
        B.goto(SITE + "#people", wait_until="networkidle")
        B.wait_for_selector(f"[data-person='{ids['a']}'] .person-photo img", timeout=15000)
        src = B.get_attribute(f"[data-person='{ids['a']}'] .person-photo img", "src")
        step("B sees A's photo on the People card", bool(src and "/avatars/" in src), src)
        B.screenshot(path=f"{OUT}/desktop-people.png", full_page=True)
        A.goto(SITE + "#people", wait_until="networkidle")
        A.wait_for_timeout(800)
        A.screenshot(path=f"{OUT}/mobile-people.png", full_page=True)

        # person sheet + sign out
        B.click(f"[data-open-person='{ids['a']}']")
        wait_text(B, "#person-sheet", "Staying for the Tab", 8000)
        step("person sheet shows the bio", True)
        B.keyboard.press("Escape")
        B.goto(SITE + "#me", wait_until="networkidle")
        B.click("[data-signout]")
        B.wait_for_selector("#landing:not([hidden])", timeout=10000)
        step("sign out returns to the landing page", True)

        step("no page errors (A)", not errors["a"], errors["a"][:3])
        step("no page errors (B)", not errors["b"], errors["b"][:3])
        browser.close()
finally:
    # ---------------------------------------------------------------- cleanup: QA users, data and photos
    for uid in cleanup_ids:
        admin("DELETE", f"/storage/v1/object/avatars/{uid}/avatar.jpg", ok=(200, 204, 400, 404))
        st, body = admin("DELETE", f"/auth/v1/admin/users/{uid}")
        step(f"deleted QA user {uid[:8]}", st in (200, 204), st)
    st, left = admin("GET", "/rest/v1/profiles?select=id,display_name&display_name=like.QA%25")
    step("no QA profiles remain", isinstance(left, list) and len(left) == 0, left)
    st, left = admin("GET", "/rest/v1/verdicts?select=id&limit=1000")
    orphan = [r for r in (left if isinstance(left, list) else [])]
    st2, objs = admin("POST", "/storage/v1/object/list/avatars", {"prefix": "", "limit": 100})
    qa_objs = [o for o in (objs if isinstance(objs, list) else []) if any(o.get("name", "").startswith(uid) for uid in cleanup_ids)]
    step("no QA photos remain", len(qa_objs) == 0, [o.get("name") for o in qa_objs])
    result["pass"] = all(s["ok"] for s in result["steps"] if not s["step"].startswith("realtime"))
    result["realtime"] = all(s["ok"] for s in result["steps"] if s["step"].startswith("realtime"))
    result["finished"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    with open(f"{OUT}/result.json", "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({"pass": result["pass"], "realtime": result.get("realtime")}))
    sys.exit(0 if result["pass"] else 1)
