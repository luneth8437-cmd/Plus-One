# Product Validation

This folder turns Plus One from a working prototype into a product case study with evidence.

Do not invent user quotes, metrics, or model results. Use these files as the operating system for collecting real evidence, then replace the placeholder rows with actual findings.

## Validation Artifacts

| File | Purpose | Status |
| --- | --- | --- |
| [01_user_interviews.md](01_user_interviews.md) | Understand how students currently find meal, study, sport, and event companions. | Detailed 8-interview record and evidence synthesis logged |
| [02_mechanism_comparison.md](02_mechanism_comparison.md) | Compare the decision mechanics behind Plus One and adjacent products. | Draft framework |
| [03_usability_test_report.md](03_usability_test_report.md) | Record whether users can complete the full create-match-chat-handoff flow. | 10-session usability report logged |
| [04_analytics_event_plan.md](04_analytics_event_plan.md) | Define the minimum funnel events needed before real launch testing. | Updated from usability-test findings |
| [05_ai_evaluation_results.md](05_ai_evaluation_results.md) | Measure parsing and moderation quality across realistic samples. | Updated from usability-observed AI risks |
| [06_product_decision_log.md](06_product_decision_log.md) | Explain the core product choices behind the MVP. | Interview- and usability-backed decisions added |
| [07_iteration_case_studies.md](07_iteration_case_studies.md) | Connect observed issues to design and engineering changes. | Updated with real-evidence iteration cases |

## Recommended Order

1. Run 5-8 user interviews before adding more features.
2. Run 5-10 usability tests on the live Render demo.
3. Run a dedicated AI parsing/moderation benchmark for the highest-risk draft failures.
4. Convert the strongest three iteration cases into portfolio slides.
5. Add 1-2 real funnel metrics after the analytics events are implemented.

## Evidence Rules

- Use direct user quotes only when they are real and anonymized.
- Store no private names, student IDs, API keys, or contact details in the repository.
- Mark assumptions clearly when a claim is not yet validated.
- Keep screenshots free of private chat content unless the content is clearly non-private test data.
