import argparse

from django.core.management.base import BaseCommand

from plusone.services.cleanup import cleanup_stale_records


def positive_days(value):
    days = int(value)
    if days < 1:
        raise argparse.ArgumentTypeError("retention days must be at least 1")
    return days


class Command(BaseCommand):
    help = "Delete stale anonymous identities, AI logs, and product events using separate retention windows."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=positive_days, default=7, help="Minimum stale age in days.")
        parser.add_argument("--llm-log-days", type=positive_days, default=30, help="AI log retention in days.")
        parser.add_argument("--event-days", type=positive_days, default=90, help="Product event retention in days.")
        parser.add_argument("--commit", action="store_true", help="Actually delete matching users.")

    def handle(self, *args, **options):
        counts = cleanup_stale_records(
            user_days=options["days"],
            llm_log_days=options["llm_log_days"],
            event_days=options["event_days"],
            dry_run=not options["commit"],
        )
        summary = (
            f"{counts['users']} anonymous user(s), {counts['llm_logs']} AI log(s), "
            f"and {counts['events']} product event(s)"
        )
        if options["commit"]:
            self.stdout.write(self.style.SUCCESS(f"Deleted {summary}."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {summary} would be deleted. Use --commit to apply."
                )
            )
