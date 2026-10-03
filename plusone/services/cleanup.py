from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.utils import timezone

from plusone.models import (
    ActivityPost,
    ActivityReport,
    BrowserBudgetBucket,
    ChatMessage,
    LLMLog,
    Match,
    PresenceLease,
    PushDelivery,
    PushSubscription,
    ProductEvent,
    RateLimitBucket,
    SafetyReport,
    UserProfile,
)
from plusone.services.lifecycle import locked_match


DEFAULT_BATCH_SIZE = 500
MAX_REPORT_RETENTION_DAYS = 90


def _validate_positive(value, name):
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _report_retention_days(value):
    _validate_positive(value, "report_days")
    if value > MAX_REPORT_RETENTION_DAYS:
        raise ValueError(f"report_days cannot exceed {MAX_REPORT_RETENTION_DAYS}")
    return value


def _agreement_protected_q(cutoff, prefix=""):
    """Protect AGREED matches until 24 hours after the expected meetup end.

    Unknown historical end times use the same runtime one-hour window as
    meetup feedback; the historical fields themselves remain unknown.
    """
    status = f"{prefix}status"
    expected_end = f"{prefix}post__expected_end_time"
    start = f"{prefix}post__start_time"
    plan_end = f"{prefix}plan_expected_end_at"
    plan_start = f"{prefix}plan_meeting_at"
    return Q(**{status: Match.Status.AGREED}) & (
        Q(**{f"{plan_end}__gte": cutoff})
        | (Q(**{f"{plan_end}__isnull": True}) & (
            Q(**{f"{expected_end}__gte": cutoff})
            | (Q(**{f"{expected_end}__isnull": True}) & (
                Q(**{f"{plan_start}__gte": cutoff - timedelta(hours=1)})
                | Q(**{f"{plan_start}__isnull": True, f"{start}__gte": cutoff - timedelta(hours=1)})
            ))
        ))
    )


def _agreement_outside_protection_q(cutoff, prefix=""):
    status = f"{prefix}status"
    expected_end = f"{prefix}post__expected_end_time"
    start = f"{prefix}post__start_time"
    plan_end = f"{prefix}plan_expected_end_at"
    plan_start = f"{prefix}plan_meeting_at"
    return Q(**{status: Match.Status.AGREED}) & (
        Q(**{f"{plan_end}__lt": cutoff})
        | (Q(**{f"{plan_end}__isnull": True}) & (
            Q(**{f"{expected_end}__lt": cutoff})
            | (Q(**{f"{expected_end}__isnull": True}) & (
                Q(**{f"{plan_start}__lt": cutoff - timedelta(hours=1)})
                | Q(**{f"{plan_start}__isnull": True, f"{start}__lt": cutoff - timedelta(hours=1)})
            ))
        ))
    )


def _stale_identity_filter(cutoff):
    fallback_is_stale = Q(last_login__lt=cutoff) | Q(
        last_login__isnull=True,
        date_joined__lt=cutoff,
    )
    return Q(userprofile__last_seen_at__lt=cutoff) | (
        Q(userprofile__last_seen_at__isnull=True) & fallback_is_stale
    )


def _with_protection_annotations(queryset, now, report_cutoff):
    participant = Q(poster_id=OuterRef("pk")) | Q(swiper_id=OuterRef("pk"))
    agreed_since = now - timedelta(hours=24)
    unresolved_statuses = [SafetyReport.Status.PENDING, SafetyReport.Status.IN_PROGRESS]
    return queryset.annotate(
        cleanup_has_active_post=Exists(
            ActivityPost.objects.filter(
                user_id=OuterRef("pk"),
                status__in=[ActivityPost.Status.ACTIVE, ActivityPost.Status.MATCHED],
                expire_time__gt=now,
            )
        ),
        cleanup_has_live_match=Exists(
            Match.objects.filter(participant, status__in=Match.LIVE_STATUSES)
        ),
        cleanup_has_current_agreement=Exists(
            Match.objects.filter(participant).filter(_agreement_protected_q(agreed_since))
        ),
        cleanup_has_unresolved_report=Exists(
            SafetyReport.objects.filter(
                Q(match__poster_id=OuterRef("pk")) | Q(match__swiper_id=OuterRef("pk")),
                status__in=unresolved_statuses,
                created_at__gte=report_cutoff,
            )
        ),
        cleanup_has_unresolved_activity_report=Exists(
            ActivityReport.objects.filter(
                Q(post__user_id=OuterRef("pk")) | Q(reporter_id=OuterRef("pk")),
                status__in=unresolved_statuses, created_at__gte=report_cutoff,
            )
        ),
    )


def stale_anonymous_users(days=7, *, report_days=MAX_REPORT_RETENTION_DAYS, now=None):
    """Return anonymous identities eligible for deletion at this instant.

    The queryset is useful for previews. Commit mode still locks and rechecks
    every user so a concurrent page view or domain write cannot be missed.
    """
    _validate_positive(days, "days")
    report_days = _report_retention_days(report_days)
    now = now or timezone.now()
    cutoff = now - timedelta(days=days)
    report_cutoff = now - timedelta(days=report_days)
    User = get_user_model()
    users = User.objects.filter(
        username__startswith="anon_",
    ).filter(_stale_identity_filter(cutoff))
    return _with_protection_annotations(users, now, report_cutoff).filter(
        cleanup_has_active_post=False,
        cleanup_has_live_match=False,
        cleanup_has_current_agreement=False,
        cleanup_has_unresolved_report=False,
        cleanup_has_unresolved_activity_report=False,
    )


def _is_stale_locked(user, cutoff):
    last_seen_at = (
        UserProfile.objects.filter(user_id=user.pk)
        .values_list("last_seen_at", flat=True)
        .first()
    )
    if last_seen_at is not None:
        return last_seen_at < cutoff
    if user.last_login is not None:
        return user.last_login < cutoff
    return user.date_joined < cutoff


def _is_protected_locked(user_id, now, report_cutoff):
    if ActivityPost.objects.filter(
        user_id=user_id,
        status__in=[ActivityPost.Status.ACTIVE, ActivityPost.Status.MATCHED],
        expire_time__gt=now,
    ).exists():
        return True

    participant = Q(poster_id=user_id) | Q(swiper_id=user_id)
    agreed_since = now - timedelta(hours=24)
    # Cleanup never advances lifecycle state. A WAITING/CHATTING row remains
    # protected even past its deadline until the lifecycle path marks it
    # terminal; this keeps dry runs mutation-free and commit mode conservative.
    if Match.objects.filter(participant).filter(
        Q(status__in=Match.LIVE_STATUSES)
        | _agreement_protected_q(agreed_since)
    ).exists():
        return True

    return SafetyReport.objects.filter(
        Q(match__poster_id=user_id) | Q(match__swiper_id=user_id),
        status__in=[SafetyReport.Status.PENDING, SafetyReport.Status.IN_PROGRESS],
        created_at__gte=report_cutoff,
    ).exists() or ActivityReport.objects.filter(
        Q(post__user_id=user_id) | Q(reporter_id=user_id),
        status__in=[ActivityReport.Status.PENDING, ActivityReport.Status.IN_PROGRESS],
        created_at__gte=report_cutoff,
    ).exists()


def _candidate_user_ids(days, report_days, now, batch_size):
    return list(
        stale_anonymous_users(days, report_days=report_days, now=now)
        .order_by("pk")
        .values_list("pk", flat=True)[:batch_size]
    )


def _cleanup_users(*, days, report_days, now, batch_size, dry_run):
    cutoff = now - timedelta(days=days)
    report_cutoff = now - timedelta(days=report_days)
    User = get_user_model()
    deleted_or_eligible = 0

    for user_id in _candidate_user_ids(days, report_days, now, batch_size):
        with transaction.atomic():
            user = User.objects.select_for_update().filter(
                pk=user_id,
                username__startswith="anon_",
            ).first()
            if user is None or not _is_stale_locked(user, cutoff):
                continue
            if _is_protected_locked(user.pk, now, report_cutoff):
                continue
            deleted_or_eligible += 1
            if not dry_run:
                user.delete()

    return deleted_or_eligible


def cleanup_anonymous_users(
    days=7,
    dry_run=True,
    *,
    report_days=MAX_REPORT_RETENTION_DAYS,
    batch_size=DEFAULT_BATCH_SIZE,
):
    _validate_positive(days, "days")
    report_days = _report_retention_days(report_days)
    _validate_positive(batch_size, "batch_size")
    return _cleanup_users(
        days=days,
        report_days=report_days,
        now=timezone.now(),
        batch_size=batch_size,
        dry_run=dry_run,
    )


def _bounded_ids(queryset, batch_size):
    return list(queryset.order_by("pk").values_list("pk", flat=True)[:batch_size])


def _delete_ids(model, ids):
    if ids:
        model.objects.filter(pk__in=ids).delete()


def _inactive_push_subscriptions(now):
    return PushSubscription.objects.filter(is_active=False).filter(
        Q(user__userprofile__retired_at__isnull=False)
        | Q(updated_at__lt=now - timedelta(days=30))
    )


def _cleanup_push_subscriptions(now, batch_size, dry_run):
    ids = _bounded_ids(_inactive_push_subscriptions(now), batch_size)
    if dry_run:
        return len(ids)
    with transaction.atomic():
        # Recheck after locking: a current user may have re-enabled reminders.
        eligible = list(_inactive_push_subscriptions(now).filter(pk__in=ids)
                        .select_for_update(of=("self",)).values_list("pk", flat=True))
        _delete_ids(PushSubscription, eligible)
    return len(eligible)


def _cleanup_push_deliveries(now, batch_size, dry_run):
    from plusone.services.push_notifications import _eligible_rows
    candidates = list(PushDelivery.objects.filter(created_at__lt=now - timedelta(days=90))
                      .select_related("subscription", "subscription__user").order_by("pk")[:batch_size])
    active_keys = {}
    ids = []
    for row in candidates:
        user = row.subscription.user
        if user.pk not in active_keys:
            active_keys[user.pk] = {notice["notification_id"] for notice in _eligible_rows(user, now)}
        # A far-future accepted plan can still use this deduplication key.
        # Keep its tombstone until that notification is no longer current.
        if row.notification_key not in active_keys[user.pk]:
            ids.append(row.pk)
    if not dry_run:
        _delete_ids(PushDelivery, ids)
    return len(ids)


def _report_message_candidates(now, report_cutoff, batch_size):
    agreed_since = now - timedelta(hours=24)
    terminal_match = Q(match__status__in=[Match.Status.DECLINED, Match.Status.EXPIRED]) | _agreement_outside_protection_q(
        agreed_since,
        prefix="match__",
    )
    messages = ChatMessage.objects.filter(
        terminal_match,
        created_at__lt=report_cutoff,
    )
    return list(messages.order_by("pk").values_list("pk", "match_id")[:batch_size])


def _match_evidence_is_protected(match, now, report_cutoff):
    if match.status in Match.LIVE_STATUSES:
        return True
    from plusone.services.meetups import _effective_times
    meetup_end = _effective_times(match)[1]
    if match.status == Match.Status.AGREED and meetup_end >= now - timedelta(hours=24):
        return True
    return False


def _cleanup_report_messages(*, now, report_cutoff, batch_size, dry_run):
    """Purge aged terminal-chat evidence under the normal match lock order.

    Live matches and future/recent AGREED handoffs retain business protection.
    A new report does not restart the absolute 90-day age of terminal evidence.
    """
    candidates = _report_message_candidates(now, report_cutoff, batch_size)
    ids_by_match = {}
    for message_id, match_id in candidates:
        ids_by_match.setdefault(match_id, []).append(message_id)

    deleted_or_eligible = 0
    for match_id in sorted(ids_by_match):
        try:
            with locked_match(match_id) as match:
                if _match_evidence_is_protected(match, now, report_cutoff):
                    continue
                eligible_ids = list(
                    ChatMessage.objects.filter(
                        pk__in=ids_by_match[match_id],
                        match_id=match.pk,
                        created_at__lt=report_cutoff,
                    ).values_list("pk", flat=True)
                )
                deleted_or_eligible += len(eligible_ids)
                if not dry_run:
                    ChatMessage.objects.filter(pk__in=eligible_ids).delete()
        except Http404:
            # A concurrent cleanup may have removed the historical match.
            continue
    return deleted_or_eligible


def cleanup_stale_records(
    user_days=7,
    llm_log_days=30,
    event_days=90,
    report_days=MAX_REPORT_RETENTION_DAYS,
    dry_run=True,
    batch_size=DEFAULT_BATCH_SIZE,
):
    """Process bounded identity units and independent record batches.

    Deleting an identity also cascades its ownership graph; batch_size bounds
    selected identities, not every child row. Run production dry-run and begin
    with a small batch before scheduling, particularly with large histories.
    """
    _validate_positive(user_days, "user_days")
    _validate_positive(llm_log_days, "llm_log_days")
    _validate_positive(event_days, "event_days")
    report_days = _report_retention_days(report_days)
    _validate_positive(batch_size, "batch_size")

    now = timezone.now()
    llm_log_ids = _bounded_ids(
        LLMLog.objects.filter(created_at__lt=now - timedelta(days=llm_log_days)),
        batch_size,
    )
    event_ids = _bounded_ids(
        ProductEvent.objects.filter(created_at__lt=now - timedelta(days=event_days)),
        batch_size,
    )
    report_ids = _bounded_ids(
        SafetyReport.objects.filter(created_at__lt=now - timedelta(days=report_days)),
        batch_size,
    )
    activity_report_ids = _bounded_ids(
        ActivityReport.objects.filter(created_at__lt=now - timedelta(days=report_days)), batch_size,
    )
    session_ids = _bounded_ids(Session.objects.filter(expire_date__lte=now), batch_size)
    rate_limit_bucket_ids = _bounded_ids(
        RateLimitBucket.objects.filter(expires_at__lte=now),
        batch_size,
    )
    browser_budget_bucket_ids = _bounded_ids(
        BrowserBudgetBucket.objects.filter(expires_at__lte=now), batch_size,
    )
    presence_lease_ids = _bounded_ids(
        PresenceLease.objects.filter(expires_at__lte=now).exclude(match__status__in=Match.LIVE_STATUSES), batch_size,
    )

    counts = {
        "users": 0,
        "llm_logs": len(llm_log_ids),
        "events": len(event_ids),
        "safety_reports": len(report_ids),
        "activity_reports": len(activity_report_ids),
        "report_messages": 0,
        "sessions": len(session_ids),
        "rate_limit_buckets": len(rate_limit_bucket_ids),
        "browser_budget_buckets": len(browser_budget_bucket_ids),
        "presence_leases": len(presence_lease_ids),
        "push_subscriptions": 0,
        "push_deliveries": 0,
    }
    if not dry_run:
        # Remove content-bearing records before identities. Their user foreign
        # keys use SET_NULL, so identity deletion alone would retain content.
        _delete_ids(LLMLog, llm_log_ids)
        _delete_ids(ProductEvent, event_ids)
        _delete_ids(SafetyReport, report_ids)
        _delete_ids(ActivityReport, activity_report_ids)
        _delete_ids(Session, session_ids)
        _delete_ids(RateLimitBucket, rate_limit_bucket_ids)
        _delete_ids(BrowserBudgetBucket, browser_budget_bucket_ids)
        _delete_ids(PresenceLease, presence_lease_ids)

    counts["report_messages"] = _cleanup_report_messages(
        now=now,
        report_cutoff=now - timedelta(days=report_days),
        batch_size=batch_size,
        dry_run=dry_run,
    )
    counts["push_deliveries"] = _cleanup_push_deliveries(now, batch_size, dry_run)
    counts["push_subscriptions"] = _cleanup_push_subscriptions(now, batch_size, dry_run)
    counts["users"] = _cleanup_users(
        days=user_days,
        report_days=report_days,
        now=now,
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return counts
