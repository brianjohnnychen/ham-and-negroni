"""Sets up the AI members: edge function, secrets, database config and the AI member accounts.

Idempotent. Run by GitHub Actions (provision.yml) after the schema is applied.
Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_PROJECT_REF, SUPABASE_ACCESS_TOKEN,
     ANTHROPIC_API_KEY (optional here; without it the AI members cannot reply and this run says so).
Writes ops/bots.log and ops/bots.json. Never prints secret values.
"""
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
REF = os.environ["SUPABASE_PROJECT_REF"]
TOKEN = os.environ["SUPABASE_ACCESS_TOKEN"]
ANTHROPIC = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MGMT = "https://api.supabase.com"

lines = []
result = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "anthropic_key_present": bool(ANTHROPIC), "function_deployed": False, "bots": 0, "ok": False}


def log(msg):
    msg = str(msg)
    for secret in (SERVICE, TOKEN, ANTHROPIC):
        if secret:
            msg = msg.replace(secret, "***")
    print(msg, flush=True)
    lines.append(msg)


def http(method, url, headers=None, body=None, raw=None, timeout=90):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode(errors="replace")
            try:
                return r.status, (json.loads(text) if text else None)
            except ValueError:
                return r.status, text
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text[:400]


def svc(method, path, body=None, extra=None):
    h = {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}", "Content-Type": "application/json"}
    h.update(extra or {})
    return http(method, URL + path, h, body)


def mgmt(method, path, body=None):
    return http(method, MGMT + path, {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}, body)


def finish(ok):
    result["ok"] = ok
    result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs("ops", exist_ok=True)
    open("ops/bots.log", "w").write("\n".join(lines) + "\n")
    json.dump(result, open("ops/bots.json", "w"), indent=2)
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------ database config (function url + shared secret)
st, rows = svc("GET", "/rest/v1/bot_config?select=key,value")
if st != 200:
    log(f"cannot read bot_config: http {st} {rows}")
    finish(False)
cfg = {r["key"]: r["value"] for r in rows}
bot_secret = cfg.get("secret") or secrets.token_urlsafe(32)
fn_url = f"{URL}/functions/v1/bot-reply"
for k, v in (("function_url", fn_url), ("secret", bot_secret)):
    st, res = svc("POST", "/rest/v1/bot_config?on_conflict=key", [{"key": k, "value": v}], {"Prefer": "resolution=merge-duplicates,return=minimal"})
    if st not in (200, 201, 204):
        log(f"cannot write bot_config {k}: http {st} {res}")
        finish(False)
log("bot_config ready")

# ------------------------------------------------------------------ edge function secrets
payload = [{"name": "BOT_SECRET", "value": bot_secret}]
if ANTHROPIC:
    payload.append({"name": "ANTHROPIC_API_KEY", "value": ANTHROPIC})
else:
    log("WARNING: ANTHROPIC_API_KEY is not set in the repo secrets; AI members will answer with a fallback line until it is.")
st, res = mgmt("POST", f"/v1/projects/{REF}/secrets", payload)
log(f"edge secrets: http {st}" + ("" if st in (200, 201) else f" {res}"))
if st not in (200, 201):
    finish(False)

# ------------------------------------------------------------------ deploy the edge function
def deploy_with_cli():
    env = dict(os.environ, SUPABASE_ACCESS_TOKEN=TOKEN)
    cmd = ["npx", "--yes", "supabase@latest", "functions", "deploy", "bot-reply", "--project-ref", REF, "--use-api", "--no-verify-jwt", "--yes"]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    log("cli deploy: exit " + str(p.returncode))
    log((p.stdout + p.stderr)[-1500:])
    return p.returncode == 0


def deploy_with_api():
    boundary = "----hn" + uuid.uuid4().hex
    src = open("supabase/functions/bot-reply/index.ts", "rb").read()
    meta = json.dumps({"entrypoint_path": "index.ts", "name": "bot-reply", "verify_jwt": False}).encode()
    parts = [
        (b'Content-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n', meta),
        (b'Content-Disposition: form-data; name="file"; filename="index.ts"\r\nContent-Type: application/typescript\r\n\r\n', src),
    ]
    body = b""
    for head, content in parts:
        body += b"--" + boundary.encode() + b"\r\n" + head + content + b"\r\n"
    body += b"--" + boundary.encode() + b"--\r\n"
    st, res = http("POST", f"{MGMT}/v1/projects/{REF}/functions/deploy?slug=bot-reply",
                   {"Authorization": f"Bearer {TOKEN}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, raw=body)
    log(f"api deploy: http {st} {str(res)[:300]}")
    return st in (200, 201)


try:
    deployed = deploy_with_api()
except Exception as e:  # noqa: BLE001
    log(f"api deploy raised: {e!r}")
    deployed = False
if not deployed:
    try:
        deployed = deploy_with_cli()
    except Exception as e:  # noqa: BLE001
        log(f"cli deploy raised: {e!r}")
result["function_deployed"] = bool(deployed)
if not deployed:
    log("edge function could not be deployed")
    finish(False)

# ------------------------------------------------------------------ the AI member accounts
personas = json.load(open("ops/bot_personas.json"))
st, plist = svc("GET", "/rest/v1/bot_personas?select=profile_id")
bot_ids = [r["profile_id"] for r in (plist if isinstance(plist, list) else [])]
existing = []
if bot_ids:
    st, existing = svc("GET", "/rest/v1/profiles?select=id,display_name&id=in.(" + ",".join(bot_ids) + ")")
by_name = {p["display_name"]: p for p in (existing if isinstance(existing, list) else [])}


def find_user_id(email):
    for page in range(1, 6):
        st, res = svc("GET", f"/auth/v1/admin/users?page={page}&per_page=200")
        users = res.get("users", []) if isinstance(res, dict) else []
        for u in users:
            if (u.get("email") or "").lower() == email.lower():
                return u["id"]
        if len(users) < 200:
            break
    return None


def upload_avatar(uid, path):
    """Puts a photo in the public avatars bucket where the app expects it and points the profile at it."""
    data = open(path, "rb").read()
    st, res = http("POST", f"{URL}/storage/v1/object/avatars/{uid}/avatar.jpg",
                   {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=3600"}, raw=data)
    if st not in (200, 201):
        return False, f"storage http {st} {str(res)[:200]}"
    version = hashlib.sha1(data).hexdigest()[:8]
    url = f"{URL}/storage/v1/object/public/avatars/{uid}/avatar.jpg?v={version}"
    st, res = svc("PATCH", f"/rest/v1/profiles?id=eq.{uid}", {"avatar_url": url}, {"Prefer": "return=minimal"})
    return st in (200, 204), f"profile http {st}"


count = 0
for p in personas:
    prof = by_name.get(p["name"])
    if not prof:
        email = f"bot.{p['slug']}@example.com"
        st, res = svc("POST", "/auth/v1/admin/users", {
            "email": email, "password": secrets.token_urlsafe(32), "email_confirm": True,
            "user_metadata": {"display_name": p["name"]}, "app_metadata": {"is_bot": True},
        })
        uid = res.get("id") if isinstance(res, dict) else None
        if not uid:
            uid = find_user_id(email)
        if not uid:
            log(f"could not create {p['name']}: http {st} {str(res)[:200]}")
            finish(False)
        # the profile is created by a trigger; make sure it exists
        for _ in range(10):
            st, rows = svc("GET", f"/rest/v1/profiles?select=id&id=eq.{uid}")
            if st == 200 and rows:
                break
            time.sleep(1)
        else:
            st, _ = svc("POST", "/rest/v1/profiles", {"id": uid, "display_name": p["name"]}, {"Prefer": "return=minimal"})
        prof = {"id": uid}
    st, res = svc("PATCH", f"/rest/v1/profiles?id=eq.{prof['id']}", {"display_name": p["name"], "bio": p["bio"]}, {"Prefer": "return=minimal"})
    if st not in (200, 204):
        log(f"cannot update profile {p['name']}: http {st} {res}")
        finish(False)
    st, res = svc("POST", "/rest/v1/bot_personas?on_conflict=profile_id", [{"profile_id": prof["id"], "persona": p["persona"], "greeting": p["greeting"]}], {"Prefer": "resolution=merge-duplicates,return=minimal"})
    if st not in (200, 201, 204):
        log(f"cannot write persona {p['name']}: http {st} {res}")
        finish(False)
    if p.get("avatar_file"):
        ok, detail = upload_avatar(prof["id"], p["avatar_file"])
        log(f"{p['name']}: photo " + ("set" if ok else "FAILED " + detail))
        if not ok:
            finish(False)
    count += 1
result["bots"] = count
log(f"AI members ready: {count}")
finish(True)
