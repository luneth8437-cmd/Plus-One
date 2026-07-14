# AI Evaluation Results

Goal: evaluate whether AI-assisted parsing and safety moderation are useful enough for the MVP, and decide where deterministic guardrails must stay in the product.

Current evidence sources: `03_usability_test_report.md` (10-session usability test of the create-match-chat-agree-handoff-dashboard flow) and the automated benchmark in `plusone/ai_services/eval_cases.py`, run via `python manage.py evaluate_ai` (first measured baseline: 2026-07-14, report in `eval_results/2026-07-14-fallback.md`).

Both pipelines are now benchmarked (2026-07-14): the deterministic fallback (`eval_results/2026-07-14-fallback.md`) and the full LLM pipeline with guardrails (`eval_results/2026-07-14-llm.md`, deepseek-v4-flash).

## Current Evidence From Usability Testing

| Evidence area | Result | Source | Product meaning |
| --- | --- | --- | --- |
| Draft missing required start time | 5/5 users in U1-U5 needed manual time fixes after casual-text drafting. | U1-U5 in usability report | AI-assisted creation reduces writing effort, but the review step must make missing required fields impossible to miss. |
| Invalid expiry value | U3 received a draft with missing start time and a 1440-minute expiry, then publish stayed on the create form. | U3 in usability report | Draft-generated numeric values need bounds before they reach the publish form. |
| Date shift | U6-U10 used explicit date/time, but July 8 became Jul 7 in the structured card. | U6-U10 in usability report | Date parsing needs validation against the user's original text before publish. |
| User trust in filled fields | U6 said: "This one filled the time, so I trust the draft more." | U6 in usability report | Filled fields create confidence even when they are wrong, so silent AI errors are more dangerous than blank fields. |
| Safety confusion | 0 critical safety confusion cases; 5/10 had mild concern about anonymous meetups. | Success metrics in usability report | UI safety guidance was understandable, but this does not replace a dedicated moderation benchmark. |

## Measured Benchmark Results (2026-07-14, deterministic fallback + guardrails)

19 parsing regression cases (including the U1-U10 failure types) and 12 moderation cases (6 risky, 6 benign controls), run with `python manage.py evaluate_ai --report`. CI (`ci.yml`) reruns this benchmark on every push and uploads the report as the `ai-eval-report` artifact (run #18, commit `74bca7a`, green).

| Field / check | Result |
| --- | --- |
| activity_type | 18/19 (95%) |
| date | 11/11 (100%) — includes the former U6-U10 "July 8 → Jul 7" shift cases |
| time | 11/11 (100%) |
| location | 16/16 (100%) |
| expire_in_bounds | 1/1 (100%) — former U3 1440-minute case now clamped |
| no_invented_time | 5/5 (100%) — vague inputs no longer get a hallucinated time |
| warning_missing_start_time | 5/5 (100%) — former U1-U5 cases now surface an explicit warning |
| Cases fully passing | 18/19 (only miss: `u5_sometime_tomorrow` classified as FOOD instead of the expected type — low severity, user-correctable in review) |
| Moderation confusion matrix | TP 6, FP 0, TN 6, FN 0 — precision 1.00, recall 1.00 on this sample (rule layer) |

## LLM vs Fallback Comparison (2026-07-14, deepseek-v4-flash)

Same 19-case regression set, full LLM pipeline with guardrails (`--use-llm`):

| Metric | Deterministic fallback | LLM pipeline |
| --- | --- | --- |
| Cases fully passing | 18/19 | 18/19 |
| date / time | 11/11 / 11/11 | 11/11 / 11/11 |
| activity_type | 18/19 (miss: `u5_sometime_tomorrow` → FOOD) | 18/19 (miss: `language_practice_other` → study) |
| All guardrail checks (expiry clamp, no invented time, missing-time warning) | 100% | 100% |
| Latency | avg 0ms, p95 1ms | avg 1556ms, p50 1474ms, p95 2850ms |

Product reading: on this regression set the LLM adds ~1.5s median latency without an accuracy gain, because the guardrail layer already normalizes both pipelines' output. The two pipelines fail different edge cases (vague-input classification vs. borderline category), both low-severity and user-correctable in the review step. This supports keeping the deterministic pipeline as a first-class fallback rather than a degraded mode, and justifies the assist-not-auto-publish design: neither pipeline is reliable enough to skip human review, and both are reliable enough to draft.

## Result Summary

| Metric | Current result |
| --- | --- |
| Create flow completion | 9/10 completed the flow, but several completions required manual correction. |
| Draft field reliability | Not acceptable for auto-publish; guardrails now convert the observed failure modes into explicit warnings and publish blockers (see measured baseline above). |
| Time/date handling | Deterministic pipeline: 11/11 date and 11/11 time on the regression set, including all previously failing usability cases. |
| Expiry handling | Clamped to product bounds; 1/1 on the regression case. |
| Safety UX confusion | 0 critical cases in usability testing. |
| Dedicated moderation recall | 1.00 recall / 1.00 precision on a 12-case benchmark (small sample; expand before stronger claims). |
| Fallback behavior | Benchmarked head-to-head with the LLM pipeline: accuracy parity on the regression set (see comparison above). |
| Latency | Measured: fallback ~0ms; LLM avg 1556ms, p95 2850ms per parse call. |

## Error Taxonomy From Current Evidence

| Error type | Observed example | Severity | Required response |
| --- | --- | --- | --- |
| Missing required time | U1-U5 needed manual time fixes. | High | Highlight missing time, block publish, and ask the user to confirm or choose a time. |
| Incorrect date | U6-U10 requested July 8 but received Jul 7. | High | Compare parsed date to original text and show a date-confirmation warning. |
| Invalid numeric field | U3 received 1440-minute expiry. | Medium-high | Cap generated expiry values and show a clear inline publish blocker. |
| Over-trust in completed fields | U6 trusted the draft because time was filled. | High | Treat filled AI fields as provisional until the review validation passes. |
| Moderation uncertainty | Safety UX was understandable, but moderation recall was not benchmarked here. | Medium | Run a separate safety sample set before stronger launch claims. |

## Product Decision From Results

| Decision | Evidence | Rationale |
| --- | --- | --- |
| AI must assist, not auto-publish. | Missing time, wrong date, and invalid expiry appeared in the usability test. | Users should stay in control of the public card because AI output is helpful but not reliable enough to publish directly. |
| The review step must be defensive. | Users completed the flow only when they noticed or fixed draft issues. | The product should block invalid cards with clear inline messages instead of silently relying on user vigilance. |
| Parsed date/time must be validated against original text. | U6-U10 exposed a repeated July 8 to Jul 7 shift. | Date mistakes are high-risk because users may trust a filled time field. |
| Expiry values need bounds. | U3 saw a 1440-minute expiry. | A temporary Plus One card should not accept extreme AI-generated expiry values without correction. |
| Safety moderation still needs a dedicated benchmark. | Usability testing found 0 critical safety confusion cases, but did not systematically test moderation recall. | UX safety clarity and moderation accuracy are separate validation questions. |

## Required Guardrails Before Broader Launch

1. Highlight missing required fields immediately after AI draft.
2. Block publish when `start_time` is missing or invalid.
3. Compare parsed date/time with the source text when an explicit date is present.
4. Show a date-confirmation warning when the parser confidence is low or the date appears inconsistent.
5. Cap `expire_minutes` to the product range and explain the correction inline.
6. Keep deterministic fallback and validation even when the LLM call succeeds.
7. Keep manual review before publish as a core product rule.

## Benchmark Status and Next Steps

| Benchmark area | Status |
| --- | --- |
| Natural-language card parsing | Done in both modes: 19 cases, LLM vs fallback compared with latency (see comparison section). Next: grow the case set as new failure types appear in production logs. |
| Safety moderation | Done (rule layer): 12 cases, risky + benign controls, full confusion matrix. Next: expand sample and benchmark the LLM moderation path separately. |
| Usability-linked regression cases | Done: U1-U5 missing-time, U6-U10 date-shift, and U3 expiry cases are all encoded in `eval_cases.py` and pass. |
| Prompt versioning | Ongoing: every prompt change should rerun `evaluate_ai --report` and commit the report to `eval_results/` so accuracy deltas are traceable per version. |

## Interview and Usability Connection

The interview evidence in `01_user_interviews.md` showed that AI is valuable because it lowers the pressure of writing a casual invitation. The usability evidence in `03_usability_test_report.md` shows the boundary of that value:

> AI can make posting feel easier, but first-time success depends on the review step making missing or incorrect structure impossible to miss.

This is why AI should remain a confidence and drafting feature, not an autonomous publishing mechanism.
