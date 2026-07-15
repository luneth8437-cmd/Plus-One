# Module Inventory

Build/review order follows the product's dependency chain: identity ->
supply -> matching -> chat -> AI assistance -> instrumentation -> demo.

| # | Module | Files | Responsibility | Priority |
| --- | --- | --- | --- | --- |
| 1 | Identity | `services/identity.py` | Anonymous session creation/reset; profile defaults | P0 - everything assumes it |
| 2 | Cards (supply) | `forms.py`, `services/posts.py`, `services/expiration.py` | Publish/edit/cancel/expire temporary cards | P0 |
| 3 | Matching | `services/matching.py`, `services/capacity.py` | Swipe -> lock -> one-to-one match; capacity + lock-retry | P0 |
| 4 | Chat + agreement | `services/chat.py` | Five-minute chat, mutual agree -> handoff, decline/report | P0 |
| 5 | AI parsing + guardrails | `ai_services/parsing.py`, `ai_services/validation.py` | Casual text -> validated draft; date alignment; publish blockers | P1 - the create-flow differentiator |
| 6 | Moderation | `ai_services/moderation.py` | Rules-as-floor + LLM union on posts and messages | P1 |
| 7 | Opening assistant | `ai_services/opening_assistant.py` | Context gathering, injection sanitization, generation (v2 prompt, reply mode), validation, fallback | P1 |
| 8 | Analytics | `models.ProductEvent`, `services/analytics.py`, `management/commands/funnel_report.py` | Server-side funnel + opener adoption attribution | P1 - decisions depend on it |
| 9 | Evaluation | `ai_services/eval_cases.py`, `management/commands/evaluate_ai.py`, `calibrate_judge.py` | Benchmarks, adversarial cases, LLM-as-judge + human calibration | P1 - runs in CI |
| 10 | Demo mode | `services/demo.py` | Labeled supply + auto-partner so one visitor sees the full loop | P2 - portfolio/demo concern |
| 11 | Read-side | `selectors.py`, `presenters.py` | Query composition and template payload shaping | P2 |
| 12 | Ops | `seed_demo`, `seed_funnel_demo`, `expire_records`, `cleanup_anonymous_sessions` | Seeding, TTL sweeps, retention | P2 |

Frontend: one CSS file, one JS file (countdowns, chat polling/async send,
quick replies + opener click tracking, create-form live preview). No build
step by design.
