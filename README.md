# Ham & Negroni

A joke dating site for friends in Japan. Founded by Carol, Anderson, Thomas, Brian, Timothy and Joseph.

- Live: https://brianjohnnychen.github.io/ham-and-negroni/
- Frontend: `site/` (static HTML/CSS/JS, no build step), deployed by `.github/workflows/pages.yml`.
- Backend: Supabase free project (auth, Postgres with row-level security, storage for photos, realtime). Schema in `supabase/schema.sql`.
- Provisioning: `.github/workflows/provision.yml` runs `ops/provision.py` (creates the free org/project, applies the schema, turns off email confirmation, stores keys as repo secrets, writes `site/config.js`, makes the repo public, enables Pages). Re-run by changing `ops/provision.request`.
- Verification: `.github/workflows/e2e.yml` runs `ops/e2e.py` (two throwaway members on phone and desktop viewports: sign up with name, email and password, likes and dislikes, live feed, messages both ways, photo upload, privacy checks), commits screenshots and `ops/e2e/result.json`, then deletes the QA accounts and their data. Re-run by changing `ops/e2e.request`.
- Keepalive: `.github/workflows/keepalive.yml` pings the database twice a week so the free project never pauses, and commits a heartbeat so the schedule never gets disabled.

Likes and dislikes are posted on the public feed, visible to all members. Messages are private to the two people in them (enforced by row-level security). Sign-up asks only for name, email and password; email confirmation is off.

## AI members

Ten AI members (five women, five men) are marked with `(a)` after their name, in People, the feed, messages and profiles. A message sent to one calls the `bot-reply` Supabase Edge Function (via a database trigger and pg_net), which writes the reply as that member using Claude Haiku 5.5. New members also get a fixed welcome message from a random AI member. Each persona is told to say it is an AI if asked.

- Setup: `ops/bots.py` (run by `provision.yml`) deploys the function, sets its secrets, and creates the ten accounts from `ops/bot_personas.json`. Needs the repo secret `ANTHROPIC_API_KEY`.
- Spending caps in the function: 30 replies per member per hour and 800 replies per day in total. Past the cap the AI member sends a "message me again later" line instead of calling the model.
- Members cannot flag themselves as AI: `is_bot` is guarded by a trigger and only the service role can set it.
