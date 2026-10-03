# AI Evaluation Run

- Date: 2026-10-03 21:52 CST
- Pipeline: deterministic_fallback_with_guardrails
- Parsing suite: 2026-10-publishing-accuracy
- Parsing cases: 28 (0 with failures)
- October suite changes: unresolved mensa/gym/dining-hall names remain blank; ending times and source provenance are scored.
- This fixed-case benchmark is not a real-user study or evidence of live provider quality. Historical July reports use different expectations.
- Parsing latency: avg 0ms, p50 0ms, p95 1ms, max 2ms

## Parsing field accuracy

| Field | Correct | Total | Accuracy |
| --- | --- | --- | --- |
| activity_type | 20 | 20 | 100% |
| date | 13 | 13 | 100% |
| end_duration | 6 | 6 | 100% |
| expire_in_bounds | 1 | 1 | 100% |
| location | 19 | 19 | 100% |
| no_invented_end | 2 | 2 | 100% |
| no_invented_time | 6 | 6 | 100% |
| source_expected_end_time | 6 | 6 | 100% |
| time | 13 | 13 | 100% |
| time_present | 1 | 1 | 100% |
| warning_ambiguous_end_time | 1 | 1 | 100% |
| warning_missing_location | 4 | 4 | 100% |
| warning_missing_start_time | 6 | 6 | 100% |
| warning_suggested_end_time | 1 | 1 | 100% |

### Failing cases

None.

## Moderation confusion matrix

| | Flagged | Allowed |
| --- | --- | --- |
| Risky | TP 6 | FN 0 |
| Benign | FP 0 | TN 6 |

- Precision: 1.00
- Recall: 1.00


## Opening assistant (deterministic path)

- Cases: 9 (0 with failures)
- `sports_shared_interest`: ok
- `food_no_shared_interests`: ok
- `study_empty_profiles`: ok
- `explore_generic`: ok
- `club_fair_shared`: ok
- `unicode_shared_interest`: ok
- `injection_in_shared_interest`: ok
- `injection_in_major`: ok
- `probe_smuggled_in_interests`: ok
