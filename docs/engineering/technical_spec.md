# Technical Specification

Scope: how Plus One is built and why. Companion documents: `api_contract.md`,
`module_inventory.md`, `test_case_catalog.md`.

## System Overview

```
Browser (Django templates + vanilla JS polling)
  -> Django 5 views (request routing only)
    -> services/   (state transitions: matching, chat, identity, analytics)
    -> selectors/  (read-side query composition)
    -> ai_services/ (parse/openers may fall back; production moderation fails closed)
  -> PostgreSQL (prod, CI) / SQLite (dev, CI)
  -> DeepSeek API (OpenAI-compatible; required for production content writes)
```

Deployment: Python 3.13 pinned in `.python-version`; Render web service
(Gunicorn+Uvicorn, WhiteNoise static); PostgreSQL add-on; scheduled liveness
and readiness probes via GitHub Actions. `DEEPSEEK_API_KEY` lives server-side
only.

## Key Design Decisions

| Decision | Rationale |
| --- | --- |
| Anonymous cookie-session identity, no accounts | Removes signup friction for an MVP whose core promise is disposable identity; verified-student mode is a launch precondition, not an MVP feature |
| Services own writes, views never touch ORM state directly | State transitions (swipe->match, agree->handoff) need locking and event logging in exactly one place |
| Draft assistance may fall back; moderation never silently falls back in production | Unavailable moderation preserves input but postpones writing; browsing, exit and reporting remain available |
| AI drafts, human confirms, AI never auto-publishes/auto-sends | Usability evidence (U6): filled-but-wrong AI fields are trusted more than empty ones, so silent AI errors are the highest-risk failure |
| Server-side analytics events at the state-change site | Client loss cannot skew the funnel; chat text is never stored in events |
| Ten-minute bounded waiting, then one five-minute chat | Foreground presence activates once; consistent user/card/match lock order protects state, capacity and message cursors |

## Data Model

- `UserProfile` - display name, interests, campus area (profile fields are
  treated as untrusted input to AI prompts).
- `CampusLocation` - seeded by migration; parse targets.
- `ActivityPost` - temporary card; status ACTIVE/MATCHED/EXPIRED/CANCELLED,
  `expire_time` discovery TTL, expected meetup end, capacity fixed to 1.
- `Swipe` - unique (user, post) with interested/pass action.
- `Match` - unique (post, swiper); WAITING/CHATTING/AGREED/DECLINED/EXPIRED,
  waiting deadline, foreground timestamps, one-time start/deadline and consent flags.
- `ChatMessage` - messages incl. system rows (icebreaker, close notices).
- `LLMLog` - audit of every AI call and fallback: task type, strategy
  (includes prompt version), latency, success.
- `ProductEvent` - server-side analytics (see `04_analytics_event_plan.md`).
- `SafetyReport` - unique participant/match statement, processing state and notes.
- `RateLimitBucket` - database-coordinated user/scope/window counts and bounded UUID reservations.
- User profiles retain nullable last_seen_at / retired_at; post/message UUIDs are nullable only for historical compatibility.

## AI Pipeline Layers

1. **Parsing** (`ai_services/parsing.py`): LLM JSON-mode draft merged with a
   date-aware rule parser; `validate_draft` guardrails (explicit-date
   cross-check, expiry clamp, missing-field warnings); publish blocked on
   date conflict until confirmed.
2. **Moderation** (`ai_services/moderation.py`): production requires a valid external decision within five seconds; error/timeout/malformed result blocks writing. Explicit rules mode exists only for DEBUG development and deterministic tests; boundary-aware rules also validate suggestions.
3. **Opening assistant** (`ai_services/opening_assistant.py`): gather context
   (identity-stripped, injection-sanitized) -> LLM generation (prompt v2,
   student tone, reply mode with session-scoped memory) -> per-item
   validation -> deterministic fallback/top-up. Suggestions fill the input;
   the human sends.

Evaluation for all three layers lives in `eval_cases.py` + `evaluate_ai`
(field accuracy, moderation confusion matrix, adversarial injection cases,
LLM-as-judge with human calibration via `calibrate_judge`).

## Non-Functional Notes

Current acceptance and rollout rules: `optimization_delivery_2026-09-19.md`.
The latency/cost figures below are historical evaluation observations, not
measured performance of this revised release.

- Latency budget: page actions < 200ms without AI; AI-assisted actions show
  results in one round-trip (parse ~1.6s p95 2.1s measured).
- Cost: ~$0.00008 per parse on deepseek-v4-flash (see 08 doc).
- Known limits: `16.07`-style numeric dates can be misread as times;
  this/next-weekday disambiguation not implemented; polling (2s) instead of
  websockets is a deliberate MVP simplification.
