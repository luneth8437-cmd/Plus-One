import argparse
import json

from django.core.management.base import BaseCommand, CommandError

from plusone.services.push_notifications import MAX_BATCH_SIZE, run_push_updates


def bounded_batch(value):
    try:
        size = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("batch size must be a whole number") from exc
    if not 1 <= size <= MAX_BATCH_SIZE:
        raise argparse.ArgumentTypeError(f"batch size must be between 1 and {MAX_BATCH_SIZE}")
    return size


class Command(BaseCommand):
    help = "Preview opt-in Web Push updates; --send explicitly delivers one bounded batch."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true", help="Contact configured browser push providers and record delivery attempts.")
        parser.add_argument("--batch-size", type=bounded_batch, default=100)

    def handle(self, *args, **options):
        report = run_push_updates(send=options["send"], batch_size=options["batch_size"])
        if options["send"] and not report["available"]:
            raise CommandError("Web Push is unavailable. Configure the feature flag and valid matching VAPID keys/contact first.")
        self.stdout.write(json.dumps(report, indent=2))
