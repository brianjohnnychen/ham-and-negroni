"""Read-only diagnostics for the welcome/AI pipeline. Prints to the job log only; never prints secret values.
Needs env: SUPABASE_ACCESS_TOKEN, SUPABASE_PROJECT_REF."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

REF = os.environ["SUPABASE_PROJECT_REF"]
TOKEN = os.environ["SUPABASE_ACCESS_TOKEN"]


def api(method, path, body=None):
    req = urllib.request.Request("https://api.supabase.com" + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "hn-diag")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def sql(title, q):
    st, res = api("POST", f"/v1/projects/{REF}/database/query", {"query": q})
    print(f"\n== {title} (http {st})")
    if isinstance(res, list):
        for row in res:
            print(json.dumps(row, default=str)[:400])
        if not res:
            print("(no rows)")
    else:
        print(str(res)[:400])


sql("triggers on profiles/messages", "select c.relname as tbl, t.tgname, t.tgenabled from pg_trigger t join pg_class c on c.oid=t.tgrelid where not t.tgisinternal and c.relname in ('profiles','messages') order by 1,2")
sql("bot_config keys (values hidden)", "select key, length(value) as len from public.bot_config order by key")
sql("function url host", "select split_part(split_part(value,'//',2),'/',1) as host, split_part(value,'/',4) as seg from public.bot_config where key='function_url'")
sql("pg_net recent responses", "select id, status_code, error_msg, timed_out, created, left(content,120) as content from net._http_response order by id desc limit 12")
sql("pg_net queue", "select count(*) as queued from net.http_request_queue")
sql("bot messages today (utc) and last hour", "select count(*) filter (where created_at > now() - interval '1 day') as last_day, count(*) filter (where created_at > now() - interval '1 hour') as last_hour from public.messages m where m.sender in (select profile_id from public.bot_personas)")
sql("most recent messages from AI members", "select m.id, m.created_at, p.display_name as sender, left(m.body,60) as body from public.messages m join public.profiles p on p.id=m.sender where m.sender in (select profile_id from public.bot_personas) order by m.id desc limit 6")
sql("most recent profiles", "select display_name, created_at from public.profiles order by created_at desc limit 4")

q = "select timestamp, event_message from function_logs order by timestamp desc limit 40"
st, res = api("GET", f"/v1/projects/{REF}/analytics/endpoints/logs.all?sql=" + urllib.parse.quote(q))
print(f"\n== edge function logs (http {st})")
rows = res.get("result") if isinstance(res, dict) else None
if isinstance(rows, list):
    for r in rows:
        print(str(r.get("timestamp")), str(r.get("event_message"))[:300])
    if not rows:
        print("(no rows)")
else:
    print(str(res)[:500])
