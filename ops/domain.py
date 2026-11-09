"""Point the GitHub Pages site at its custom domain, wait for the HTTPS certificate, enforce HTTPS, verify.

Idempotent and safe to re-run. The domain comes from site/CNAME (the single source of truth).
Needs env: GH_PAT (repo admin), GITHUB_REPOSITORY. DNS lives at Cloudflare: four A records for the apex
and a www CNAME to <owner>.github.io, all DNS only (grey cloud) so GitHub can issue the certificate.
Writes ops/domain/result.json. Exit 0 only when the site answers on https://<domain>/ with the real backend config.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
DOMAIN = open("site/CNAME").read().strip().lower()
ENV = {**os.environ, "GH_TOKEN": os.environ["GH_PAT"]}
PAGES_IPS = {"185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153"}
RESULT = {"domain": DOMAIN, "log": []}


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    RESULT["log"].append(line)


def finish(ok):
    RESULT["ok"] = ok
    RESULT["finished"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    os.makedirs("ops/domain", exist_ok=True)
    with open("ops/domain/result.json", "w") as f:
        json.dump(RESULT, f, indent=2)
    sys.exit(0 if ok else 1)


def pages():
    r = subprocess.run(["gh", "api", f"repos/{REPO}/pages"], capture_output=True, text=True, env=ENV)
    return json.loads(r.stdout) if r.returncode == 0 else None


def put(**fields):
    cmd = ["gh", "api", "-X", "PUT", f"repos/{REPO}/pages"]
    for k, v in fields.items():
        cmd += ["-F" if isinstance(v, bool) or v is None else "-f", f"{k}={'null' if v is None else str(v).lower() if isinstance(v, bool) else v}"]
    r = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[:300]


def resolve(host):
    try:
        return {i[4][0] for i in socket.getaddrinfo(host, 443, socket.AF_INET)}
    except OSError:
        return set()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hn-domain-check"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.geturl(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except Exception as e:  # noqa: BLE001
        return f"error: {e}", url, ""


cur = pages()
if cur is None:
    log("GitHub Pages is not enabled for this repo; run provision first")
    finish(False)
log(f"pages: cname={cur.get('cname')!r} https_enforced={cur.get('https_enforced')} build_type={cur.get('build_type')}")

# 1. custom domain
if cur.get("cname") != DOMAIN:
    ok, msg = put(cname=DOMAIN)
    log(f"set custom domain {DOMAIN}: {'ok' if ok else 'FAILED ' + msg}")
    if not ok:
        finish(False)
else:
    log("custom domain already set")

# 2. DNS must point at GitHub before a certificate can be issued
deadline = time.time() + 20 * 60
while True:
    apex, www = resolve(DOMAIN), resolve("www." + DOMAIN)
    if apex and apex <= PAGES_IPS and www and www <= PAGES_IPS:
        log(f"dns ok: {DOMAIN} -> {sorted(apex)}, www -> {sorted(www)}")
        break
    if time.time() > deadline:
        log(f"dns not pointing at GitHub Pages after 20 min: apex={sorted(apex)} www={sorted(www)}")
        finish(False)
    log(f"waiting for dns: apex={sorted(apex)} www={sorted(www)}")
    time.sleep(30)

# 3. certificate (GitHub issues it once its own DNS check passes)
started = time.time()
retriggered = False
state = None
while time.time() - started < 30 * 60:
    cur = pages() or {}
    cert = cur.get("https_certificate") or {}
    state = cert.get("state")
    if state == "approved":
        log(f"certificate approved (expires {cert.get('expires_at')}, domains {cert.get('domains')})")
        break
    log(f"certificate state: {state}")
    stuck = state in ("errored", "bad_authz", "bad_authz_fatal", "dns_changed", "new") or (state is None and time.time() - started > 8 * 60)
    if stuck and not retriggered and time.time() - started > 4 * 60:
        retriggered = True
        put(cname=None)
        time.sleep(5)
        ok, msg = put(cname=DOMAIN)
        log(f"re-triggered certificate by re-setting the domain: {'ok' if ok else 'FAILED ' + msg}")
    time.sleep(30)
if state != "approved":
    log("certificate not approved in time; re-run later (touch ops/domain.request)")
    finish(False)

# 4. enforce HTTPS
for attempt in range(1, 7):
    ok, msg = put(https_enforced=True)
    if ok:
        log("https enforced")
        break
    log(f"enforce https attempt {attempt}: {msg}")
    time.sleep(20)
else:
    finish(False)

# 5. verify what visitors get (a new certificate takes a few minutes to reach every GitHub edge, so retry)
deadline = time.time() + 15 * 60
while True:
    code, final, body = fetch(f"https://{DOMAIN}/")
    cfg_code, _, cfg = fetch(f"https://{DOMAIN}/config.js")
    wcode, wfinal, _ = fetch(f"https://www.{DOMAIN}/")
    hcode, hfinal, _ = fetch(f"http://{DOMAIN}/")
    good = (code == 200 and "Negroni" in body and "supabase.co" in cfg
            and wcode == 200 and wfinal.startswith(f"https://{DOMAIN}")
            and hcode == 200 and hfinal.startswith("https://"))
    log(f"https://{DOMAIN}/ -> {str(code)[:60]}, site={'Negroni' in body}, backend config={'supabase.co' in cfg}; "
        f"www -> {str(wcode)[:40]} ({wfinal}); http -> {str(hcode)[:40]} ({hfinal})")
    if good or time.time() > deadline:
        break
    time.sleep(30)
RESULT["apex"] = {"status": code, "final_url": final, "has_site": "Negroni" in body, "has_backend": "supabase.co" in cfg}
RESULT["www"] = {"status": wcode, "final_url": wfinal}
RESULT["http"] = {"status": hcode, "final_url": hfinal}
log("verified" if good else "verification failed")
finish(good)
