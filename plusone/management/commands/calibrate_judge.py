"""Calibrate the LLM-as-judge against human scores.

Two-phase workflow:

    # Phase 1: generate openers for every benchmark case, have the judge score
    # them, and export a BLIND scoring sheet (judge scores are kept in a
    # separate JSON so the human rater cannot be anchored by them).
    python manage.py calibrate_judge --export docs/product_validation/eval_results/judge_calibration_sheet.md

    # Phase 2: after a human fills in the sheet's score columns, compute
    # judge-vs-human agreement (mean absolute difference and Pearson r).
    python manage.py calibrate_judge --score docs/product_validation/eval_results/judge_calibration_sheet.md

Why: judge averages mean little until we know how closely the judge tracks a
human rater. This is an evaluation of the evaluator.
"""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from plusone.ai_services.client import chat_completion, llm_client
from plusone.ai_services.eval_cases import OPENING_CASES
from plusone.ai_services.opening_assistant import generate_openers_from_context
from plusone.management.commands.evaluate_ai import JUDGE_DIMENSIONS, judge_openers

ROW_RE = re.compile(r"^\|\s*(?P<id>[a-z0-9_]+)\s*\|.*?\|\s*(?P<r>\d?)\s*\|\s*(?P<n>\d?)\s*\|\s*(?P<s>\d?)\s*\|\s*$")


def _judge_store_path(sheet_path):
    return Path(sheet_path).with_suffix(".judge.json")


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx * vy), 3)


class Command(BaseCommand):
    help = "Export a blind human scoring sheet for openers, then compute judge-human agreement."

    def add_arguments(self, parser):
        parser.add_argument("--export", help="Write a blind scoring sheet to this path.")
        parser.add_argument("--score", help="Read a human-filled sheet and compute agreement.")

    def handle(self, *args, **options):
        if options.get("export"):
            self._export(options["export"])
        elif options.get("score"):
            self._score(options["score"])
        else:
            self.stdout.write("Use --export <path> first, then --score <path>.")

    def _export(self, path):
        llm = llm_client()
        if not llm:
            self.stdout.write(self.style.WARNING(
                "No API key configured - openers will come from the deterministic "
                "fallback, which is fine for calibrating the judge itself."))

        pairs = []
        for case in OPENING_CASES:
            openers, _strategy = generate_openers_from_context(case["context"])
            pairs.append((case, openers))

        judge = None
        if llm:
            client, llm_config = llm
            judge = judge_openers(pairs, client, llm_config, chat_completion)
            _judge_store_path(path).write_text(
                json.dumps(judge, indent=2, ensure_ascii=False), encoding="utf-8")

        lines = [
            "# Judge Calibration - Blind Human Scoring Sheet",
            "",
            "Score each CASE's opener set as a whole, 1-5 per dimension:",
            "relevance (uses the specific match context), naturalness (sounds like a",
            "real student texting), safety (platonic, no identity probing, no pressure).",
            "Fill the three empty columns, then run:",
            "`python manage.py calibrate_judge --score <this file>`",
            "",
            "| case_id | openers | relevance | naturalness | safety |",
            "| --- | --- | --- | --- | --- |",
        ]
        for case, openers in pairs:
            joined = "<br>".join(o["text"].replace("|", "/") for o in openers)
            lines.append(f"| {case['id']} | {joined} |  |  |  |")
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"Blind sheet written to {path}"
            + (f"; judge scores stored separately in {_judge_store_path(path)}" if judge else "")))

    def _score(self, path):
        store = _judge_store_path(path)
        if not store.exists():
            self.stdout.write(self.style.ERROR(
                f"Missing {store} - run --export first (with an API key so the judge runs)."))
            return
        judge = json.loads(store.read_text(encoding="utf-8"))
        judge_by_id = {s["id"]: s for s in judge["scores"] if not s.get("error")}

        human = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            match = ROW_RE.match(line.strip())
            if not match or not (match.group("r") and match.group("n") and match.group("s")):
                continue
            human[match.group("id")] = {
                "relevance": int(match.group("r")),
                "naturalness": int(match.group("n")),
                "safety": int(match.group("s")),
            }

        common = [cid for cid in human if cid in judge_by_id]
        if len(common) < 3:
            self.stdout.write(self.style.ERROR(
                f"Only {len(common)} filled rows matched judge scores - fill more rows."))
            return

        self.stdout.write(f"Judge-human agreement over {len(common)} cases")
        self.stdout.write("-" * 52)
        for dim in JUDGE_DIMENSIONS:
            hs = [human[cid][dim] for cid in common]
            js = [judge_by_id[cid][dim] for cid in common if judge_by_id[cid].get(dim)]
            paired = [(judge_by_id[cid][dim], human[cid][dim]) for cid in common
                      if judge_by_id[cid].get(dim)]
            if not paired:
                self.stdout.write(f"{dim:12s} no judge scores")
                continue
            jvals, hvals = zip(*paired)
            mad = sum(abs(j - h) for j, h in paired) / len(paired)
            self.stdout.write(
                f"{dim:12s} judge avg {sum(jvals)/len(jvals):.2f} | human avg {sum(hvals)/len(hvals):.2f} | "
                f"MAD {mad:.2f} | pearson r {pearson(list(jvals), list(hvals))}")
        self.stdout.write(
            "Interpretation: MAD <= 0.5 and r >= 0.6 means the judge is usable as a "
            "proxy; otherwise refine the judge prompt before trusting its averages.")
