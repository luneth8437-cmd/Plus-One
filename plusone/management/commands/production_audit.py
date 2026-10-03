import json
import re

from django.core.management.base import BaseCommand, CommandError

from plusone.services.operations import production_audit


class Command(BaseCommand):
    help = "Read local deployment facts and maintenance backlog without secrets or remote requests."

    def add_arguments(self, parser):
        parser.add_argument("--expected-commit", help="Expected full 40-character Git SHA.")
        parser.add_argument("--strict", action="store_true", help="Exit nonzero on failed local configuration checks.")

    def handle(self, *args, **options):
        expected = options.get("expected_commit")
        if expected and not re.fullmatch(r"[0-9a-fA-F]{40}", expected):
            raise CommandError("--expected-commit must be a full 40-character Git SHA.")
        report = production_audit(expected)
        self.stdout.write(json.dumps(report, indent=2))
        if options["strict"] and report["issues"]:
            raise CommandError("Local checks failed: " + ", ".join(report["issues"]))
