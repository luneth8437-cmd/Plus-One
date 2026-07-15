"""Print the core product funnel from server-side ProductEvent rows.

    python manage.py funnel_report
    python manage.py funnel_report --days 7
    python manage.py funnel_report --json

Funnel (see docs/product_validation/04_analytics_event_plan.md):

    publish_card -> match_created -> first_message_sent
    -> first_reply_received -> agree_clicked -> both agreed

Opener assistant adoption is reported alongside:
    opener_suggested sessions, and the share of first messages that used a
    suggestion verbatim or edited.
"""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from plusone.models import Match, ProductEvent


def _count(name, since):
    qs = ProductEvent.objects.filter(name=name)
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs.count()


def _distinct_matches(name, since):
    qs = ProductEvent.objects.filter(name=name, match__isnull=False)
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs.values("match").distinct().count()


def build_report(days=None):
    since = timezone.now() - timedelta(days=days) if days else None

    published = _count(ProductEvent.Name.PUBLISH_CARD, since)
    matches = _count(ProductEvent.Name.MATCH_CREATED, since)
    first_messages = _distinct_matches(ProductEvent.Name.FIRST_MESSAGE_SENT, since)
    first_replies = _distinct_matches(ProductEvent.Name.FIRST_REPLY_RECEIVED, since)
    agree_matches = _distinct_matches(ProductEvent.Name.AGREE_CLICKED, since)

    agreed_qs = Match.objects.filter(status=Match.Status.AGREED)
    if since:
        agreed_qs = agreed_qs.filter(created_at__gte=since)
    both_agreed = agreed_qs.count()

    opener_sessions = _count(ProductEvent.Name.OPENER_SUGGESTED, since)
    opener_clicks = _count(ProductEvent.Name.OPENER_CLICKED, since)
    first_msg_qs = ProductEvent.objects.filter(name=ProductEvent.Name.FIRST_MESSAGE_SENT)
    if since:
        first_msg_qs = first_msg_qs.filter(created_at__gte=since)
    first_msg_events = list(first_msg_qs.values_list("properties", flat=True))
    used = sum(1 for p in first_msg_events if p.get("opener_usage") in ("verbatim", "edited"))
    verbatim = sum(1 for p in first_msg_events if p.get("opener_usage") == "verbatim")

    def rate(numerator, denominator):
        return round(100 * numerator / denominator, 1) if denominator else None

    return {
        "window_days": days,
        "funnel": {
            "publish_card": published,
            "match_created": matches,
            "matches_with_first_message": first_messages,
            "matches_with_first_reply": first_replies,
            "matches_with_agree": agree_matches,
            "matches_both_agreed": both_agreed,
        },
        "conversion": {
            "publish_to_match_pct": rate(matches, published),
            "match_to_first_message_pct": rate(first_messages, matches),
            "first_message_to_reply_pct": rate(first_replies, first_messages),
            "match_to_both_agreed_pct": rate(both_agreed, matches),
        },
        "opening_assistant": {
            "suggestion_sessions": opener_sessions,
            "suggestion_clicks": opener_clicks,
            "click_through_pct": rate(opener_clicks, opener_sessions),
            "first_messages_total": len(first_msg_events),
            "first_messages_using_suggestion": used,
            "adoption_pct": rate(used, len(first_msg_events)),
            "verbatim_share_of_adopted_pct": rate(verbatim, used),
        },
    }


class Command(BaseCommand):
    help = "Print the core funnel and opening-assistant adoption from ProductEvent."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, help="Only include events from the last N days.")
        parser.add_argument("--json", action="store_true", help="Print JSON.")

    def handle(self, *args, **options):
        report = build_report(days=options.get("days"))
        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2))
            return

        window = f"last {report['window_days']} days" if report["window_days"] else "all time"
        self.stdout.write(f"Plus One funnel ({window})")
        self.stdout.write("-" * 44)
        for key, value in report["funnel"].items():
            self.stdout.write(f"{key:32s} {value}")
        self.stdout.write("-" * 44)
        for key, value in report["conversion"].items():
            display = f"{value}%" if value is not None else "n/a"
            self.stdout.write(f"{key:32s} {display}")
        self.stdout.write("-" * 44)
        for key, value in report["opening_assistant"].items():
            display = f"{value}%" if key.endswith("_pct") and value is not None else value
            self.stdout.write(f"{key:32s} {display}")
