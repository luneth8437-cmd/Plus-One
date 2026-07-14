# AI Evaluation Results

This folder stores dated benchmark runs of the drafting pipeline and safety
moderation, produced by:

```
python manage.py evaluate_ai --report docs/product_validation/eval_results/$(date +%F)-fallback.md
python manage.py evaluate_ai --use-llm --report docs/product_validation/eval_results/$(date +%F)-llm.md
```

The benchmark cases live in `plusone/ai_services/eval_cases.py` and include
the regression cases from the 10-session usability test
(`03_usability_test_report.md`):

- U1-U5: casual text without a clear clock time must not get an invented
  start time, and the draft must carry a `missing_start_time` warning.
- U6-U10: explicit dates ("July 8", "16.7.", ISO dates) must not shift.
- U3: out-of-range expiry values (e.g. 1440 minutes) must be clamped to the
  5-180 minute product range.

Moderation is scored as a confusion matrix (precision/recall) over risky
samples plus benign controls.

Run the deterministic benchmark in CI; run the `--use-llm` variant manually
after any prompt change, and commit both reports so accuracy can be compared
across iterations.
