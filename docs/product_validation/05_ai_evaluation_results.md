# AI Evaluation Results

Goal: evaluate whether AI-assisted parsing and safety moderation are useful enough for the MVP, and decide where deterministic guardrails must stay in the product.

Current evidence source: `03_usability_test_report.md`, based on a 10-session usability test of the create-match-chat-agree-handoff-dashboard flow.

Important boundary: this document reports usability-observed AI risks. It does not claim a full benchmark of all parsing or moderation behavior yet.

## Current Evidence From Usability Testing

| Evidence area | Result | Source | Product meaning |
| --- | --- | --- | --- |
| Draft missing required start time | 5/5 users in U1-U5 needed manual time fixes after casual-text drafting. | U1-U5 in usability report | AI-assisted creation reduces writing effort, but the review step must make missing required fields impossible to miss. |
| Invalid expiry value | U3 received a draft with missing start time and a 1440-minute expiry, then publish stayed on the create form. | U3 in usability report | Draft-generated numeric values need bounds before they reach the publish form. |
| Date shift | U6-U10 used explicit date/time, but July 8 became Jul 7 in the structured card. | U6-U10 in usability report | Date parsing needs validation against the user's original text before publish. |
| User trust in filled fields | U6 said: "This one filled the time, so I trust the draft more." | U6 in usability report | Filled fields create confidence even when they are wrong, so silent AI errors are more dangerous than blank fields. |
| Safety confusion | 0 critical safety confusion cases; 5/10 had mild concern about anonymous meetups. | Success metrics in usability report | UI safety guidance was understandable, but this does not replace a dedicated moderation benchmark. |

## Result Summary

| Metric | Current result |
| --- | --- |
| Create flow completion | 9/10 completed the flow, but several completions required manual correction. |
| Draft field reliability | Not acceptable for auto-publish. Missing start time and date shift appeared repeatedly. |
| Time/date handling acceptable rate | Not acceptable based on usability evidence: 10/10 relevant draft sessions exposed either missing start time or wrong date. |
| Expiry handling | Needs guardrails: one observed 1440-minute generated expiry blocked the user. |
| Safety UX confusion | 0 critical cases in usability testing. |
| Dedicated moderation recall | Not yet measured in a real benchmark after the usability report. |
| Fallback behavior | Not evaluated in the current usability report. |
| Latency | Not measured in the current usability report. |

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

## Dedicated AI Benchmark Still Needed

The next evaluation should use real model or fallback outputs captured from actual runs. It should include:

| Benchmark area | Minimum sample | What to record |
| --- | --- | --- |
| Natural-language card parsing | 15 realistic student inputs across lunch, study, sports, coffee, language practice, and campus events. | Input, expected fields, actual output, missing fields, wrong fields, date/time handling, fallback use, latency, verdict. |
| Safety moderation | 5-10 post/chat samples including risky and benign controls. | Surface, input, expected allow/block, actual allow/block, category, false positive/false negative, fallback use, latency. |
| Usability-linked regression cases | At least the U1-U10 failure types. | Missing start time, July 8 to Jul 7 shift, invalid expiry, publish blocker clarity. |

## Interview and Usability Connection

The interview evidence in `01_user_interviews.md` showed that AI is valuable because it lowers the pressure of writing a casual invitation. The usability evidence in `03_usability_test_report.md` shows the boundary of that value:

> AI can make posting feel easier, but first-time success depends on the review step making missing or incorrect structure impossible to miss.

This is why AI should remain a confidence and drafting feature, not an autonomous publishing mechanism.
