"""Print the core product funnel from server-side ProductEvent rows.

    python manage.py funnel_report
    python manage.py funnel_report --days 7
    python manage.py funnel_report --json

Funnel (see docs/product_validation/04_analytics_event_plan.md):

    publish_card -> match_created -> first_message_sent
    -> first_reply_received -> agree_clicked -> both agreed
    -> meetup_confirmed

Opener assistant adoption is reported alongside:
    opener_suggested sessions, and the share of first messages that used a
    suggestion verbatim or edited.
"""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from plusone.models import Match, ProductEvent


def _count(name, since):
    qs = ProductEvent.objects.filter(name=name)
    if since:
        qs = qs.filter(post_created_at__gte=since)
    return qs.count()


def _distinct_matches(name, since):
    qs = ProductEvent.objects.filter(name=name, match_reference__isnull=False)
    if since:
        qs = qs.filter(post_created_at__gte=since)
    return qs.order_by().values("match_reference").distinct().count()


def _rate(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else None


def _journey_report(since):
    events = ProductEvent.objects.all()
    if since:
        events = events.filter(created_at__gte=since)
    visits = events.filter(name=ProductEvent.Name.DISCOVER_VISITED).count()
    empty = events.filter(name=ProductEvent.Name.DISCOVER_EMPTY).count()
    outcomes = {}
    reasons = {}
    for props in events.filter(name=ProductEvent.Name.INTERESTED_RESULT).values_list("properties", flat=True):
        value = props.get("outcome", "unknown")
        outcomes[value] = outcomes.get(value, 0) + 1
    for props in events.filter(name=ProductEvent.Name.WAIT_CLOSED).values_list("properties", flat=True):
        value = props.get("reason", "unknown")
        reasons[value] = reasons.get(value, 0) + 1
    return {
        "window_basis": "event time; card impressions mean server-rendered, not observed viewport visibility",
        "discover_visits": visits, "empty_visits": empty,
        "empty_visit_pct": _rate(empty, visits),
        "create_started": events.filter(name=ProductEvent.Name.CREATE_STARTED).count(),
        "rendered_card_impressions": events.filter(name=ProductEvent.Name.CARD_IMPRESSION, properties__own_card=False).count(),
        "interested_results": outcomes, "waiting_closed_reasons": reasons,
    }


def _opening_report(since, first_messages):
    events = ProductEvent.objects.filter(name__in=[ProductEvent.Name.OPENER_SUGGESTED, ProductEvent.Name.OPENER_CLICKED])
    if since:
        events = events.filter(post_created_at__gte=since)
    rows = list(events.values("name", "properties"))
    batches = {row["properties"]["batch_id"]: row["properties"] for row in rows
               if row["name"] == ProductEvent.Name.OPENER_SUGGESTED and row["properties"].get("batch_id")}
    selected = {row["properties"].get("batch_id") for row in rows
                if row["name"] == ProductEvent.Name.OPENER_CLICKED and row["properties"].get("batch_id") in batches}
    grouped = {}
    for batch_id, props in batches.items():
        key = (props.get("prompt_version", "unknown"), props.get("model", "unknown"), props.get("generation", "unknown"))
        group = grouped.setdefault(key, {"prompt_version": key[0], "model": key[1], "generation": key[2],
                                        "suggestion_batches": 0, "selected_batches": 0, "first_messages_using_suggestion": 0})
        group["suggestion_batches"] += 1
        group["selected_batches"] += batch_id in selected
    for props in first_messages:
        if props.get("opener_usage") not in {"verbatim", "edited"}:
            continue
        batch = batches.get(props.get("batch_id"))
        if batch:
            key = (batch.get("prompt_version", "unknown"), batch.get("model", "unknown"), batch.get("generation", "unknown"))
            grouped[key]["first_messages_using_suggestion"] += 1
    for group in grouped.values():
        group["selected_batch_pct"] = _rate(group["selected_batches"], group["suggestion_batches"])
    used = sum(p.get("opener_usage") in {"verbatim", "edited"} for p in first_messages)
    verbatim = sum(p.get("opener_usage") == "verbatim" for p in first_messages)
    return {
        "suggestion_sessions": sum(row["name"] == ProductEvent.Name.OPENER_SUGGESTED for row in rows),
        "suggestion_clicks": sum(row["name"] == ProductEvent.Name.OPENER_CLICKED for row in rows),
        "suggestion_batches": len(batches), "selected_batches": len(selected),
        "click_through_pct": _rate(len(selected), len(batches)),
        "click_through_basis": "unique known batches selected at least once; repeated clicks are not unique users",
        "legacy_suggestion_batches_unknown": sum(row["name"] == ProductEvent.Name.OPENER_SUGGESTED and not row["properties"].get("batch_id") for row in rows),
        "unattributed_clicks": sum(row["name"] == ProductEvent.Name.OPENER_CLICKED and row["properties"].get("batch_id") not in batches for row in rows),
        "first_messages_total": len(first_messages), "first_messages_using_suggestion": used,
        "adoption_pct": _rate(used, len(first_messages)), "verbatim_share_of_adopted_pct": _rate(verbatim, used),
        "by_version": list(grouped.values()),
    }


def _feedback_report(since, now):
    """Use retained evidence; an absent actor or timing stays unknown."""
    from plusone.services.meetups import _effective_times
    matches = Match.objects.filter(status=Match.Status.AGREED).select_related("post")
    if since:
        matches = matches.filter(post__created_at__gte=since)
    groups = {}
    for match in matches:
        groups[match.pk] = {
            "end": _effective_times(match)[1], "cancelled": bool(match.meetup_cancelled_at),
            "outcomes": {"publisher": match.poster_meetup_outcome, "guest": match.swiper_meetup_outcome},
            "actors": {match.poster_id: "publisher", match.swiper_id: "guest"},
        }
    events = ProductEvent.objects.filter(name__in=[ProductEvent.Name.BOTH_AGREED, ProductEvent.Name.MEETUP_OUTCOME,
                                                   ProductEvent.Name.MEETUP_CONFIRMED, ProductEvent.Name.MEETUP_CANCELLED],
                                        match_reference__isnull=False).order_by("created_at", "pk")
    if since:
        events = events.filter(post_created_at__gte=since)
    for event in events:
        group = groups.setdefault(event.match_reference, {"end": None, "cancelled": False, "outcomes": {}, "actors": {}})
        props = event.properties
        raw_end = props.get("meeting_end_at")
        try:
            end = parse_datetime(raw_end) if isinstance(raw_end, str) else None
        except (TypeError, ValueError):
            end = None
        if end and timezone.is_aware(end):
            group["end"] = end
        if event.name == ProductEvent.Name.MEETUP_CANCELLED:
            group["cancelled"] = True
        role = props.get("actor_role") or group["actors"].get(event.user_id)
        if role in {"publisher", "guest"}:
            if event.name == ProductEvent.Name.MEETUP_OUTCOME and props.get("outcome") in {"met", "not_met"}:
                group["outcomes"][role] = props["outcome"]
            elif event.name == ProductEvent.Name.MEETUP_CONFIRMED and not group["outcomes"].get(role):
                group["outcomes"][role] = "met"
    report = dict.fromkeys(["mature_agreements", "future_or_feedback_open", "maturity_unknown", "both_met",
                           "both_not_met", "conflicting_feedback", "one_sided_feedback", "no_feedback", "cancelled"], 0)
    for group in groups.values():
        if not group["end"]:
            report["maturity_unknown"] += 1
            continue
        if group["end"] + timedelta(hours=24) >= now:
            report["future_or_feedback_open"] += 1
            continue
        report["mature_agreements"] += 1
        outcomes = [group["outcomes"].get(role) for role in ("publisher", "guest")]
        if group["cancelled"]:
            key = "cancelled"
        elif outcomes == ["met", "met"]:
            key = "both_met"
        elif outcomes == ["not_met", "not_met"]:
            key = "both_not_met"
        elif all(outcomes):
            key = "conflicting_feedback"
        elif any(outcomes):
            key = "one_sided_feedback"
        else:
            key = "no_feedback"
        report[key] += 1
    report["both_met_pct"] = _rate(report["both_met"], report["mature_agreements"])
    report["basis"] = "activity creation cohort; agreed window ended more than 24h ago; independent self-reports, not verified attendance"
    return report


def build_report(days=None):
    if days is not None and days < 1:
        raise ValueError("days must be at least 1")
    now = timezone.now()
    since = now - timedelta(days=days) if days else None

    cohort = ProductEvent.objects.filter(post_reference__isnull=False)
    if since:
        cohort = cohort.filter(post_created_at__gte=since)
    published = cohort.filter(name=ProductEvent.Name.PUBLISH_CARD).order_by().values("post_reference").distinct().count()
    matched_cards = cohort.filter(name=ProductEvent.Name.MATCH_CREATED).order_by().values("post_reference").distinct().count()
    matches = _distinct_matches(ProductEvent.Name.MATCH_CREATED, since)
    first_messages = _distinct_matches(ProductEvent.Name.FIRST_MESSAGE_SENT, since)
    first_replies = _distinct_matches(ProductEvent.Name.FIRST_REPLY_RECEIVED, since)
    agree_matches = _distinct_matches(ProductEvent.Name.AGREE_CLICKED, since)

    both_agreed = _distinct_matches(ProductEvent.Name.BOTH_AGREED, since)
    meetups = _distinct_matches(ProductEvent.Name.MEETUP_CONFIRMED, since)

    first_msg_qs = ProductEvent.objects.filter(name=ProductEvent.Name.FIRST_MESSAGE_SENT)
    if since:
        first_msg_qs = first_msg_qs.filter(post_created_at__gte=since)
    first_msg_events = list(first_msg_qs.values_list("properties", flat=True))
    rate = _rate

    return {
        "window_days": days,
        "window_basis": "activity creation cohort; subsequent events within retained history",
        "legacy_meetup_metric_basis": "meetup_confirmed means at least one participant self-reported met; compatibility counter, not bilateral success",
        "unattributed_events": ProductEvent.objects.filter(post_reference__isnull=True).exclude(
            name__in=[ProductEvent.Name.DISCOVER_VISITED, ProductEvent.Name.DISCOVER_EMPTY,
                      ProductEvent.Name.CREATE_STARTED, ProductEvent.Name.MAINTENANCE_COMPLETED]).count(),
        "legacy_agreements_without_agreement_event": Match.objects.filter(status=Match.Status.AGREED).exclude(pk__in=ProductEvent.objects.filter(name=ProductEvent.Name.BOTH_AGREED, match_reference__isnull=False).values("match_reference")).count(),
        "funnel": {
            "publish_card": published,
            "matched_cards": matched_cards,
            "match_created": matches,
            "chat_started": _distinct_matches(ProductEvent.Name.CHAT_STARTED, since),
            "matches_with_first_message": first_messages,
            "matches_with_first_reply": first_replies,
            "matches_with_agree": agree_matches,
            "matches_both_agreed": both_agreed,
            "matches_with_meetup_confirmed": meetups,
        },
        "conversion": {
            "publish_to_match_pct": rate(matched_cards, published),
            "match_to_first_message_pct": rate(first_messages, matches),
            "first_message_to_reply_pct": rate(first_replies, first_messages),
            "match_to_both_agreed_pct": rate(both_agreed, matches),
            "agreed_to_meetup_confirmed_pct": rate(meetups, both_agreed),
        },
        "journey": _journey_report(since),
        "meetup_feedback": _feedback_report(since, now),
        "opening_assistant": _opening_report(since, first_msg_events),
    }


class Command(BaseCommand):
    help = "Print the core funnel and opening-assistant adoption from ProductEvent."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, help="Only include events from the last N days.")
        parser.add_argument("--json", action="store_true", help="Print JSON.")

    def handle(self, *args, **options):
        try:
            report = build_report(days=options.get("days"))
        except ValueError as error:
            raise CommandError(str(error)) from error
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
        for section in ("journey", "meetup_feedback"):
            self.stdout.write(f"\n{section}")
            self.stdout.write(json.dumps(report[section], indent=2))
