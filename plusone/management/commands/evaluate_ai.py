"""Benchmark the Plus One drafting pipeline and safety moderation.

Examples:

    python manage.py evaluate_ai                 # deterministic pipeline (rule parser + guardrails)
    python manage.py evaluate_ai --use-llm       # full pipeline with the configured LLM
    python manage.py evaluate_ai --json          # machine-readable output
    python manage.py evaluate_ai --report docs/product_validation/eval_results/latest.md

The deterministic run needs no network and is safe for CI. Field-level
accuracy is reported per case so a prompt or guardrail change can be compared
against the previous run.
"""

import json
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from plusone.ai_services.eval_cases import MODERATION_CASES, PARSING_CASES
from plusone.ai_services.moderation import rule_moderate_text
from plusone.ai_services.parsing import _finalize_draft, rule_parse_activity
from plusone.models import CampusLocation


def _expected_date(spec, today):
    date_spec = spec.get("date")
    if not date_spec:
        return None
    if "days" in date_spec:
        return today + timedelta(days=date_spec["days"])
    candidate_year = today.year
    from datetime import date as date_cls

    candidate = date_cls(candidate_year, date_spec["month"], date_spec["day"])
    if candidate < today - timedelta(days=1):
        candidate = date_cls(candidate_year + 1, date_spec["month"], date_spec["day"])
    return candidate


def _parse_local(value):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return timezone.localtime(parsed)


def evaluate_parsing(parse_fn):
    """Score ``parse_fn`` (text -> draft dict) against PARSING_CASES."""
    today = timezone.localtime().date()
    results = []
    field_totals = {}
    field_correct = {}

    def score(case, field, ok, detail=""):
        field_totals[field] = field_totals.get(field, 0) + 1
        field_correct[field] = field_correct.get(field, 0) + bool(ok)
        if not ok:
            results[-1]["failures"].append({"field": field, "detail": detail})

    latencies = []
    for case in PARSING_CASES:
        started = time.perf_counter()
        draft = parse_fn(case["text"])
        latencies.append(time.perf_counter() - started)
        validation = draft.get("validation") or {}
        results.append({"id": case["id"], "tags": case.get("tags", []), "failures": [],
                        "latency_ms": round(latencies[-1] * 1000)})
        expected = case["expected"]

        if "activity_type" in expected:
            got = draft.get("activity_type")
            score(case, "activity_type", got == expected["activity_type"], f"got {got!r}")

        if "location_name" in expected:
            got = draft.get("location_name")
            score(case, "location", got == expected["location_name"], f"got {got!r}")

        if "start" in expected:
            start = _parse_local(draft.get("start_time"))
            if expected["start"] is None:
                score(case, "no_invented_time", start is None, f"got {draft.get('start_time')!r}")
            elif start is None:
                score(case, "date", False, "start_time empty")
                score(case, "time", False, "start_time empty")
            else:
                want_date = _expected_date(expected["start"], today)
                score(case, "date", start.date() == want_date, f"got {start.date()}, want {want_date}")
                score(case, "time", start.strftime("%H:%M") == expected["start"]["time"],
                      f"got {start.strftime('%H:%M')}, want {expected['start']['time']}")

        if expected.get("start_time_required"):
            score(case, "time_present", bool(draft.get("start_time")), "start_time empty")

        for code in expected.get("warnings", []):
            fired = any(w.get("code") == code for w in validation.get("warnings", []))
            score(case, f"warning_{code}", fired, "warning not fired")

        if "expire_between" in expected:
            low, high = expected["expire_between"]
            minutes = draft.get("expire_minutes")
            ok = isinstance(minutes, int) and low <= minutes <= high
            score(case, "expire_in_bounds", ok, f"got {minutes!r}")

    summary = {
        field: {"correct": field_correct[field], "total": field_totals[field]}
        for field in sorted(field_totals)
    }
    failed_cases = [r for r in results if r["failures"]]
    ordered = sorted(latencies)
    latency = {
        "avg_ms": round(1000 * sum(ordered) / len(ordered)),
        "p50_ms": round(1000 * ordered[len(ordered) // 2]),
        "p95_ms": round(1000 * ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]),
        "max_ms": round(1000 * ordered[-1]),
    } if ordered else None
    return {"cases": results, "fields": summary, "failed_case_count": len(failed_cases),
            "case_count": len(results), "latency": latency}


def evaluate_moderation(moderate_fn):
    tp = fp = tn = fn = 0
    failures = []
    for text, should_flag in MODERATION_CASES:
        flagged = bool(moderate_fn(text).get("flagged"))
        if flagged and should_flag:
            tp += 1
        elif flagged and not should_flag:
            fp += 1
            failures.append({"text": text, "error": "false_positive"})
        elif not flagged and should_flag:
            fn += 1
            failures.append({"text": text, "error": "false_negative"})
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": precision, "recall": recall, "failures": failures}


def render_markdown(parsing, moderation, strategy):
    now = timezone.localtime()
    lines = [
        "# AI Evaluation Run",
        "",
        f"- Date: {now.strftime('%Y-%m-%d %H:%M %Z')}",
        f"- Pipeline: {strategy}",
        f"- Parsing cases: {parsing['case_count']} ({parsing['failed_case_count']} with failures)",
    ]
    lat = parsing.get("latency")
    if lat:
        lines.append(
            f"- Parsing latency: avg {lat['avg_ms']}ms, p50 {lat['p50_ms']}ms, "
            f"p95 {lat['p95_ms']}ms, max {lat['max_ms']}ms")
    lines += [
        "",
        "## Parsing field accuracy",
        "",
        "| Field | Correct | Total | Accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for field, stats in parsing["fields"].items():
        pct = 100 * stats["correct"] / stats["total"]
        lines.append(f"| {field} | {stats['correct']} | {stats['total']} | {pct:.0f}% |")
    lines += ["", "### Failing cases", ""]
    failing = [c for c in parsing["cases"] if c["failures"]]
    if not failing:
        lines.append("None.")
    for case in failing:
        details = "; ".join(f"{f['field']} ({f['detail']})" for f in case["failures"])
        lines.append(f"- `{case['id']}`: {details}")
    m = moderation
    lines += [
        "",
        "## Moderation confusion matrix",
        "",
        "| | Flagged | Allowed |",
        "| --- | --- | --- |",
        f"| Risky | TP {m['tp']} | FN {m['fn']} |",
        f"| Benign | FP {m['fp']} | TN {m['tn']} |",
        "",
        f"- Precision: {m['precision']:.2f}" if m["precision"] is not None else "- Precision: n/a",
        f"- Recall: {m['recall']:.2f}" if m["recall"] is not None else "- Recall: n/a",
        "",
    ]
    if m["failures"]:
        lines.append("### Moderation failures")
        lines.append("")
        for failure in m["failures"]:
            lines.append(f"- {failure['error']}: \"{failure['text']}\"")
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = "Benchmark the drafting pipeline (parser + guardrails) and safety moderation."

    def add_arguments(self, parser):
        parser.add_argument("--use-llm", action="store_true",
                            help="Evaluate the full LLM pipeline instead of the deterministic fallback.")
        parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
        parser.add_argument("--report", help="Also write a markdown report to this path.")

    def handle(self, *args, **options):
        if not CampusLocation.objects.exists():
            self.stdout.write(self.style.WARNING(
                "No campus locations found. Run python manage.py seed_demo first."))
            return

        if options["use_llm"]:
            from django.contrib.auth import get_user_model

            from plusone.ai import parse_activity_text

            user = get_user_model().objects.filter(is_superuser=False).first() or \
                get_user_model().objects.first()
            strategy = "llm_with_guardrails"

            def parse_fn(text):
                return parse_activity_text(user, text)
        else:
            strategy = "deterministic_fallback_with_guardrails"

            def parse_fn(text):
                return _finalize_draft(text, rule_parse_activity(text))

        parsing = evaluate_parsing(parse_fn)
        moderation = evaluate_moderation(rule_moderate_text)

        if options["json"]:
            self.stdout.write(json.dumps(
                {"strategy": strategy, "parsing": parsing, "moderation": moderation},
                indent=2, default=str))
        else:
            self.stdout.write(f"Plus One AI evaluation ({strategy})")
            self.stdout.write("-" * 48)
            for field, stats in parsing["fields"].items():
                pct = 100 * stats["correct"] / stats["total"]
                self.stdout.write(f"{field:24s} {stats['correct']}/{stats['total']} ({pct:.0f}%)")
            self.stdout.write(
                f"cases with failures: {parsing['failed_case_count']}/{parsing['case_count']}")
            lat = parsing.get("latency")
            if lat:
                self.stdout.write(
                    f"latency: avg {lat['avg_ms']}ms | p50 {lat['p50_ms']}ms | "
                    f"p95 {lat['p95_ms']}ms | max {lat['max_ms']}ms")
            for case in parsing["cases"]:
                for failure in case["failures"]:
                    self.stdout.write(self.style.WARNING(
                        f"  FAIL {case['id']}: {failure['field']} - {failure['detail']}"))
            m = moderation
            self.stdout.write("-" * 48)
            self.stdout.write(
                f"moderation: TP {m['tp']} FP {m['fp']} TN {m['tn']} FN {m['fn']} | "
                f"precision {m['precision']:.2f} recall {m['recall']:.2f}")
            for failure in m["failures"]:
                self.stdout.write(self.style.WARNING(
                    f"  FAIL {failure['error']}: {failure['text']}"))

        if options["report"]:
            report = render_markdown(parsing, moderation, strategy)
            with open(options["report"], "w", encoding="utf-8") as handle:
                handle.write(report)
            self.stdout.write(self.style.SUCCESS(f"Report written to {options['report']}"))
