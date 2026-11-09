"""Provision the Ham & Negroni backend from GitHub Actions.

Idempotent. Needs env: SUPABASE_ACCESS_TOKEN (account token), GH_PAT (repo admin), GITHUB_REPOSITORY.
Creates (or finds) a FREE Supabase organization and project, applies supabase/schema.sql, turns off
email confirmation, stores keys as repo secrets, writes site/config.js and ops/infra.json.
Never prints secret values.
"""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.supabase.com"
TOKEN = os.environ["SUPABASE_ACCESS_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]  # owner/name
OWNER, NAME = REPO.split("/")
ORG_NAME = os.environ.get("HN_ORG_NAME", "Meteor City Sandbox")
PROJECT_NAME = os.environ.get("HN_PROJECT_NAME", "ham-and-negroni")
REGION = os.environ.get("HN_REGION", "ap-northeast-1")
LEGACY_URL = f"https://{OWNER}.github.io/{NAME}/"
_cname = os.path.join("site", "CNAME")  # custom domain, single source of truth (see ops/domain.py)
DOMAIN = open(_cname).read().strip() if os.path.exists(_cname) else ""
SITE_URL = f"https://{DOMAIN}/" if DOMAIN else LEGACY_URL
LOG = []


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    LOG.append(line)


def call(method, path, body=None, ok=(200, 201, 204), base=API, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw[:800]
        if e.code in ok:
            return e.code, parsed
        return e.code, parsed


def must(status, body, what, ok=(200, 201, 204)):
    if status not in ok:
        log(f"FAILED {what}: http {status} {json.dumps(body)[:800] if not isinstance(body, str) else body}")
        finish(False)
    return body


def finish(success):
    os.makedirs("ops", exist_ok=True)
    with open("ops/provision.log", "w") as f:
        f.write("\n".join(LOG) + "\n")
    sys.exit(0 if success else 1)


def gh_secret(name, value):
    r = subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value, text=True, capture_output=True,
                       env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]})
    log(f"secret {name}: {'set' if r.returncode == 0 else 'FAILED ' + r.stderr.strip()[:200]}")


# ------------------------------------------------------------------ organization (free tier)
status, orgs = call("GET", "/v1/organizations")
must(status, orgs, "list organizations")
org = next((o for o in orgs if o.get("name") == ORG_NAME), None)
if org:
    log(f"org found: {ORG_NAME} ({org['id']})")
else:
    status, org = call("POST", "/v1/organizations", {"name": ORG_NAME})
    must(status, org, "create free organization")
    log(f"org created: {ORG_NAME} ({org['id']})")
ORG_ID = org["id"]

# ------------------------------------------------------------------ project
status, projects = call("GET", "/v1/projects")
must(status, projects, "list projects")
proj = next((p for p in projects if p.get("name") == PROJECT_NAME and p.get("organization_id") == ORG_ID), None)
db_pass = None
if proj:
    log(f"project found: {proj['id']} status={proj.get('status')}")
else:
    db_pass = secrets.token_urlsafe(24)
    status, proj = call("POST", "/v1/projects", {
        "name": PROJECT_NAME, "organization_id": ORG_ID, "db_pass": db_pass, "region": REGION,
    })
    must(status, proj, "create project")
    log(f"project created: {proj['id']} region={REGION}")
    gh_secret("SUPABASE_DB_PASSWORD", db_pass)
REF = proj["id"]
URL = f"https://{REF}.supabase.co"

# ------------------------------------------------------------------ wait until healthy
deadline = time.time() + 900
while True:
    status, p = call("GET", f"/v1/projects/{REF}")
    st = (p or {}).get("status") if status == 200 else f"http {status}"
    if st == "ACTIVE_HEALTHY":
        log("project is ACTIVE_HEALTHY")
        break
    if time.time() > deadline:
        log(f"FAILED waiting for project, last status {st}")
        finish(False)
    log(f"waiting, status={st}")
    time.sleep(15)

# ------------------------------------------------------------------ schema
schema = open("supabase/schema.sql").read()
for attempt in range(1, 7):
    status, res = call("POST", f"/v1/projects/{REF}/database/query", {"query": schema})
    if status in (200, 201):
        log("schema applied")
        break
    log(f"schema attempt {attempt}: http {status} {json.dumps(res)[:400] if not isinstance(res, str) else res[:400]}")
    if attempt == 6:
        finish(False)
    time.sleep(20)

# sanity: tables present
status, res = call("POST", f"/v1/projects/{REF}/database/query", {
    "query": "select table_name from information_schema.tables where table_schema='public' order by 1"})
log(f"public tables: {[r.get('table_name') for r in res] if isinstance(res, list) else res}")

# ------------------------------------------------------------------ auth settings
status, res = call("PATCH", f"/v1/projects/{REF}/config/auth", {
    "site_url": SITE_URL,
    "uri_allow_list": f"{SITE_URL},{SITE_URL}*,{LEGACY_URL},{LEGACY_URL}*,http://localhost:8765/*,http://127.0.0.1:8765/*",
    "mailer_autoconfirm": True,
    "external_email_enabled": True,
    "disable_signup": False,
    "password_min_length": 6,
})
must(status, res, "auth config")
log("auth: email+password on, confirmation off, site_url set")

# ------------------------------------------------------------------ api keys
status, keys = call("GET", f"/v1/projects/{REF}/api-keys?reveal=true")
must(status, keys, "api keys")
anon = next((k for k in keys if k.get("name") == "anon"), None) or next((k for k in keys if k.get("type") == "publishable"), None)
service = next((k for k in keys if k.get("name") == "service_role"), None) or next((k for k in keys if k.get("type") == "secret"), None)
if not anon:
    log(f"FAILED: no anon/publishable key among {[k.get('name') for k in keys]}")
    finish(False)
log(f"keys: using '{anon.get('name')}' for the site" + (f", '{service.get('name')}' stored as secret" if service else ""))
gh_secret("SUPABASE_URL", URL)
gh_secret("SUPABASE_PROJECT_REF", REF)
gh_secret("SUPABASE_ANON_KEY", anon["api_key"])
if service:
    gh_secret("SUPABASE_SERVICE_ROLE_KEY", service["api_key"])

# ------------------------------------------------------------------ files for the site and the record
with open("site/config.js", "w") as f:
    f.write("// Generated by .github/workflows/provision.yml. The anon key is public by design; row-level security does the guarding.\n")
    f.write("window.HN_CONFIG = " + json.dumps({"url": URL, "key": anon["api_key"]}) + ";\n")
infra = {
    "supabase": {"org": ORG_NAME, "org_id": ORG_ID, "project": PROJECT_NAME, "ref": REF, "url": URL, "region": REGION, "plan": "free"},
    "site": {"url": SITE_URL, "hosting": "GitHub Pages (workflow build from site/)"},
    "secrets": ["SUPABASE_URL", "SUPABASE_PROJECT_REF", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_PASSWORD"],
    "provisioned_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}
with open("ops/infra.json", "w") as f:
    json.dump(infra, f, indent=2)
log("wrote site/config.js and ops/infra.json")

# ------------------------------------------------------------------ github: public repo + pages (workflow build)
env = {**os.environ, "GH_TOKEN": os.environ["GH_PAT"]}
r = subprocess.run(["gh", "api", f"repos/{REPO}", "--jq", ".visibility"], capture_output=True, text=True, env=env)
vis = r.stdout.strip()
if vis != "public":
    r = subprocess.run(["gh", "repo", "edit", REPO, "--visibility", "public", "--accept-visibility-change-consequences"], capture_output=True, text=True, env=env)
    log(f"repo visibility {vis} -> public: {'ok' if r.returncode == 0 else 'FAILED ' + r.stderr.strip()[:200]}")
else:
    log("repo already public")
r = subprocess.run(["gh", "api", f"repos/{REPO}/pages", "--jq", ".build_type"], capture_output=True, text=True, env=env)
if r.returncode == 0:
    bt = r.stdout.strip()
    if bt != "workflow":
        r2 = subprocess.run(["gh", "api", "-X", "PUT", f"repos/{REPO}/pages", "-f", "build_type=workflow"], capture_output=True, text=True, env=env)
        log(f"pages build_type {bt} -> workflow: {'ok' if r2.returncode == 0 else 'FAILED ' + r2.stderr.strip()[:200]}")
    else:
        log("pages already enabled (workflow)")
else:
    r2 = subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/pages", "-f", "build_type=workflow"], capture_output=True, text=True, env=env)
    log(f"pages enabled: {'ok' if r2.returncode == 0 else 'FAILED ' + (r2.stderr or r2.stdout).strip()[:300]}")

log("provisioning complete")
finish(True)
