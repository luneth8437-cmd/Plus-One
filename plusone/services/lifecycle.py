"""All live state and message writes lock users -> card -> match, in that order.

Never call an external service while holding these locks. PostgreSQL provides
the production concurrency guarantees; SQLite is for single-process development.
"""
from contextlib import contextmanager
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from plusone.models import ActivityPost, ChatMessage, Match, PresenceLease, ProductEvent, UserProfile
from plusone.services.capacity import sync_locked_post


def lock_users(ids):
    return list(get_user_model().objects.select_for_update().filter(pk__in=ids).order_by("pk"))


def identities_retired(ids):
    return UserProfile.objects.filter(user_id__in=ids, retired_at__isnull=False).exists()


@contextmanager
def locked_post(post_id, actor_id=None, participant_ids=()):
    owner_id = get_object_or_404(ActivityPost.objects.only("user_id"), pk=post_id).user_id
    participants = set(Match.objects.filter(post_id=post_id, status__in=Match.HOLDING_STATUSES).values_list("swiper_id", flat=True))
    participants.update(participant_ids)
    participants.add(owner_id)
    if actor_id:
        participants.add(actor_id)
    with transaction.atomic():
        lock_users(participants)
        post = get_object_or_404(ActivityPost.objects.select_for_update(), pk=post_id)
        yield post


@contextmanager
def locked_match(match_id):
    target = get_object_or_404(Match.objects.only("post_id", "poster_id", "swiper_id"), pk=match_id)
    with locked_post(target.post_id, participant_ids=target.participant_ids()) as post:
        match = get_object_or_404(Match.objects.select_for_update(), pk=match_id)
        match.post = post
        yield match


def system_message_locked(match, text):
    return ChatMessage.objects.create(match=match, message=text, is_system=True)


def end_locked(match, *, status=Match.Status.EXPIRED, reason=Match.CloseReason.TIMEOUT, user=None):
    if match.status not in Match.LIVE_STATUSES:
        return False
    was_waiting = match.status == Match.Status.WAITING
    match.status = status
    match.close_reason = reason
    match.closed_by = user
    match.closed_at = timezone.now()
    match.save(update_fields=["status", "close_reason", "closed_by", "closed_at"])
    system_message_locked(match, "This match has ended. Its history is still available; you can report a safety concern below.")
    sync_locked_post(match.post)
    if was_waiting:
        from plusone.services.analytics import log_event
        wait_reason = "card_expired" if reason == Match.CloseReason.TIMEOUT and match.post.expire_time <= match.closed_at else reason
        log_event(ProductEvent.Name.WAIT_CLOSED, user=user, match=match, properties={
            "reason": wait_reason,
            "elapsed_seconds": max(0, int((match.closed_at - match.created_at).total_seconds())),
            "actor_role": "publisher" if user and user.pk == match.poster_id else "guest" if user and user.pk == match.swiper_id else "system",
        })
    return True


def expire_locked(match, now=None):
    if match.plan_meeting_at is None:
        # Old/imported rows acquire the original arrangement only. Their
        # confirmations, deadlines, and unknown end time are not fabricated.
        from plusone.services.meetups import initialize_plan_locked
        initialize_plan_locked(match, legacy=True)
    now = now or timezone.now()
    from plusone.services.safety import blocked_between
    if blocked_between(match.poster_id, match.swiper_id):
        if match.status in Match.LIVE_STATUSES:
            return end_locked(match, reason=Match.CloseReason.BLOCKED)
        if match.status == Match.Status.AGREED and not match.meetup_cancelled_at:
            from plusone.services.meetups import _effective_times, cancel_meetup_locked
            if _effective_times(match)[1] > now:
                return cancel_meetup_locked(match, None, reason="blocked")
    deadline = match.phase_deadline
    if match.status == Match.Status.WAITING:
        deadline = min(deadline, match.post.matching_deadline) if deadline else match.post.matching_deadline
    if match.status in Match.LIVE_STATUSES and deadline and deadline <= now:
        return end_locked(match)
    return False


def refresh_match(match_id):
    with locked_match(match_id) as match:
        expire_locked(match)
        return match


def active_chat_for_user(user_id, *, exclude_match_id=None, now=None):
    now = now or timezone.now()
    chats = Match.objects.filter(Q(poster_id=user_id) | Q(swiper_id=user_id), status=Match.Status.CHATTING).filter(
        Q(chat_expires_at__gt=now) | Q(chat_expires_at__isnull=True)
    )
    return chats.exclude(pk=exclude_match_id) if exclude_match_id is not None else chats


def participant_presence(match, user_id, now=None):
    now = now or timezone.now()
    leases = PresenceLease.objects.filter(match=match, user_id=user_id)
    last_seen = leases.filter(visible=True, expires_at__gt=now).aggregate(last_seen=Max("last_visible_at"))["last_seen"]
    if last_seen or leases.exists():
        return last_seen
    # Old clients/imported rows may still have a recent participant signal.
    # Once this actor owns a lease, an old projection cannot revive it.
    old_signal = match.poster_last_present_at if user_id == match.poster_id else match.swiper_last_present_at
    return old_signal if old_signal and old_signal >= now - timedelta(seconds=15) else None


def _presence_input(user, tab_id, sequence):
    from plusone.services.requests import RequestError
    legacy = tab_id is None and sequence is None
    if legacy:
        return uuid5(NAMESPACE_URL, f"plusone:legacy-presence:{user.pk}"), None
    try:
        tab_id = UUID(str(tab_id))
        if isinstance(sequence, bool):
            raise ValueError
        sequence = int(sequence)
        if sequence < 1 or sequence > 9223372036854775807:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise RequestError("Presence needs a valid tab ID and increasing sequence.")
    return tab_id, sequence


def set_presence(match_id, user, visible, *, tab_id=None, sequence=None):
    from plusone.services.analytics import log_event
    tab_id, sequence = _presence_input(user, tab_id, sequence)
    with locked_match(match_id) as match:
        if not match.is_participant(user):
            return None
        now = timezone.now()
        expire_locked(match, now)
        if identities_retired(match.participant_ids()):
            end_locked(match, reason=Match.CloseReason.RESET)
            return match
        if match.status not in Match.LIVE_STATUSES:
            return match
        lease, _ = PresenceLease.objects.get_or_create(match=match, user=user, tab_id=tab_id)
        sequence = lease.sequence + 1 if sequence is None else sequence
        if sequence <= lease.sequence:
            return match
        lease.sequence = sequence
        lease.visible = bool(visible)
        lease.expires_at = now + timedelta(seconds=15) if visible else now
        if visible:
            lease.last_visible_at = now
        lease.save(update_fields=["sequence", "visible", "last_visible_at", "expires_at"])
        field = "poster_last_present_at" if user.pk == match.poster_id else "swiper_last_present_at"
        setattr(match, field, participant_presence(match, user.pk, now))
        match.save(update_fields=[field])
        if (match.status == Match.Status.WAITING
                and participant_presence(match, match.poster_id, now)
                and participant_presence(match, match.swiper_id, now)
                and not active_chat_for_user(match.poster_id, exclude_match_id=match.pk, now=now).exists()
                and not active_chat_for_user(match.swiper_id, exclude_match_id=match.pk, now=now).exists()):
            match.status = Match.Status.CHATTING
            match.chat_started_at = now
            match.chat_expires_at = now + timedelta(minutes=5)
            match.save(update_fields=["status", "chat_started_at", "chat_expires_at"])
            system_message_locked(match, f"You are both here. Start with: What are you looking forward to about {match.post.title}?")
            log_event(ProductEvent.Name.CHAT_STARTED, match=match, post=match.post)
        return match


def phase_payload(match, user):
    from plusone.services.meetups import plan_payload
    from plusone.services.matching import waiting_retry_state
    from plusone.services.safety import closed_notice
    now = timezone.now()
    poster = user.pk == match.poster_id
    other_id = match.swiper_id if poster else match.poster_id
    mine = participant_presence(match, user.pk, now)
    other = participant_presence(match, other_id, now)
    viewer_busy = active_chat_for_user(user.pk, exclude_match_id=match.pk, now=now).exists()
    other_busy = active_chat_for_user(other_id, exclude_match_id=match.pk, now=now).exists()
    return {
        "phase": match.status, "chat_status": match.status,
        "closure_notice": closed_notice(match),
        "chat_active": match.status == Match.Status.CHATTING,
        "server_time": now.isoformat(),
        "phase_deadline": match.phase_deadline.isoformat() if match.phase_deadline else None,
        "viewer_agreed": match.poster_agreed if poster else match.swiper_agreed,
        "other_agreed": match.swiper_agreed if poster else match.poster_agreed,
        "viewer_present": bool(mine),
        "other_present": bool(other),
        "viewer_busy": viewer_busy, "other_busy": other_busy,
        "waiting_reason": "both_busy" if viewer_busy and other_busy else "viewer_busy" if viewer_busy else "other_busy" if other_busy else "waiting_for_presence",
        "plan": plan_payload(match, user),
        "waiting_retry": waiting_retry_state(match, user) if match.status == Match.Status.EXPIRED else None,
    }
