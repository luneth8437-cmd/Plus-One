# Test Case Catalog

Automated tests run against SQLite and PostgreSQL on every push (`.github/workflows/ci.yml`), plus a
non-test benchmark suite (`evaluate_ai`) whose report uploads as a CI
artifact. Philosophy: every bug found by usability testing or by the
benchmark becomes a permanent regression test.

## Suites

| Suite | File | Count | Covers |
| --- | --- | --- | --- |
| Core product | `plusone/tests.py` | 76 | Anonymous identity create/reset; post publish/edit/cancel; discovery filter rules; swipe/match concurrency outcomes; chat permissions and expiry; agreement handoff; moderation blocking + logging; dashboard state separation; AI parsing fallback and mocked moderation-provider paths |
| Draft guardrails | `plusone/tests_validation.py` | 17 | Explicit-date extraction (month-name, day-month, ISO, numeric, weekday, year rollover); expiry clamping incl. the observed 1440-minute case (U3); U6-U10 date-shift regression; missing-time warning (U1-U5); publish blocked on date conflict until confirmed |
| Opening assistant | `plusone/tests_opening.py` | 16 | Context assembly without identity leakage; shared-interest computation; validation drops unsafe/probing/overlong/duplicate outputs; personalized deterministic fallback; mocked-LLM end-to-end + top-up; failure -> fallback with logging; view renders without sending; closed-chat refusal; non-participant 403; injection defense (field stripping, per-token interest filtering, sanitize_context, output probes) |
| Analytics | `plusone/tests_analytics.py` | 9 | publish/match/message/first-reply/agree/meetup-confirmed events; opener adoption classification (verbatim/edited/none); funnel_report aggregation; **privacy: no chat text ever stored in events** |

## Edge Conditions Explicitly Pinned

- Race: two swipes on the last slot of one card (row lock + sqlite lock retry
  fallback paths).
- Time: past start_time rejection with grace window; explicit date never
  rolled forward; expected end must follow the start and defaults to one hour;
  expiry clamped to 5-180.
- Lifecycle: chat writes after decline/expire refused; identity reset closes
  live cards and chats; countdown renders a labeled state, never "--".
- Adversarial: prompt injection via interests/major fields; personal-info
  probes in LLM output; explicit debug-only rules and production moderation fail-closed behavior.

## Reliability regression suites (September 2026)

- `tests_reliability.py`: waiting/foreground activation/expiry; terminal replay; UUID conflicts; reset fencing; edit-versus-match; reports after agreement; shared quota; server-time/cursor/consent payloads; total AI deadline cancellation. PostgreSQL-only tests exercise real competing transactions.
- `tests_retention.py`: last-seen throttling, background exclusions, live/future/expected-end+24h and unresolved-report protection, legacy start-time fallback, report/evidence cap, bounded identity batches, dry-run and repeatability.
- `tests_ai_safety.py`: mocked unavailable/malformed/timeout providers; no production rules fallback; word boundaries and Chinese/English contact cases; budgets and no SDK retries.
- `tests_migrations.py`: real 0009-to-latest migration with old rows, unchanged chat deadlines, nullable compatibility fields and evidence-based attribution recovery.
- `tests/js/`: cursor isolation, sorting/deduplication, single in-flight polling and retry behavior.
- `tests/browser/`: real isolated browser contexts, waiting-to-chat activation, cross-client messages, weak-network retry, agreement/report and responsive keyboard checks.

The older suite counts above are historical inventory, not acceptance criteria. Current execution results are in `docs/project_progress_log.md`. Production data is never used as a test fixture.

## Beyond Unit Tests

- `evaluate_ai` (CI): 19 parsing cases (U1-U10 regressions), 12-sample
  moderation confusion matrix, 9 opener cases incl. 3 injection attacks.
- `evaluate_ai --use-llm` (manual, needs key): live-model comparison +
  LLM-as-judge scores.
- `calibrate_judge`: blind human scoring sheet -> judge-human agreement
  (MAD, Pearson r) - the evaluation of the evaluator.
- `seed_funnel_demo` + `funnel_report`: instrumentation smoke test through
  real service paths.
