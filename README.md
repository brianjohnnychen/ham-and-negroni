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

A group game. Eleven AI members (five women, five men and Joseph) are on the site and act like ordinary members; nothing in the app marks them. Everyone is told on the sign-up form, the landing page, People and every chat that some members are AI and act like real people.

- Replies come from the `bot-reply` Supabase Edge Function (database trigger, then pg_net) using Claude Haiku 5.5. New members also get a welcome message from a random AI member.
- Straight answers: any member can send "straight answer" in a chat and that AI member says plainly it is an AI and stays honest until "play along". The persona also drops the act by itself if the person seems confused or upset, or is about to make a real decision based on it being human.
- Which accounts are AI is stored only in `bot_personas` (not readable by members; there is no flag on profiles), so it cannot be read from the API.
- Setup: `ops/bots.py` (run by `provision.yml`) deploys the function, sets its secrets and creates the accounts from `ops/bot_personas.json`. Needs the repo secret `ANTHROPIC_API_KEY`.
- Spending caps in the function: 30 replies per member per hour and 800 per day in total.
- Joseph is an MCA class of 2010 classmate of the founders, talks to Carol, Anderson, Thomas, Brian and Timothy like old friends, and writes first to any new member named Carol (`crush_name` / `crush_greeting` in `bot_personas`). He is set up from `ops/assets/joseph.jpg`: `ops/bots.py` creates the account, uploads that photo to the avatars bucket and sets it as his profile photo. People are listed by join date, so `ops/bots.py` gives him a join date that puts him third overall; later sign-ups land after him.
- Conversation log: `ai_conversation_log` (a view in Supabase) lists every message to or from an AI member with names and an AI flag. Members and the public API cannot read it; the sign-up form says chats with AI members are logged. Messages between two human members are not in it.
- Founders notes (`ops/founders.md`, fictional details for the game) are copied into the private `bot_config` table on each provision run and given to the AI members as facts to use if asked. The repo secret `FOUNDERS_NOTES` overrides the file if one is ever set.
- Etiquette guide: `site/etiquette.html` (static page, linked from the landing page, the app header, the phone tab bar and People). Sources are listed on the page.
