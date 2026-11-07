# Ham & Negroni

A joke dating site for friends in Japan. Founded by Carol, Anderson, Thomas, Brian and Timothy.

- Live: https://brianjohnnychen.github.io/ham-and-negroni/
- Frontend: `site/` (static HTML/CSS/JS, no build step), deployed by `.github/workflows/pages.yml`.
- Backend: Supabase free project (auth, Postgres with row-level security, storage for photos, realtime). Schema in `supabase/schema.sql`.
- Provisioning: `.github/workflows/provision.yml` runs `ops/provision.py` (creates the free org/project, applies the schema, turns off email confirmation, stores keys as repo secrets, writes `site/config.js`, makes the repo public, enables Pages). Re-run by changing `ops/provision.request`.
- Verification: `.github/workflows/e2e.yml` runs `ops/e2e.py` (two throwaway members on phone and desktop viewports: sign up with name, email and password, likes and dislikes, live feed, messages both ways, photo upload, privacy checks), commits screenshots and `ops/e2e/result.json`, then deletes the QA accounts and their data. Re-run by changing `ops/e2e.request`.
- Keepalive: `.github/workflows/keepalive.yml` pings the database twice a week so the free project never pauses, and commits a heartbeat so the schedule never gets disabled.

Likes and dislikes are posted on the public feed, visible to all members. Messages are private to the two people in them (enforced by row-level security). Sign-up asks only for name, email and password; email confirmation is off.
