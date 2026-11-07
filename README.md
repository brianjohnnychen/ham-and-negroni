# Ham & Negroni

A joke dating site for friends in Japan, served seriously. Founded by Carol, Anderson, Thomas, Brian and Timothy over one plate of ham and several Negronis.

- Live: https://brianjohnnychen.github.io/ham-and-negroni/
- Frontend: `site/` (static HTML/CSS/JS, no build step), deployed by `.github/workflows/pages.yml`.
- Backend: Supabase free project (auth, Postgres with row-level security, storage for photos, realtime). Schema in `supabase/schema.sql`.
- Provisioning: `.github/workflows/provision.yml` runs `ops/provision.py` (creates the free org/project, applies the schema, turns off email confirmation, stores keys as repo secrets, writes `site/config.js`, makes the repo public, enables Pages). Re-run by changing `ops/provision.request`.
- Verification: `.github/workflows/e2e.yml` runs `ops/e2e.py` (two throwaway members on phone and desktop viewports: join, verdicts, live Tab, chat both ways, photo upload, privacy checks), commits screenshots and `ops/e2e/result.json`, then deletes the QA accounts and their data. Re-run by changing `ops/e2e.request`.
- Keepalive: `.github/workflows/keepalive.yml` pings the database twice a week so the free project never pauses, and commits a heartbeat so the schedule never gets disabled.

Vocabulary: a like is "Another round" and a dislike is "Call it a night". Every verdict is posted on The Tab, visible to all members. Chats are private to the two people in them (enforced by row-level security).
