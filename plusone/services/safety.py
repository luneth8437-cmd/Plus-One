"""Private safety reports and identity blocks, without requiring a match."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from plusone.models import ActivityReport, Match, SafetyReport, UserBlock
from plusone.services.lifecycle import expire_locked, identities_retired, locked_match, locked_post, lock_users
from plusone.services.requests import RequestError


def _actor_id(user):
    if not getattr(user, "is_authenticated", False) or user.pk is None:
        raise RequestError("Start an anonymous session before using safety controls.", 403)
    return user.pk


def _active_actor(user):
    actor_id = _actor_id(user)
    if identities_retired([actor_id]):
        raise RequestError("This identity has been reset. Refresh before using safety controls.", 409)
    return actor_id


def blocked_between(first_id, second_id):
    """Blocks prevent new contact in both directions."""
    return UserBlock.objects.filter(
        Q(blocker_id=first_id, target_id=second_id)
        | Q(blocker_id=second_id, target_id=first_id)
    ).exists()


def blocked_user_ids(user):
    """A lazy ID query suitable for excluding authors from Discover."""
    user_id = _actor_id(user)
    outgoing = UserBlock.objects.filter(blocker_id=user_id).values("target_id")
    incoming = UserBlock.objects.filter(target_id=user_id).values("blocker_id")
    return get_user_model().objects.filter(
        Q(pk__in=outgoing) | Q(pk__in=incoming)
    ).values_list("pk", flat=True)


def create_block_locked(user, target_id):
    """Caller holds both user locks; existing match reports share this path."""
    actor_id = _active_actor(user)
    if actor_id == target_id:
        raise RequestError("You cannot block your own identity.")
    return UserBlock.objects.get_or_create(blocker_id=actor_id, target_id=target_id)[0]


def close_blocked_relationships(first_id, second_id):
    """Converge each pair after releasing the block/report transaction locks.

    Each match acquires its own users -> card -> match locks. Keeping this out
    of the block-writing transaction avoids acquiring another card's users
    while already holding the first card's locks. New contact is fenced by
    the committed block before existing relationships are enumerated.
    """
    from plusone.services.meetups import _effective_times
    pairs = Q(poster_id=first_id, swiper_id=second_id) | Q(poster_id=second_id, swiper_id=first_id)
    now = timezone.now()
    candidates = Match.objects.filter(pairs).filter(
        Q(status__in=Match.LIVE_STATUSES) | Q(status=Match.Status.AGREED, meetup_cancelled_at__isnull=True)
    ).select_related("post").order_by("pk")
    match_ids = [match.pk for match in candidates if match.status in Match.LIVE_STATUSES or _effective_times(match)[1] > now]
    for match_id in match_ids:
        with locked_match(match_id) as match:
            if match.status == Match.Status.AGREED and _effective_times(match)[1] <= timezone.now():
                continue
            expire_locked(match)


def closed_notice(match):
    """Explain the terminal state without disclosing private safety actions."""
    if match.status == Match.Status.AGREED:
        if match.meetup_cancelled_at:
            return "This meetup was cancelled. Do not travel based on the previous plan."
        from plusone.services.meetups import _effective_times
        if _effective_times(match)[1] <= timezone.now():
            return "The meeting window has ended. Your plan and feedback remain available."
        return "Chat complete. Use the meeting plan to coordinate your arrival."
    if match.status in Match.LIVE_STATUSES:
        return ""
    if match.close_reason == Match.CloseReason.CANCELLED:
        return "This activity was cancelled. No meetup was confirmed."
    if match.close_reason == Match.CloseReason.DECLINED:
        return "A participant exited this match. No meetup was confirmed."
    if match.close_reason == Match.CloseReason.TIMEOUT:
        if not match.chat_started_at:
            if match.closed_at and match.post.expire_time <= match.closed_at:
                return "The activity card expired before you both joined. No chat started."
            return "The wait ended before you both joined. No chat started."
        return "Time ran out before you both agreed. No meetup was confirmed."
    # Reporting, blocking and retiring an identity all have the same public
    # explanation. Do not expose who used a private safety control or why.
    return "This match has ended. No meetup was confirmed. Your history and safety report remain available."


def block_user(user, target_id):
    actor_id = _actor_id(user)
    try:
        if isinstance(target_id, bool):
            raise ValueError
        target_id = int(target_id)
    except (TypeError, ValueError):
        raise RequestError("Choose a valid identity to block.")
    if target_id <= 0:
        raise RequestError("Choose a valid identity to block.")
    if actor_id == target_id:
        raise RequestError("You cannot block your own identity.")
    with transaction.atomic():
        users = lock_users([actor_id, target_id])
        if target_id not in {item.pk for item in users}:
            raise RequestError("This identity is no longer available.", 404)
        block = create_block_locked(user, target_id)
    close_blocked_relationships(actor_id, target_id)
    return block


def block_post_owner(post_id, user):
    actor_id = _actor_id(user)
    with locked_post(post_id, actor_id) as post:
        block = create_block_locked(user, post.user_id)
        target_id = post.user_id
    close_blocked_relationships(actor_id, target_id)
    return block


def unblock_user(user, block_id):
    actor_id = _actor_id(user)
    block = get_object_or_404(UserBlock.objects.only("pk", "blocker_id", "target_id"), pk=block_id)
    if block.blocker_id != actor_id:
        raise RequestError("You can only remove blocks you created.", 403)
    with transaction.atomic():
        lock_users([actor_id, block.target_id])
        _active_actor(user)
        deleted, _ = UserBlock.objects.filter(pk=block.pk, blocker_id=actor_id).delete()
        return bool(deleted)


def report_post(post_id, user, category="other", reason="", *, block=True):
    actor_id = _actor_id(user)
    if not isinstance(reason, str) or category not in ActivityReport.Category.values or len(reason) > 500:
        raise RequestError("Choose a report category and use at most 500 characters.")
    with locked_post(post_id, actor_id) as post:
        _active_actor(user)
        if post.user_id == actor_id:
            raise RequestError("You cannot report your own card.")
        report, created = ActivityReport.objects.get_or_create(
            post=post, reporter=user, defaults={
                "category": category, "reason": reason,
                "title_snapshot": post.title,
                "description_snapshot": post.description,
                "location_snapshot": post.location.name,
                "start_time_snapshot": post.start_time,
                "expected_end_time_snapshot": post.expected_end_time,
            },
        )
        if not created:
            if report.created_at <= timezone.now() - timedelta(days=90):
                raise RequestError("This report has reached its retention deadline. It cannot be supplemented; contact site support for a new concern.", 410)
            if report.status == ActivityReport.Status.RESOLVED:
                report.status = ActivityReport.Status.PENDING
            report.category = category
            report.reason = reason
            report.save(update_fields=["category", "reason", "status", "updated_at"])
        if block:
            create_block_locked(user, post.user_id)
        target_id = post.user_id
    if block:
        close_blocked_relationships(actor_id, target_id)
    return report


def own_report_statuses(user):
    """Only the reporter sees their status; moderator notes remain private."""
    _actor_id(user)
    rows = []
    for report in ActivityReport.objects.filter(reporter=user).select_related("post").order_by("-created_at")[:100]:
        rows.append({
            "title": report.title_snapshot or report.post.title,
            "category": report.get_category_display(),
            "status": report.get_status_display(),
            "created_at": report.created_at,
            "url": reverse("post_detail", args=[report.post_id]),
        })
    for report in SafetyReport.objects.filter(reporter=user).select_related("match__post").order_by("-created_at")[:100]:
        rows.append({
            "title": report.match.post.title,
            "category": report.get_category_display(),
            "status": report.get_status_display(),
            "created_at": report.created_at,
            "url": reverse("chat", args=[report.match_id]),
        })
    return sorted(rows, key=lambda item: item["created_at"], reverse=True)[:100]
