import argparse

from django.core.management.base import BaseCommand

from plusone.services.cleanup import (
    DEFAULT_BATCH_SIZE,
    MAX_REPORT_RETENTION_DAYS,
    cleanup_stale_records,
)


def positive_days(value):
    days = int(value)
    if days < 1:
        raise argparse.ArgumentTypeError("retention days must be at least 1")
    return days


def report_days(value):
    days = positive_days(value)
    if days > MAX_REPORT_RETENTION_DAYS:
        raise argparse.ArgumentTypeError(
            f"safety report retention cannot exceed {MAX_REPORT_RETENTION_DAYS} days"
        )
    return days


class Command(BaseCommand):
    help = "Delete one bounded batch of expired and stale temporary product data."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=positive_days, default=7, help="Minimum stale age in days.")
        parser.add_argument("--llm-log-days", type=positive_days, default=30, help="AI log retention in days.")
        parser.add_argument("--event-days", type=positive_days, default=90, help="Product event retention in days.")
        parser.add_argument(
            "--report-days",
            type=report_days,
            default=MAX_REPORT_RETENTION_DAYS,
            help=f"Safety report retention in days (maximum {MAX_REPORT_RETENTION_DAYS}).",
        )
        parser.add_argument(
            "--batch-size",
            type=positive_days,
            default=DEFAULT_BATCH_SIZE,
            help="Maximum selected rows per class. Each selected identity also cascades its owned records.",
        )
        parser.add_argument("--commit", action="store_true", help="Actually delete matching records.")

    def handle(self, *args, **options):
        counts = cleanup_stale_records(
            user_days=options["days"],
            llm_log_days=options["llm_log_days"],
            event_days=options["event_days"],
            report_days=options["report_days"],
            batch_size=options["batch_size"],
            dry_run=not options["commit"],
        )
        summary = (
            f"{counts['users']} anonymous user(s), {counts['llm_logs']} AI log(s), "
            f"{counts['events']} product event(s), {counts['safety_reports']} safety report(s), "
            f"{counts['activity_reports']} activity report(s), "
            f"{counts['report_messages']} expired chat-evidence message(s), "
            f"{counts['sessions']} expired session(s), and "
            f"{counts['rate_limit_buckets']} expired rate-limit bucket(s), and "
            f"{counts['browser_budget_buckets']} expired browser-budget bucket(s), and "
            f"{counts['presence_leases']} expired terminal presence lease(s), "
            f"{counts['push_subscriptions']} inactive push subscription(s), and "
            f"{counts['push_deliveries']} old push delivery record(s)"
        )
        if options["commit"]:
            self.stdout.write(self.style.SUCCESS(f"Deleted {summary}."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {summary} would be deleted. Use --commit to apply."
                )
            )
        self.stdout.write("Identity counts include a whole ownership graph; cascaded child rows are not included in the independent row counts above.")
