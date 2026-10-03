import argparse
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from plusone.models import ProductEvent
from plusone.services.cleanup import DEFAULT_BATCH_SIZE, cleanup_stale_records
from plusone.services.expiration import refresh_expired_records
from plusone.services.operations import operational_backlog


def positive_integer(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return value


class Command(BaseCommand):
    help = "Preview maintenance, or expire lifecycle state then clean one bounded batch with --commit."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Apply expiry and bounded cleanup; record successful completion.")
        parser.add_argument("--batch-size", type=positive_integer, default=DEFAULT_BATCH_SIZE)

    def handle(self, *args, **options):
        started_at = timezone.now()
        before = operational_backlog(started_at)
        commit = options["commit"]
        expired = refresh_expired_records() if commit else {
            "posts": before["expired_active_posts"], "matches": min(before["expired_live_matches"], 200),
        }
        if expired.get("deferred"):
            raise CommandError("Lifecycle expiry was deferred because the database was busy; cleanup was not run.")
        counts = cleanup_stale_records(dry_run=not commit, batch_size=options["batch_size"])
        after = operational_backlog()
        report = {
            "mode": "commit" if commit else "dry_run", "batch_size": options["batch_size"],
            "expired_counts": expired, "cleanup_counts": counts,
            "backlog_before": before, "backlog_after": after,
            "note": "Each selected identity also cascades its owned records; batch size is not a total row cap.",
        }
        if not commit:
            report["preview_basis"] = "No lifecycle state changed. Cleanup previews current eligibility; applying expiry first may make more identities eligible."
        else:
            # Operational evidence is separate from the product funnel. A
            # failed write raises instead of claiming recorded success.
            ProductEvent.objects.create(name=ProductEvent.Name.MAINTENANCE_COMPLETED, properties={
                "started_at": started_at.isoformat(), "completed_at": timezone.now().isoformat(),
                "batch_size": options["batch_size"], "expired_counts": expired,
                "cleanup_counts": counts, "backlog_after": after,
            })
            report["successful_run_recorded"] = True
        self.stdout.write(json.dumps(report, indent=2))
