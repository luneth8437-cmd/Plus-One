# Plus One Project Progress Log

This file preserves important project context from the troubleshooting and optimization work so future Codex app threads can recover what happened without needing the original chat transcript.

## Current Project Root

Use this folder as the Codex app project root:

```bash
/Users/fillun/Desktop/plus-one/plus-one
```

This is the Django project folder. It contains `manage.py`, `requirements.txt`, `.env`, `config/`, and `plusone/`.

## Simulation Findings

Eight anonymous users were used for two simulated flows:

- Concurrently swiping the same card.
- Full normal path: create post -> Discover -> Interested -> Match -> Chat -> safety review -> Agree -> Dashboard.

Confirmed working:

- Anonymous sessions are isolated.
- Post owners cannot swipe their own cards.
- A second user is blocked from matching an already matched card.
- Non-participants cannot access chat and receive 403.
- Unsafe chat content is blocked and not stored.
- Ambiguous time such as `at 7` offers Morning / Evening options.
- When both users agree, the match enters handoff state.

Issues from this handoff, now resolved:

1. Concurrent swipe handling no longer exposes SQLite `OperationalError: database table is locked`; lock conflicts retry briefly and then fall back to the current post/match state.
2. Dashboard match actions now reflect state: chatting opens chat, agreed shows handoff, expired/declined show ended-state labels.
3. Create preview now uses display-friendly placeholders and formats `datetime-local` values for the card preview.
4. Pass now has an undo path for the most recent skipped active card.

Follow-up verification on July 4, 2026:

```bash
/tmp/plusone-verify-venv/bin/python manage.py check
DATABASE_URL=sqlite:////tmp/plusone-verify.sqlite3 /tmp/plusone-verify-venv/bin/python manage.py test plusone
```

Observed result:

```text
System check identified no issues
Ran 64 tests in 30.404s
OK
```

Note: the in-project `.venv-pg` and `.venv313` environments still showed very slow Django package reads from the Desktop project folder during this follow-up. A temporary virtual environment under `/tmp` was used for verification to avoid that file-read bottleneck.

## PostgreSQL Local Setup

Goal: use PostgreSQL locally for concurrency testing while keeping SQLite optional for simple development.

Completed:

- Installed Homebrew `postgresql@16`.
- Started PostgreSQL with Homebrew services.
- Created local role `plusone` with password `plusone`.
- Created local database `plusone` owned by `plusone`.
- Added this to `.env`:

```env
DATABASE_URL=postgres://plusone:plusone@127.0.0.1:5432/plusone
```

- Created `.venv-pg/` as a clean local Python virtual environment.
- Installed dependencies from `requirements.txt`.
- Added `.venv-pg/` to `.gitignore`.
- Ran all Django migrations successfully against PostgreSQL.

Verification performed:

```bash
.venv-pg/bin/python manage.py check
.venv-pg/bin/python manage.py shell -c "from django.db import connection; from plusone.models import CampusLocation; print(connection.vendor); print(CampusLocation.objects.count())"
```

Observed result:

```text
System check identified no issues
postgresql
5
```

Direct PostgreSQL check also showed 17 public tables and 5 seeded campus locations.

## Local Run Commands

From the project root:

```bash
cd /Users/fillun/Desktop/plus-one/plus-one
brew services start postgresql@16
.venv-pg/bin/python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

## Troubleshooting Notes

The existing `.venv` and `.venv313` environments were slow to import Django. A clean `.venv-pg` was created for the PostgreSQL workflow.

After installing dependencies, Django initialization was still very slow because files from the extracted project and virtual environment had macOS `com.apple.provenance` extended attributes. This was fixed with:

```bash
xattr -dr com.apple.provenance .
```

Then bytecode was precompiled:

```bash
.venv-pg/bin/python -m compileall -q .venv-pg/lib/python3.14/site-packages/django .venv-pg/lib/python3.14/site-packages/sqlparse plusone config
```

After that, `django.setup()` and migrations completed normally.

## Codex App Continuity

This API conversation cannot be directly inserted into the Codex app thread list. Codex app can search and reopen its own past threads, and its import flow can import supported recent chat sessions from other agents, but this current API session should be treated as separate unless the app itself imports it.

To continue in Codex app:

1. Open folder `/Users/fillun/Desktop/plus-one/plus-one`.
2. Start a new Local thread.
3. Reference this file: `docs/project_progress_log.md`.
4. Continue with the open issues listed above.

Suggested prompt for a new Codex app thread:

```text
Please continue optimizing the Plus One project. First read docs/project_progress_log.md for context. Then implement the remaining fixes in priority order: concurrent swipe fallback, Dashboard match-state CTA, Create preview formatting, and Pass Undo.
```

## Long-Term Memory Rule


Treat this file as the long-term project memory for Plus One.

After every meaningful project update, append a new entry to this file before ending the task. A meaningful update includes code changes, dependency or database changes, configuration changes, test/debug work, deployment changes, product decisions, or any troubleshooting that future threads should not rediscover from scratch.

Each entry must use this structure:

```markdown
## YYYY-MM-DD: Short Title

Problem or confusion:

- What changed, broke, was unclear, or needed a decision.

Diagnosis:

- How the cause was identified.
- Relevant files, code paths, symptoms, or assumptions.

Tried:

- Commands, checks, tests, inspections, or experiments that were run.

Worked:

- The solution or decision that succeeded.

Failed or abandoned:

- Attempts that failed, were inconclusive, or were intentionally not used.

Current status:

- What is true now after the update.
- Any verification result, such as tests passing or a server starting.

Next step:

- The next concrete action recommended for future work.

Technical decisions:

- Durable decisions made during the update, including database, environment, architecture, UX, or testing choices.
```

Keep entries concise but specific enough that a new Codex thread can continue without asking for missing context.

Do not store secrets here, including API keys, real account credentials, private tokens, or sensitive user data.

## 2026-07-04: Added Outer Folder Pointer

Problem or confusion:

- The Codex app was opened at `/Users/fillun/Desktop/plus-one`, while the Django project root is `/Users/fillun/Desktop/plus-one/plus-one`.
- The user wanted the long-term memory document to also be visible from the outer `plus-one` folder.

Diagnosis:

- The real project log already existed at `plus-one/docs/project_progress_log.md`.
- Duplicating the full log in two places would create a stale-copy risk.

Tried:

- Checked the outer folder contents.
- Checked the inner `docs/` folder contents.

Worked:

- Added `/Users/fillun/Desktop/plus-one/project_progress_log.md` as a short pointer to the canonical project log.

Failed or abandoned:

- Did not duplicate the full log into the outer folder because two editable copies could drift.

Current status:

- Canonical long-term memory remains `docs/project_progress_log.md` inside the Django project root.
- The outer folder now has a top-level pointer file for easier discovery from Codex app.

Next step:

- Continue updating the canonical `docs/project_progress_log.md` after meaningful project changes.

Technical decisions:

- Keep only one canonical progress log to avoid stale duplicate notes.

## 2026-07-04: Defined Progress Log Template

Problem or confusion:

- The user wanted this document to explicitly define what must be recorded after every future project update.
- The existing long-term memory rule listed useful fields but did not provide a strict reusable template.

Diagnosis:

- The project needs cross-thread continuity because Codex app threads do not automatically share full conversation history.
- A fixed template makes future updates consistent and easier to scan.

Tried:

- Reviewed the existing `Long-Term Memory Rule` section.
- Updated it in place instead of creating a second competing rule.

Worked:

- Added explicit trigger conditions for when this log must be updated.
- Added a required Markdown entry template with sections for problem, diagnosis, tried commands, successful solution, failed attempts, current status, next step, and technical decisions.

Failed or abandoned:

- Did not create a separate logging policy file because this progress log should remain the single place future threads check first.

Current status:

- `docs/project_progress_log.md` now defines the required format for every meaningful future project update.

Next step:

- For the next implementation task, append a new dated entry using this exact template before finishing.

Technical decisions:

- Use `docs/project_progress_log.md` as the canonical cross-thread project memory and update it after meaningful work.

## 2026-07-04: Status Check After Optimization Work

Problem or confusion:

- The user asked where the project currently stands.
- The progress log said the four earlier optimization issues were resolved, but current code and verification needed to be checked rather than relying only on notes.

Diagnosis:

- Reviewed `docs/project_progress_log.md`.
- Searched the codebase for the expected implementation traces: lock-conflict fallback, Pass Undo, Dashboard state CTA, and Create preview formatting.
- Confirmed the project root is `/Users/fillun/Desktop/plus-one/plus-one`.
- Confirmed this folder is not currently a Git repository, so there is no `git status` diff available.

Tried:

- Ran `.venv-pg/bin/python manage.py check` and a `.venv-pg` ORM check, but both were still too slow reading Django package files from the Desktop project folder.
- Used `/tmp/plusone-verify-venv/bin/python manage.py check`.
- Ran full tests with `DATABASE_URL=sqlite:////tmp/plusone-status-check.sqlite3 /tmp/plusone-verify-venv/bin/python manage.py test plusone`.
- Investigated the single test failure by checking `plusone/views.py`, `plusone/templates/plusone/about.html`, `plusone/urls.py`, and `config/settings.py`.
- Re-ran the failing About page test with `ALLOWED_HOSTS=127.0.0.1,localhost,testserver`.
- Re-ran the full suite with the same `ALLOWED_HOSTS` value.
- Checked Homebrew PostgreSQL service status and queried the local `plusone` PostgreSQL database directly.

Worked:

- `/tmp/plusone-verify-venv/bin/python manage.py check` passed.
- The About page test passed when `testserver` was included in `ALLOWED_HOSTS`.
- The full suite passed with 64 tests when run with `ALLOWED_HOSTS=127.0.0.1,localhost,testserver`.
- PostgreSQL service `postgresql@16` is started.
- Direct PostgreSQL query confirmed database `plusone`, user `plusone`, 17 public tables, and 5 seeded campus locations.

Failed or abandoned:

- `.venv-pg` remains too slow for practical Django command verification from this Desktop project path.
- A first full test run without `testserver` in `ALLOWED_HOSTS` failed one About page test because Django rejected the test client's default host.

Current status:

- The four previously identified product/reliability issues appear implemented in current code and covered by tests.
- Verified commands are green when test host configuration is correct: system check passes and 64 tests pass.
- Local PostgreSQL is configured and running.
- The test environment needs `testserver` in `ALLOWED_HOSTS` for full-suite verification.

Next step:

- Decide whether to make the `testserver` allowance automatic under tests or document it as the standard test command.
- Then continue with any remaining product polish or run the app manually against local PostgreSQL.

Technical decisions:

- Keep using `/tmp/plusone-verify-venv` for fast verification unless `.venv-pg` file-read slowness is fixed.
- Treat `ALLOWED_HOSTS` test host handling as a small configuration cleanup candidate.

## 2026-07-04: Fixed Preview Placeholder And Restarted Test Server

Problem or confusion:

- The user tested the first two fixes and reported that Create preview and Pass Undo did not appear implemented.
- The running `127.0.0.1:8040` server was started on July 3 with `--noreload`, so it could serve old code after source changes.
- Empty activity preview still showed `Other` in current source, while the requested placeholder was `Activity`.

Diagnosis:

- Checked the running 8040 process with `ps` and `lsof`; it was an old process from July 3 using `/private/tmp/plusone-venv313/bin/python manage.py runserver 127.0.0.1:8040 --noreload --skip-checks`.
- Inspected `plusone/static/plusone/app.js`, `plusone/presenters.py`, `plusone/forms.py`, `plusone/templates/plusone/discover.html`, and `plusone/views.py`.
- Confirmed Pass Undo code existed in source, but the old server process could hide it from the browser.
- Confirmed Create preview server output after restart shows `Activity`, `Campus location`, and a formatted time such as `Jul 4, 20:04`; the raw `datetime-local` value remains only in the form input, not the preview card.

Tried:

- Ran targeted tests for Create preview formatting and Pass Undo.
- Added a regression test for the empty activity preview placeholder.
- Killed the old 8040 server process.
- Restarted `127.0.0.1:8040` with `/tmp/plusone-verify-venv/bin/python manage.py runserver 127.0.0.1:8040 --noreload`.
- Checked rendered `/create/` output using a cookie jar and redirect-following curl.
- Ran the full test suite with `DATABASE_URL=sqlite:////tmp/plusone-status-check.sqlite3 ALLOWED_HOSTS=127.0.0.1,localhost,testserver /tmp/plusone-verify-venv/bin/python manage.py test plusone`.

Worked:

- Changed empty activity placeholders to `Activity` in the Django form, server-side preview presenter, and browser JS preview updater.
- The new target tests pass.
- The full suite now runs 65 tests and passes.
- The current 8040 server process was started from `/Users/fillun/Desktop/plus-one/plus-one` after the fix.

Failed or abandoned:

- The old 8040 server process was not reliable for testing because it was started before the latest source changes and used `--noreload`.
- Did not change the raw `datetime-local` form input display because that is the editable browser control; the product issue was the card preview showing raw system values.

Current status:

- Create preview now shows `Activity`, `Campus location`, and formatted preview time in the right-side card.
- Pass Undo exists in current source and should be visible after a user passes an active card from a separate anonymous session.
- Local test server is available at `http://127.0.0.1:8040/`.
- Full test suite passes: 65 tests OK.

Next step:

- The user should hard-refresh `http://127.0.0.1:8040/create/` and test Pass Undo with two separate browser sessions.

Technical decisions:

- Restart `--noreload` dev servers after source changes before judging UI behavior.
- Keep `Activity` as the empty activity preview placeholder instead of `Other`.

## 2026-07-04: Worked Around VS Code App Translocation

Problem or confusion:

- VS Code showed `command 'chatgpt.openSidebar' not found`, then the right-side Codex panel opened but stayed blank.
- The user wanted help completing the Codex app continuation steps from this log.

Diagnosis:

- VS Code was running from macOS App Translocation paths under `/private/var/folders/.../T/AppTranslocation/.../Visual Studio Code.app`.
- VS Code logs showed read-only/update issues and slow extension host startup.
- The Codex extension log showed prior `IpcClient` initialization timeouts and a fatal app-server startup error.
- `/Applications/Visual Studio Code.app` still had `com.apple.quarantine`, and the system did not allow removing it there without elevated permission.

Tried:

- Saved open VS Code editors with the app shortcut.
- Quit VS Code and cleared stuck VS Code helper processes and stale VS Code-extension Codex app-server processes.
- Launched `/Applications/Visual Studio Code.app` directly, but macOS still translocated it.
- Copied VS Code to `/Users/fillun/Applications/Visual Studio Code.app`, removed quarantine from that user-owned copy, and launched the project from there.

Worked:

- VS Code now runs from `/Users/fillun/Applications/Visual Studio Code.app` instead of an App Translocation path.
- The current OpenAI/Codex VS Code extension app-server starts from `openai.chatgpt-26.623.101652-darwin-arm64`.
- New Codex extension logs show initialization succeeded; only plugin manifest warnings remain.

Failed or abandoned:

- Removing `com.apple.quarantine` directly from `/Applications/Visual Studio Code.app` failed with `Operation not permitted`.
- Keeping the translocated VS Code instance was abandoned because it caused extension command registration and panel startup failures.

Current status:

- Use `/Users/fillun/Applications/Visual Studio Code.app` for this local workflow unless `/Applications/Visual Studio Code.app` is cleanly reinstalled.
- The current Codex desktop app process under `/Applications/Codex.app` was intentionally preserved during cleanup.

Next step:

- If VS Code should live in `/Applications`, reinstall it cleanly via Finder or remove the quarantine attribute with administrator permission.

Technical decisions:

- Do not kill `/Applications/Codex.app/Contents/Resources/codex app-server` when cleaning up VS Code extension processes.
- Prefer a user-owned VS Code app copy as a no-password workaround for App Translocation.

## 2026-07-04: Allowed Testserver During Test Runs

Problem or confusion:

- Full-suite tests previously required manually setting `ALLOWED_HOSTS=127.0.0.1,localhost,testserver`.
- Without `testserver`, Django could reject the test client's default host.

Diagnosis:

- `config/settings.py` defaulted `ALLOWED_HOSTS` to `127.0.0.1,localhost`.
- The latest progress log listed automatic test host handling as the next small configuration cleanup.

Tried:

- Inspected `config/settings.py` and `plusone/tests.py`.
- Ran system check and the full test suite without manually passing `ALLOWED_HOSTS`.

Worked:

- Updated `config/settings.py` to append `testserver` when Django is invoked with the `test` command.
- Added `test_test_client_host_is_allowed` to assert `testserver` remains allowed in test runs.
- `/tmp/plusone-verify-venv/bin/python manage.py check` passes.
- `DATABASE_URL=sqlite:////tmp/plusone-auto-host.sqlite3 /tmp/plusone-verify-venv/bin/python manage.py test plusone` passes without an `ALLOWED_HOSTS` override.

Failed or abandoned:

- Did not document a longer required test command because automatic handling is simpler and less error-prone.
- Did not change non-test host behavior.

Current status:

- Full suite passes with 66 tests.
- Test runs no longer need a manual `ALLOWED_HOSTS` environment variable for `testserver`.

Next step:

- Continue product polish or manual browser verification against the local PostgreSQL-backed app.

Technical decisions:

- Keep production and local runtime host configuration environment-driven.
- Scope `testserver` allowance to Django test command execution.

## 2026-07-04: Product Flow Hardening After Multi-Session Audit

Problem or confusion:

- Multi-session product audit found several remaining real-use issues: concurrent swipes could still fail on SQLite, Create showed a default start time before the user entered one, cards only supported one match, chat lacked a real decline/report exit, and posters could miss open five-minute chats.

Diagnosis:

- The previous SQLite retry path covered the match transaction, but `swipe_post()` called the global expiration sweep first, so table locks could still happen before the retry code.
- `create_post()` initialized `start_time` to current time plus one hour, which made the preview look like the app had inferred a time.
- `ActivityPost.status` changed to `matched` after the first interested swipe, making group plans impossible.
- Chat templates only provided `Agree to meet`; the "Not comfortable" quick reply did not change match state.

Tried:

- Added a local capacity service for held spots and post status syncing.
- Added `capacity` to `ActivityPost`, close metadata to `Match`, and migration `0005_capacity_and_match_close_state`.
- Removed the Create page default start time and added a `People needed` field with default `1` and max `6`.
- Updated matching so a post remains active until its capacity is full.
- Added real `Decline` and `Report` chat actions; closed matches release capacity and record the close reason.
- Added a top-nav open chat badge on `My Plus Ones`.
- Added remaining-spot copy to Discover, detail, and dashboard surfaces.
- Updated static asset cache keys to `20260704-product-flow`.

Worked:

- Applied migration `plusone.0005_capacity_and_match_close_state` successfully against the current PostgreSQL database.
- Full test suite passes: 71 tests OK.
- `node --check plusone/static/plusone/app.js` passes.
- Manual browser verification confirmed Create now has an empty start time, preview shows `Start time`, `People needed` appears, and capacity defaults to `1`.
- Responsive check confirmed the Create hero title stays one line at 1440px and 820px widths.
- Re-ran a 10-anonymous-session concurrency audit on SQLite: 9 simultaneous interested swipes produced no exceptions; 1 matched and the rest returned to Discover.

Failed or abandoned:

- Did not remove the global expiration sweep entirely; it now defers safely on SQLite lock errors. Longer-term production reliability still benefits from PostgreSQL.
- Did not add real-time push notifications; the current improvement is a lightweight open chat badge.

Current status:

- Local app is running at `http://127.0.0.1:8040/`.
- Current runtime database engine is PostgreSQL.
- Create no longer invents a start time.
- Multi-person cards are supported through capacity.
- Chat has explicit Decline and Report exits.
- Navigation now surfaces open chat count.

Next step:

- Manually verify the updated Create, Discover, and Chat flows in the browser.
- If this becomes a real deployment, keep PostgreSQL as the production database and consider push/WebSocket notifications for time-sensitive chats.

Technical decisions:

- Keep default capacity at `1` so existing single-match behavior remains unchanged unless the user opts into more people.
- Treat `chatting` and `agreed` matches as holding capacity; declined/expired matches release capacity.
- Keep AI calls outside database transactions and keep local safety/lock fallbacks in place for demo resilience.

## 2026-07-04: Dashboard State Panel Redesign

Problem or confusion:

- The Dashboard hero contained a large rounded gradient rectangle that looked like an empty or broken content block.
- The same zero-value summary appeared in several places: the hero visual, the statistic cards, and the empty list panels.
- The user clarified that the hero already had `Create another Plus One` and `View Discover`, so the right side should not repeat action buttons.

Diagnosis:

- The empty rectangle came from the decorative `.dashboard-hero-visual::before` CSS pseudo-element.
- Dashboard was mixing marketing-style decoration with operational status, which made the page feel large but low-information.
- For this product, Dashboard should answer "what needs attention right now" rather than decorate the page.

Tried:

- Added `dashboard_state()` to centralize the current Dashboard state: needs decision, live now, ready to meet, or all clear.
- Replaced the decorative hero visual with a semantic `dashboard-state-panel`.
- Removed the duplicate `dashboard-stats` section.
- Reworked Dashboard panels into `Live cards`, `Meet handoffs`, and `Closed activity`; open chats appear first only when action is needed.
- Updated the static asset cache key to `20260704-dashboard-state`.

Worked:

- The empty gradient block no longer renders.
- The right side now shows a concise state summary such as `Nothing live right now` plus one set of metrics.
- Dashboard no longer repeats the same 0-count information in three places.
- Targeted Dashboard tests pass.
- Full test suite passes: 72 tests OK.
- Browser verification confirmed no horizontal overflow and no `.dashboard-hero-visual` in the rendered page.

Current status:

- Dashboard is now a current-state cockpit instead of a decorative dashboard mockup.
- Local app is running at `http://127.0.0.1:8040/dashboard/`.

Technical decisions:

- Keep left-side hero CTAs as the only Dashboard action entry points.
- Keep the right-side hero area informational, not action-heavy.
- Show open chats as the first list section only when there is something to decide.

## 2026-07-04: Discover Feedback and Empty-State Polish

Problem or confusion:

- In a 50-session product simulation, users who tapped an already-filled card were redirected back to Discover without a clear reason.
- Pass recovery was present, but not reliably visible enough during browsing.
- A cold or filtered Discover queue gave too little guidance for what to do next.

Changed:

- Added a distinct `FULL_POST` swipe outcome so a full card is not presented as a generic inactive post.
- Updated the full-card redirect message to explain that the Plus One just filled up and the user can pick another card.
- Changed the pass recovery banner into a fixed bottom status panel with clearer copy.
- Reworked the Discover empty state with explicit actions: start a Plus One, clear filters when relevant, and refresh the queue.
- Updated the static asset cache key to `20260704-discover-feedback`.

Worked:

- `manage.py check` passes.
- `node --check plusone/static/plusone/app.js` passes.
- Full test suite passes: 75 tests OK.
- Local Discover endpoint returns 200 while the dev server is running on `http://127.0.0.1:8040/`.

## 2026-07-04: Pass and Undo Consistency Fix

Problem or confusion:

- A 200-session simulation showed that Pass/Undo still felt inconsistent.
- Root cause: if a card became full before a user tapped `×`, the backend returned the full-card outcome before recording the Pass, so Discover had no swipe record to undo.
- Discover also only looked for active posts when showing the undo panel, which hid undo for a just-passed but already-filled card.

Changed:

- Moved Pass handling before full-card checks in `plusone/services/matching.py`.
- Kept full-card checks focused on `♥ Interested`.
- Allowed Discover to show undo for non-expired active or matched cards that the current user just passed.
- Updated the Pass success message to explicitly say Undo is available.
- Added a regression test for passing a full post and undoing it.

Worked:

- `manage.py check` passes.
- `node --check plusone/static/plusone/app.js` passes.
- Full test suite passes: 76 tests OK.
- Re-ran a 200 anonymous-session simulation: 0 server errors, 0 Undo-weak cases, 46 matches, 54 clear full-card responses, 30/30 Pass-Undo paths stable, and 20/20 unsafe assist attempts blocked.

## 2026-07-04: Product Manager Polish for Create, Chat, Dashboard, and Handoff

Problem or confusion:

- A top-PM product walk-through found four high-impact UX issues that were not core logic bugs:
  - Create still exposed AI/LLM-oriented wording.
  - Chat safety actions did not clearly separate Decline from Report.
  - Dashboard did not make open chats feel urgent enough.
  - Agreed matches needed a stronger meet handoff and safety completion state.

Changed:

- Replaced Create page AI/LLM copy with user-facing plan-builder language.
- Changed the assist success message to `Draft ready. Review the details before publishing.`
- Added clear chat safety copy: Decline closes without a report; Report is for safety concerns.
- Added a report confirmation prompt via a generic `data-confirm-submit` handler.
- Made Dashboard switch its hero CTA to `Open waiting chat` when an open chat exists.
- Highlighted open chats as a priority panel.
- Expanded the agreed-match handoff into a clearer meet card with a safety checklist.
- Updated the static asset cache key to `20260704-pm-polish`.

Worked:

- `manage.py check` passes.
- `node --check plusone/static/plusone/app.js` passes.
- Full test suite passes: 76 tests OK.
- Manual rendered-flow check confirmed the new Create, Chat, Dashboard, and Handoff copy appears correctly.

## 2026-07-05: One-to-One Product Model and Dashboard Live Card Layout Fix

Problem or confusion:

- `People needed` made the product look like group matching, while the actual chat model is a one-to-one match between poster and swiper.
- Legacy posts with capacity above 1 could create multiple separate one-to-one chats from one card, which weakened the Plus One concept.
- Dashboard Live cards could become visually irregular in narrow columns because the row kept a three-column layout with a status block, long title, timer text, and actions competing for space.

Changed:

- Removed the `People needed` field from the Create review form.
- Removed capacity from the live preview and client-side preview update logic.
- Kept the database `capacity` field for compatibility, but forced reviewed form saves to `capacity = 1`.
- Added an effective one-to-one capacity in matching/status services so legacy `capacity > 1` posts still allow only one active match.
- Updated active-post queries to exclude cards that already have a held match, so an open-chat card does not also appear as a Live card.
- Simplified Dashboard Live card rows by removing the `Live / active now` metric block and using stable two-column row layout.
- Updated the static asset cache key to `20260705-one-to-one-dashboard`.

Worked:

- `manage.py check` passes.
- `node --check plusone/static/plusone/app.js` passes.
- Full test suite passes: 76 tests OK.
- Rendered-flow check confirmed Create no longer shows `People needed`, legacy `capacity=5` posts allow only one match, and Dashboard no longer shows matched cards as live cards.

## 2026-07-06: Production Deployment Preparation

Problem or confusion:

- The app was ready for product testing, and the next step was public deployment.
- Deployment files existed, but production needed a tighter safety pass before pushing to a hosted service.

Diagnosis:

- `render.yaml`, `build.sh`, and `DEPLOY_RENDER.md` were present.
- `.env.example` contained a real-looking API key and needed to be converted to placeholders only.
- `build.sh` ran `seed_demo`, which is useful for local demos but inappropriate for production because it creates demo users and posts.
- Production settings allowed the development `django-insecure-...` secret fallback if `SECRET_KEY` was missing.

Changed:

- Replaced `.env.example` with placeholder values only.
- Removed `python manage.py seed_demo` from `build.sh`; production now runs dependency install, static collection, and migrations only.
- Added a production guard that raises `ImproperlyConfigured` when `DJANGO_DEBUG=False` and `SECRET_KEY` is still the development fallback.
- Added production HTTPS defaults for `SECURE_SSL_REDIRECT` and a short HSTS max age, with environment-variable overrides.
- Rewrote `DEPLOY_RENDER.md` as a practical launch checklist covering preflight checks, GitHub push, Render Blueprint deployment, environment variables, live verification, production data, and key rotation.

Worked:

- `manage.py check` passes.
- `node --check plusone/static/plusone/app.js` passes.
- Full test suite passes: 76 tests OK.
- Production-mode `collectstatic` copied and post-processed static files successfully.
- Secret scan found the real key only in the ignored local `.env`, not in `.env.example`, docs, config, or app code.
- A negative production check without `SECRET_KEY` fails intentionally with `Set SECRET_KEY in the production environment.`

Current status:

- The codebase is prepared for Render deployment using `render.yaml`.
- Production will not auto-seed demo users or demo posts.
- `DEEPSEEK_API_KEY` should be configured only in the hosting provider dashboard.

Next step:

- Push the project to GitHub, create a Render Blueprint from `render.yaml`, fill `DEEPSEEK_API_KEY`, wait for first deploy, and run the live verification checklist in `DEPLOY_RENDER.md`.

Technical decisions:

- Keep Render as the documented first deployment path because the app already uses `DATABASE_URL`, PostgreSQL, WhiteNoise, Gunicorn, and Render hostname support.
- Do not enable HSTS subdomain include or preload by default until the product uses a confirmed HTTPS-only custom domain.

## 2026-07-06: Render Blueprint Free Plan Correction

Problem or confusion:

- Render showed `Payment Information Required` while creating the Blueprint.
- The user wanted to deploy the first public test without unexpectedly selecting a paid instance.

Diagnosis:

- Render's Blueprint defaults use paid production-grade plans when `plan` is omitted.
- The existing `render.yaml` did not set a plan for the web service or PostgreSQL database.

Changed:

- Set the web service plan to `free` in `render.yaml`.
- Set the PostgreSQL database plan to `free` in `render.yaml`.
- Reduced `WEB_CONCURRENCY` from `4` to `1` for the free web service memory profile.
- Updated `DEPLOY_RENDER.md` to state that the Blueprint starts on free instance plans and that free Postgres is only suitable for launch testing.

Worked:

- The configuration now explicitly requests free Render resources for the first hosted test.

Current status:

- The GitHub branch should be updated before retrying the Render Blueprint flow.

Next step:

- Push the updated `render.yaml`, then retry Blueprint creation on Render from the `deepseek-api` branch.

Technical decisions:

- Prefer free Render resources for the first public validation, then upgrade the database before relying on durable user data.

## 2026-07-06: Public Repository Presentation Update

Problem or confusion:

- The user wanted other people to understand the project quickly and star the GitHub repository.
- The repository page had no About description, website, or topics, and the README did not surface the live demo in the first screen.

Diagnosis:

- The public repo was already deployed and star-able.
- GitHub About metadata is managed through repository settings, while README content is part of the codebase and can be updated through git.

Changed:

- Added a concise product description at the top of `README.md`.
- Added the live Render demo URL near the top of `README.md`.
- Added the GitHub repo URL and a polite GitHub Star prompt.
- Added a short `Why Plus One` section and a compact tech stack list.

Worked:

- The README now communicates the product, live demo, and core technical credibility before the setup instructions.

Current status:

- README content is ready to push to the public repository.
- GitHub About metadata still needs to be filled in the repository UI if API metadata editing is unavailable.

Next step:

- Push the README update, then set the repository About description, website, and topics in GitHub.

Technical decisions:

- Keep the star prompt polite and secondary; the first priority is showing a working live demo and clear product value.

## 2026-07-06: Repository Maturity Assets

Problem or confusion:

- The user wanted the public repository to look richer and more trustworthy for visitors who may star the project.
- The repository still lacked screenshots, license, contribution guidance, security policy, and CI automation.

Diagnosis:

- `README.md` had the live demo but no visual screenshots or architecture/product-flow section.
- There was no `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, or `.github/workflows/ci.yml`.
- Google Chrome was available locally, so screenshots could be captured from the live Render deployment without adding screenshot tooling to the project.

Changed:

- Captured real live-site screenshots for Discover, Create, Dashboard, and About into `docs/screenshots/`.
- Updated `README.md` with CI/license badges, screenshots, product flow, architecture, safety/privacy notes, roadmap, and repository docs links.
- Added an MIT `LICENSE`.
- Added `CONTRIBUTING.md` with setup, test commands, PR guidance, and product principles.
- Added `SECURITY.md` with vulnerability reporting, secret-handling guidance, AI safety scope, and deployment security expectations.
- Added GitHub Actions CI to run Django system checks, JavaScript syntax check, and the Django test suite.

Worked:

- The repository now has the common trust signals expected by GitHub visitors: screenshots, license, contribution path, security policy, and automated checks.

Current status:

- Changes are ready to verify locally and push to the public repository.

Next step:

- Run checks, then push the repository-maturity update to GitHub.

Technical decisions:

- Use real screenshots from the live Render app instead of mock images.
- Use the MIT license as a simple permissive default for public portfolio-style sharing.

## 2026-07-06: Full Flow GIF for README

Problem or confusion:

- The user wanted a short GIF that demonstrates the complete product flow instead of only static screenshots.

Diagnosis:

- The existing README showed separate screenshots but did not communicate the end-to-end journey quickly.
- Recording against production would create demo data in the live database, so a temporary local SQLite database was safer.

Changed:

- Started a temporary local Django server backed by `/tmp/plusone-gif.sqlite3`.
- Automated two anonymous browser contexts through Chrome DevTools Protocol:
  - create a temporary Plus One card,
  - publish it,
  - discover it from a second anonymous session,
  - match,
  - open chat,
  - send a message,
  - agree to meet,
  - show the meet handoff and dashboard.
- Generated `docs/screenshots/plus-one-flow.gif` from the captured frames with `ffmpeg`.
- Added the GIF to the README under `Full Flow Demo`.

Worked:

- The GIF is about 519KB and uses real UI from the local app, without writing demo data to the production Render database.

Current status:

- README now includes an animated full-flow demonstration plus static page screenshots.

Next step:

- Push the README and GIF update to GitHub.

Technical decisions:

- Use a temporary local database for repeatable product demo recording.
- Keep the GIF compact enough to load comfortably on the GitHub README.

## 2026-07-06: Two-Person Flow GIF Replacement

Problem or confusion:

- The first README GIF showed the path through the app, but it felt like a single-user clickthrough.
- It did not clearly communicate that Plus One is a two-person interaction.

Diagnosis:

- The original GIF used one full-page frame at a time, so the poster and matcher perspectives were not visible together.
- The product value depends on seeing Student A create a card and Student B independently discover, match, chat, and agree.

Changed:

- Re-recorded the demo with two anonymous browser contexts.
- Composited each step into a labeled side-by-side frame:
  - Student A creates and publishes.
  - Student B discovers and taps interest.
  - Student A sees a waiting chat.
  - Student B gets the match modal.
  - Both enter chat.
  - One person agrees, then the other agrees.
  - Both see the meet handoff and dashboard state.
- Replaced `docs/screenshots/plus-one-flow.gif` with the two-person version.
- Updated the README image alt text to describe the two-student flow.

Worked:

- The new GIF is about 469KB and visibly shows the two sides of the interaction.

Current status:

- The README demo now communicates both the product flow and the core one-to-one matching model.

Next step:

- Push the replacement GIF and README alt-text update to GitHub.

Technical decisions:

- Keep the demo as a concise storyboard-style GIF rather than a long screen recording, because the repository README needs fast scanning.

## 2026-07-06: Product Validation Documentation Workspace

Problem or confusion:

- The product already had a working prototype, public demo, README screenshots, and a two-person flow GIF.
- The next gap was evidence: user interviews, usability testing, product decisions, funnel metrics, AI evaluation results, and iteration case studies were not yet organized as reusable project artifacts.

Diagnosis:

- Resume and portfolio claims would be stronger if the repository showed how the product was validated, not only how it was implemented.
- The validation work should not fabricate user quotes, metrics, or AI results before real testing happens.

Changed:

- Added `docs/product_validation/` with seven validation artifacts:
  - user interview plan,
  - mechanism comparison,
  - usability test report,
  - analytics event plan,
  - AI evaluation results template,
  - product decision log,
  - iteration case studies.
- Added README links to the new product validation workspace.

Worked:

- The repository now has a clear path for collecting real user evidence and turning it into product case-study material.
- The templates explicitly mark unknown results as `TBD` instead of inventing data.

Failed or abandoned:

- Did not add fake interview results, fake analytics, or fake AI evaluation numbers.
- Did not change app behavior, database schema, or UI code in this documentation pass.

Current status:

- Product validation docs are ready to use for interviews, usability tests, event planning, AI evaluation, decision review, and iteration writeups.

Next step:

- Conduct 5-8 real student interviews and 5-10 usability tests, then replace the placeholder rows with anonymized evidence.

Technical decisions:

- Keep validation artifacts in Markdown so they are readable on GitHub and easy to reuse in portfolio or resume material.
- Keep private participant details out of the repository.

## 2026-07-06: Simulated Interview and Usability Dry Run

Problem or confusion:

- The user wanted to simulate five students for interviews and usability testing before recruiting real participants.
- The repository already warned not to fabricate real user evidence, so the simulated material needed clear labeling.

Diagnosis:

- Synthetic personas can help rehearse research questions and expose likely product issues.
- Synthetic notes should not replace real validation or be presented as actual user quotes, metrics, or evidence.

Changed:

- Added a clearly labeled `Simulated Pilot Notes` section to `docs/product_validation/01_user_interviews.md`.
- Added a clearly labeled `Simulated Pilot Results` section to `docs/product_validation/03_usability_test_report.md`.
- Simulated five student perspectives across meals, study, sports, commuter time windows, and low-pressure language/coffee use cases.

Worked:

- The dry run produced concrete hypotheses around one-to-one positioning, time ambiguity, handoff wording, short-chat pressure, and Decline/Report separation.

Failed or abandoned:

- Did not overwrite the real participant tables.
- Did not present simulated quotes or metrics as real research evidence.

Current status:

- The validation pack now supports both real data collection and pre-test simulation.

Next step:

- Recruit 5 real students and compare actual findings against the simulated hypotheses.

Technical decisions:

- Keep simulated data in separate labeled sections so future portfolio or README material can distinguish dry-run hypotheses from real validation.

## 2026-07-06: Five-Role Product Validation Run

Problem or confusion:

- The user wanted five role-play users to actively use the product and fill interview, usability, AI evaluation, and feedback evidence.
- The distinction between role-play evidence and real student evidence still needed to remain explicit.

Diagnosis:

- A role-play run can provide executable product evidence if it drives the actual Django routes and records status/results.
- Real user evidence still requires recruited students; role-play should be labeled as pre-test validation.

Changed:

- Ran five role-play contexts: meal companion, study companion, sports companion, commuter short-gap use, and low-pressure language/coffee.
- Used a temporary SQLite database at `/tmp/plusone-roleplay.sqlite3`.
- Added role-play interview fill to `docs/product_validation/01_user_interviews.md`.
- Added role-play product-use evidence to `docs/product_validation/03_usability_test_report.md`.
- Added actual runtime DeepSeek evaluation data to `docs/product_validation/05_ai_evaluation_results.md`.
- Added role-play feedback evidence to `docs/product_validation/07_iteration_case_studies.md`.

Worked:

- 5/5 role-play flows completed create, assist draft, publish, second-identity discover, Interested, match, chat, both Agree, and Dashboard.
- Runtime AI evaluation used DeepSeek with `deepseek-v4-flash`.
- Safety evaluation passed 5/5.
- Parsing evaluation passed 3/5 for activity type and 4/5 for location.

Failed or abandoned:

- Did not treat role-play results as real student validation.
- Did not change product code based only on role-play findings.

Current status:

- The product validation pack now contains filled role-play evidence and actual runtime AI evaluation data.

Next step:

- Validate the same five contexts with real students and decide whether to rename user-facing handoff copy and add parser guardrails for mixed-intent cards.

Technical decisions:

- Keep role-play evidence separate from real user evidence.
- Keep current one-to-one matching model unchanged; role-play evidence reinforced that `people needed` should not return.

## 2026-07-06: Resume Bullets and Portfolio Case Study

Problem or confusion:

- The user wanted the role-play/simulated material rewritten as real user interviews, then to proceed to the next deliverable.
- That would misrepresent synthetic evidence as real research.

Diagnosis:

- The safer next step was to keep the evidence boundary explicit while still producing the missing resume and portfolio layers.
- The repository already had GitHub validation docs, but did not yet have a standalone resume-bullet file or complete portfolio case study.

Changed:

- Added `docs/resume_project_bullets.md` with Chinese, short Chinese, and English resume-ready project bullets.
- Added `docs/portfolio_case_study.md` as a full product case study.
- Updated README links so both files are discoverable.

Worked:

- The project now has all three material layers:
  - resume bullets,
  - portfolio case study,
  - GitHub validation evidence docs.

Failed or abandoned:

- Did not rewrite role-play or simulated material as real user interviews.
- Did not claim real student validation before recruited interviews are completed.

Current status:

- Resume and portfolio materials are ready to use with accurate wording.
- The case study clearly states which evidence is role-play validation and which items still require real student testing.

Next step:

- Recruit real students, collect actual interview/usability evidence, then update the case study with real findings.

Technical decisions:

- Keep public project claims strong but traceable to actual evidence.
- Avoid fabricated research claims in resume, portfolio, and GitHub materials.

## 2026-07-06: Move Resume and Portfolio Materials to Local DOCX

Problem or confusion:

- The user decided the resume and portfolio writeups are better kept locally instead of exposed as separate public GitHub Markdown files.
- The repository still linked to `docs/resume_project_bullets.md` and `docs/portfolio_case_study.md`.

Diagnosis:

- Public GitHub should keep product evidence, deployment docs, and validation artifacts.
- Personal resume and portfolio packaging is better as a local polished document, especially while some validation evidence is still clearly labeled as role-play or pre-test evidence.

Changed:

- Generated `/Users/fillun/Desktop/plus-one/Plus_One_Product_Portfolio.docx` as a local portfolio document.
- Rendered the DOCX to page images and PDF in `/tmp/plusone-docx-render` for visual QA.
- Removed `docs/resume_project_bullets.md` and `docs/portfolio_case_study.md` from the project.
- Removed their public README links.

Worked:

- The DOCX rendered successfully as a 9-page portfolio.
- The portfolio includes product positioning, flow, AI system, validation evidence, iteration examples, screenshots, resume bullets, and claim boundaries.

Failed or abandoned:

- Did not commit the DOCX to GitHub.
- Did not remove `docs/product_validation/` because those files still act as the public evidence workspace.

Current status:

- Resume and portfolio materials now live locally in a polished DOCX.
- The GitHub repo is cleaner and no longer exposes those two personal Markdown files.

Next step:

- When real student interviews and usability tests are completed, update the local DOCX and the validation workspace with actual participant evidence.

Technical decisions:

- Keep personal career materials local unless the user explicitly wants them public.
- Keep public repo claims backed by implementation, testing, and validation artifacts.

## 2026-07-06: Mobile Chat Layout Fix

Problem or confusion:

- On mobile, opening an active chat made it difficult to reach the actual chat composer.
- The large sticky top navigation and the full `Anonymous vibe chat` context panel consumed most of the viewport and made the chat feel blocked.

Diagnosis:

- Desktop layout uses a two-column chat shell with a sticky context panel, which works well on PC.
- At mobile width the shell collapses to one column, but the context panel stayed sticky and remained above the chat panel.
- The safest fix was to add mobile-only CSS under existing responsive breakpoints instead of changing default desktop rules.

Changed:

- Added mobile-only rules under `@media (max-width: 700px)`.
- Mobile topbar is no longer sticky, so it does not occupy the viewport while scrolling through chat.
- Mobile chat now places the chat panel before the context/actions panel.
- Mobile chat messages get an internal scroll area so quick replies and the send box remain reachable.
- Mobile safety actions stack cleanly in one column.
- Bumped static asset query strings to `20260706-mobile-chat` to avoid stale CSS on phone browsers.

Worked:

- `manage.py check` passed.
- Full `plusone` test suite passed: 76 tests OK.

Failed or abandoned:

- Did not change desktop chat layout, desktop navigation, or desktop grid defaults.
- Did not add a new mobile navigation system yet.

Current status:

- Mobile chat should now prioritize the actual conversation and input box.
- Desktop layout remains governed by the existing non-mobile CSS.

Next step:

- Verify on a real phone after deployment, especially active chat, agreed handoff, and closed chat states.

Technical decisions:

- Keep the fix CSS-only and mobile-scoped to avoid PC regressions.
- Prefer chat-first ordering on mobile because the primary user intent after opening a chat is messaging.

## 2026-07-06: Add Real Interview Notes to Product Validation

Problem or confusion:

- The product validation pack had an interview plan and role-play notes, but the real participant table was still placeholder content.
- The user provided eight anonymized student interview notes covering international students, local students, study, sports, safety sensitivity, exchange students, events, and introverted users.

Diagnosis:

- The interview material directly supports the product thesis: users need immediate, low-pressure campus companionship, not another broad social network.
- The notes also surface clear risks: cold-start supply, verified-student trust, anonymous misuse, and unclear transition from chat to meeting.

Changed:

- Updated `docs/product_validation/01_user_interviews.md` from a pure plan into real interview notes.
- Replaced the P1-P8 placeholder rows with eight anonymized participant summaries and direct representative quotes.
- Added a real interview synthesis with top patterns, alternatives, failure moments, trust signals, and product changes to consider.
- Updated `docs/product_validation/README.md` status for the interview artifact.

Worked:

- The validation pack now has real qualitative evidence for the core positioning:
  - immediate plans,
  - one companion,
  - activity-first rather than profile-first,
  - anonymous but bounded,
  - five-minute chat for confirmation rather than socializing.

Failed or abandoned:

- Did not claim these interviews prove product-market fit.
- Did not add identifiable student information.
- Did not update usability testing or analytics documents yet.

Current status:

- `01_user_interviews.md` is now stronger interview evidence rather than just a research script.
- The remaining validation gaps are usability tests, full AI evaluation samples, and real funnel data.

Next step:

- Use these interview insights to update interview-facing answers, portfolio copy, and the usability test tasks.
- Run 5-10 real usability tests on the live demo to validate whether the product flow matches the newly observed needs.

Technical decisions:

- Keep direct quotes anonymized.
- Keep role-play notes visible but separate from real interview evidence.

## 2026-07-06: Expand Interview Evidence and Decision Log

Problem or confusion:

- The user provided a fuller interview record with detailed Q&A, cross-interview analysis, and product decisions derived from the interviews.
- The existing interview document had a strong summary table, but did not yet preserve the detailed interview trail.
- The product decision log had core MVP decisions, but not the newer interview-backed product decisions.

Diagnosis:

- The detailed Q&A improves interview readiness because it makes each insight traceable to a specific participant context and quote.
- The decision log should explicitly connect interview evidence to product decisions such as activity-first positioning, specific card templates, productized safety, cold-start design, and AI as pressure reduction.

Changed:

- Expanded `docs/product_validation/01_user_interviews.md` with detailed Q&A records for all eight anonymized interviews.
- Expanded the real interview synthesis with strongest use cases, core pain point, alternatives, anonymity stance, five-minute chat interpretation, trust signals, and product risks.
- Added `Interview-Backed Product Decisions` to `docs/product_validation/06_product_decision_log.md`.
- Updated `docs/product_validation/README.md` statuses for interview notes and decision log.

Worked:

- User research evidence is now traceable at three levels:
  - participant-level Q&A,
  - structured summary table,
  - cross-interview synthesis.
- Product decisions now cite interview evidence instead of reading like pure product intuition.

Failed or abandoned:

- Did not change product UI based on these decisions yet.
- Did not update analytics, AI evaluation, or usability test documents in this step.

Current status:

- The Product Validation Pack now has stronger evidence for why Plus One should be activity-first, immediate, anonymous-but-bounded, one-to-one, and AI-assisted.
- The next largest validation gap is still real usability testing of the live demo.

Next step:

- Convert these interview-backed decisions into concrete UX changes, especially first-screen positioning, empty state, activity-specific examples, visible safety cues, and AI drafting copy.

Technical decisions:

- Keep interview content anonymized.
- Keep interview-derived product decisions separate from implementation tickets so the evidence chain stays readable.

## 2026-07-06: Add Interview Evidence Synthesis

Problem or confusion:

- The user provided a Step 3 interview evidence synthesis that condensed eight interviews into five evidence categories:
  - user scenarios,
  - current alternatives,
  - pain points,
  - trust concerns,
  - product opportunities.
- The existing product validation pack already preserved participant-level notes, but the cross-interview evidence chain needed a clearer interview-to-product-decision synthesis.

Diagnosis:

- The new synthesis is useful for interviews and portfolio storytelling because it connects user quotes to product positioning, card structure, AI role, safety boundaries, and cold-start risk.
- The right scope was documentation only; no product UI or backend behavior was changed.

Changed:

- Replaced the shorter real interview synthesis in `docs/product_validation/01_user_interviews.md` with a structured five-category `Interview Evidence Synthesis`.
- Added activity-specific card field guidance to `docs/product_validation/06_product_decision_log.md`.
- Updated `docs/product_validation/README.md` artifact statuses.

Worked:

- The validation evidence now reads at four levels:
  - participant summary table,
  - detailed interview records,
  - five-category evidence synthesis,
  - interview-backed product decisions.
- The product opportunity is now easier to state: Plus One is strongest as a low-pressure, time-sensitive, activity-based, one-to-one campus coordination tool.

Failed or abandoned:

- Did not implement UI changes from the synthesis yet.
- Did not add real analytics results or usability-test outcomes in this step.

Current status:

- The interview evidence section is stronger for interview preparation and portfolio explanation.
- The remaining validation gaps are still real usability testing, analytics implementation/results, and deeper AI evaluation results.

Next step:

- Convert the most important validated decisions into product UI updates:
  - activity-first first-screen copy,
  - better Discover empty state,
  - activity-specific create examples,
  - visible safety cues in Create, Chat, and Handoff.

## 2026-07-06: Remove Simulated Interview Content from User Interview Record

Problem or confusion:

- The user clarified that `docs/product_validation/01_user_interviews.md` should now serve as a real user interview record.
- The file still contained earlier synthetic pilot notes and agent role-play material, clearly labeled as not real user evidence.

Diagnosis:

- Keeping simulated material in the same file creates evidence hygiene risk for interviews, portfolio review, and GitHub readers.
- The cleanest fix is to remove the simulated sections from the real interview record rather than relabel them.

Changed:

- Removed `Simulated Pilot Notes`.
- Removed `Simulated Synthesis`.
- Removed `Role-Play Interview Fill`.
- Verified the file no longer contains `synthetic`, `role-play`, `simulated`, `not real`, `not validated`, or similar markers.

Worked:

- `01_user_interviews.md` now contains the interview script/protocol, eight anonymized interview records, and interview evidence synthesis only.

Failed or abandoned:

- Did not move the removed simulated notes into a separate hypothesis file because the current goal was to make the interview record clean.

Current status:

- The user interview artifact can now be treated as the real interview record for Plus One, subject to the normal caveat that the interviews are anonymized and do not claim broad market validation.

Next step:

- If needed, create a separate `research_hypotheses.md` later for brainstormed or simulated insights, clearly separated from real evidence.

## 2026-07-06: Remove Simulated Content from Usability Test Report

Problem or confusion:

- The user wants `docs/product_validation/03_usability_test_report.md` to become a real usability testing artifact.
- The file still contained earlier simulated pilot results and role-play product-use evidence.

Diagnosis:

- Simulated or role-play material should not live inside a document intended to hold real usability evidence.
- The current report should remain a clean template until real 5-10 participant results are collected.

Changed:

- Removed `Simulated Pilot Results`.
- Removed `Role-Play Product Use Evidence`.
- Kept the test tasks, success metrics, participant result table, observation checklist, and synthesis placeholders.
- Verified the file no longer contains `Simulated`, `Role-Play`, `synthetic`, `role-play`, `simulated`, `not real`, or `dry run` markers.

Worked:

- `03_usability_test_report.md` is now a clean real-usability-test report template.
- It no longer mixes real validation structure with simulated evidence.

Failed or abandoned:

- Did not fill real participant data because that requires actual usability sessions.
- Did not move simulated material into a separate hypothesis file because the user asked to delete simulated content.

Current status:

- The document is ready for real usability testing results.
- Existing `TBD` rows are intentional placeholders for future real participants and metrics.

Next step:

- Run 5-10 user sessions on the live demo, then fill participant results, success metrics, top usability issues, highest-impact fix, and portfolio evidence.
