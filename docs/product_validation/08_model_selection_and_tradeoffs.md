# Model Selection, Cost, and the Agent Boundary

Goal: document why Plus One uses deepseek-v4-flash behind a deterministic-first pipeline, what each AI call costs, and where agent-style AI is and is not justified in this product.

Evidence base: `05_ai_evaluation_results.md` (benchmark + LLM-vs-fallback comparison, 2026-07-14), `LLMLog` production records, and the pipeline code in `plusone/ai_services/`.

## AI Surfaces and Their Requirements

Each AI surface has a different failure cost and latency budget, which drives a different design.

| Surface | Task shape | Latency budget | Cost of a wrong answer | Design consequence |
| --- | --- | --- | --- | --- |
| Post parsing (`parse_activity_text`) | Closed: text → 6 fixed JSON fields | ~2s (user is waiting on the create form) | High: a wrong date/time can strand a real meetup | LLM output merged with rule parser, then guardrail validation; human review before publish |
| Safety moderation (`moderate_text`) | Closed: text → flag/allow + reason | <1s (blocks posting/chat) | Very high (false negative) / medium (false positive) | Rule layer always runs; LLM adds coverage; rules cannot be overridden by the LLM |
| Icebreaker (`icebreaker.py`) | Open: generate a friendly opener | Relaxed (post-match, non-blocking) | Low: a bad suggestion is just ignored | The only surface where generative freedom is acceptable |

## Model Choice: deepseek-v4-flash

| Criterion | deepseek-v4-flash (primary) | gpt-4o-mini (fallback provider) | Rule parser (always-on fallback) |
| --- | --- | --- | --- |
| Input / output price per 1M tokens | $0.14 / $0.28 (cache hit: $0.003 input) | $0.15 / $0.60 | $0 |
| Measured parse latency (thinking disabled) | avg 1556ms, p50 1474ms, p95 2850ms (19-case benchmark, 2026-07-14) | not benchmarked in this project | ~0ms |
| Regression-set accuracy with guardrails | 18/19 | not benchmarked | 18/19 |
| Chinese + English mixed input | Strong (relevant for a Chinese campus user base) | Adequate | English patterns only |
| API shape | OpenAI-compatible (one client codepath for both providers) | Native | n/a |

Why this pick, in order of weight: the OpenAI-compatible API means the provider is swappable via one env var (`DEEPSEEK_API_KEY` → `OPENAI_API_KEY`) with zero code change, so the choice is low-commitment by construction; output pricing is ~2x cheaper than the closest OpenAI equivalent; and reasoning ("thinking") can be disabled per call, which matters because a create-form parse must not spend seconds reasoning about a lunch invitation.

`DEEPSEEK_THINKING` is disabled by default in `client.py` for exactly this reason: parsing and moderation are closed tasks where reasoning tokens add latency and cost without accuracy gain — the 2026-07-14 benchmark shows the non-reasoning pipeline already matches the deterministic parser (18/19) on the regression set.

## Cost per Call and per User

Estimated from the actual prompts in `parsing.py` / `moderation.py` (system prompt ≈ 250 tokens including the campus location list, user text ≈ 30 tokens, JSON output ≈ 120 tokens):

| Item | Tokens (in / out) | Cost |
| --- | --- | --- |
| One parse call | ~300 / ~120 | ≈ $0.00008 |
| One moderation call | ~80 / ~60 | ≈ $0.00003 |
| One published post (1 parse + 2 moderation passes) | — | ≈ $0.00014 |
| 1,000 published posts | — | ≈ $0.14 |

The system prompt is identical across calls except for the timestamp, so DeepSeek context caching ($0.003/1M on cache hits) would cut the dominant input cost by ~98% at scale; not yet enabled because absolute cost is negligible at current volume. Conclusion: model cost is not a constraint for this product; latency and reliability are the binding constraints, which is why they — not price — drive the architecture.

## Degradation Ladder

Three tiers, checked in order at call time (`client.py`):

1. DeepSeek (primary) — used when `DEEPSEEK_API_KEY` is set and the call succeeds.
2. OpenAI-compatible fallback — same codepath, used when only `OPENAI_API_KEY` is set.
3. Deterministic rule pipeline — used when no key is configured or any LLM call raises; every LLM parse is also merged against the rule parse, so tier 3 is active even when tier 1 succeeds.

Every call logs provider, model, success, and latency to `LLMLog` (strategy `*_failed_rule_fallback` marks degradations), so the fallback trigger rate is measurable from production data rather than assumed.

The benchmark justifies calling tier 3 a peer, not a degraded mode: with guardrails applied, fallback and LLM both score 18/19 on the regression set, failing different low-severity cases. Users lose ~1.5s of latency when the LLM is up and lose almost no accuracy when it is down.

## Why the Core Loop Is Not Agent-Based

The high-level question: Plus One could route creation, matching, and chat through an agent loop (multi-step tool calls, memory, autonomous actions). It deliberately does not.

| Property the core loop needs | Deterministic pipeline + single LLM call | Agent loop |
| --- | --- | --- |
| Predictable latency on a blocking form | Yes (one bounded call, p95 2.85s) | No (variable step count) |
| Testable in CI without network | Yes (19-case benchmark runs on every push) | Hard (multi-step trajectories are expensive to evaluate) |
| Bounded worst-case behavior | Yes (guardrails clamp/block; human publishes) | Weaker (each added step compounds error) |
| Cheap at scale | Yes (~$0.0001/post) | 5-20x tokens per interaction |
| Actually required by the task | The task is closed-form field extraction | Overkill |

The product-safety version of the same argument: Plus One's riskiest moment is two anonymous students agreeing to meet. The design answer is that AI drafts and humans commit — the publish action, the match acceptance, and the meetup agreement are all human clicks on AI-prepared material. An agent that acts autonomously would move exactly the wrong actions across the human-commit line.

Where an agent would be justified: the post-match opening-assistant concept (read both activity cards, generate personalized openers with stated reasons). That task is open-ended, non-blocking, low-failure-cost, and benefits from multi-step context assembly — the profile that fits an agent. It is scoped as a candidate iteration, not shipped, and the decision rule it illustrates is the point: choose agent-shaped AI by task shape and failure cost, not by resume keywords.

## Decision Summary

| Decision | Rationale |
| --- | --- |
| deepseek-v4-flash as primary model | Cheapest adequate model with controllable reasoning and an escape hatch to OpenAI via one env var |
| Thinking disabled for parse/moderation | Closed tasks; benchmark shows no accuracy gain to pay latency for |
| Deterministic pipeline as peer, not backup | Accuracy parity (18/19 both) at ~0ms; product works with zero API spend |
| No agent loop in the core flow | Blocking latency, CI testability, bounded behavior, and the human-commit safety line all argue against it |
| Agent reserved for open-ended, low-stakes surfaces | Opening assistant fits the profile; core loop does not |

Pricing sources (checked 2026-07-14): [DeepSeek API pricing](https://api-docs.deepseek.com/quick_start/pricing/), [OpenRouter deepseek-v4-flash](https://openrouter.ai/deepseek/deepseek-v4-flash), [OpenAI API pricing](https://developers.openai.com/api/docs/pricing).
