# Technical Specification

Scope: how Plus One is built and why. Companion documents: `api_contract.md`,
`module_inventory.md`, `test_case_catalog.md`.

## System Overview

```
Browser (Django templates + vanilla JS polling)
  -> Django 5 views (request routing only)
    -> services/   (state transitions: matching, chat, identity, analytics)
    -> selectors/  (read-side query composition)
    -> ai_services/ (parse / moderate / openers, each with deterministic fallback)
  -> PostgreSQL (prod) / SQLite (dev, CI)
  -> DeepSeek API (OpenAI-compatible; optional at runtime)
```

Deployment: Render web service (Gunicorn+Uvicorn, WhiteNoise static),
PostgreSQL add-on, keep-alive ping via GitHub Actions. `DEEPSEEK_API_KEY`
lives server-side only.

## Key Design Decisions

| Decision | Rationale |
| --- | --- |
| Anonymous cookie-session identity, no accounts | Removes signup friction for an MVP whose core promise is disposable identity; verified-student mode is a launch precondition, not an MVP feature |
| Services own writes, views never touch ORM state directly | State transitions (swipe->match, agree->handoff) need locking and event logging in exactly one place |
| Every AI call has a deterministic fallback that also acts as a guardrail | Measured: fallback parses activity type at 95% vs LLM 89%, at ~0ms vs 1.6s; the product must degrade to "slightly less smart", never to "broken" |
| AI drafts, human confirms, AI never auto-publishes/auto-sends | Usability evidence (U6): filled-but-wrong AI fields are trusted more than empty ones, so silent AI errors are the highest-risk failure |
| Server-side analytics events at the state-change site | Client loss cannot skew the funnel; chat text is never stored in events |
| Five-minute chat TTL with row-level locking on match creation | Concurrency-safe one-to-one promise (unique (post, swiper), select_for_update, sqlite lock retry) |

## Data Model (7 tables)

- `UserProfile` - display name, interests, campus area (profile fields are
  treated as untrusted input to AI prompts).
- `CampusLocation` - seeded by migration; parse targets.
- `ActivityPost` - temporary card; status ACTIVE/MATCHED/EXPIRED/CANCELLED,
  `expire_time` TTL, capacity fixed to 1.
- `Swipe` - unique (user, post) with interested/pass action.
- `Match` - unique (post, swiper); CHATTING/AGREED/DECLINED/EXPIRED,
  five-minute `chat_expires_at`, two agreed flags.
- `ChatMessage` - messages incl. system rows (icebreaker, close notices).
- `LLMLog` - audit of every AI call and fallback: task type, strategy
  (includes prompt version), latency, success.
- `ProductEvent` - server-side analytics (see `04_analytics_event_plan.md`).

## AI Pipeline Layers

1. **Parsing** (`ai_services/parsing.py`): LLM JSON-mode draft merged with a
   date-aware rule parser; `validate_draft` guardrails (explicit-date
   cross-check, expiry clamp, missing-field warnings); publish blocked on
   date conflict until confirmed.
2. **Moderation** (`ai_services/moderation.py`): keyword rules are a hard
   floor, LLM verdicts can only add flags, never remove them.
3. **Opening assistant** (`ai_services/opening_assistant.py`): gather context
   (identity-stripped, injection-sanitized) -> LLM generation (prompt v2,
   student tone, reply mode with session-scoped memory) -> per-item
   validation -> deterministic fallback/top-up. Suggestions fill the input;
   the human sends.

Evaluation for all three layers lives in `eval_cases.py` + `evaluate_ai`
(field accuracy, moderation confusion matrix, adversarial injection cases,
LLM-as-judge with human calibration via `calibrate_judge`).

## Non-Functional Notes

- Latency budget: page actions < 200ms without AI; AI-assisted actions show
  results in one round-trip (parse ~1.6s p95 2.1s measured).
- Cost: ~$0.00008 per parse on deepseek-v4-flash (see 08 doc).
- Known limits: `16.07`-style numeric dates can be misread as times;
  this/next-weekday disambiguation not implemented; polling (2s) instead of
  websockets is a deliberate MVP simplification.
