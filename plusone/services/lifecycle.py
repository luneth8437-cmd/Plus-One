"""All live state and message writes lock users -> card -> match, in that order.

Never call an external service while holding these locks. PostgreSQL provides
the production concurrency guarantees; SQLite is for single-process development.
"""
from contextlib import contextmanager
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from plusone.models import ActivityPost, ChatMessage, Match, ProductEvent, UserProfile
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
    match.status = status
    match.close_reason = reason
    match.closed_by = user
    match.closed_at = timezone.now()
    match.save(update_fields=["status", "close_reason", "closed_by", "closed_at"])
    system_message_locked(match, "This match has ended. Its history is still available; you can report a safety concern below.")
    sync_locked_post(match.post)
    return True


def expire_locked(match, now=None):
    if match.plan_meeting_at is None:
        # Old/imported rows acquire the original arrangement only. Their
        # confirmations, deadlines, and unknown end time are not fabricated.
        from plusone.services.meetups import initialize_plan_locked
        initialize_plan_locked(match, legacy=True)
    now = now or timezone.now()
    deadline = match.phase_deadline
    if match.status == Match.Status.WAITING:
        deadline = min(deadline, match.post.expire_time) if deadline else match.post.expire_time
    if match.status in Match.LIVE_STATUSES and deadline and deadline <= now:
        return end_locked(match)
    return False


def refresh_match(match_id):
    with locked_match(match_id) as match:
        expire_locked(match)
        return match


def set_presence(match_id, user, visible):
    from plusone.services.analytics import log_event
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
        field = "poster_last_present_at" if user.pk == match.poster_id else "swiper_last_present_at"
        setattr(match, field, now if visible else None)
        match.save(update_fields=[field])
        threshold = now - timedelta(seconds=15)
        if (match.status == Match.Status.WAITING and match.poster_last_present_at
                and match.swiper_last_present_at
                and match.poster_last_present_at >= threshold and match.swiper_last_present_at >= threshold):
            match.status = Match.Status.CHATTING
            match.chat_started_at = now
            match.chat_expires_at = now + timedelta(minutes=5)
            match.save(update_fields=["status", "chat_started_at", "chat_expires_at"])
            system_message_locked(match, f"You are both here. Start with: What are you looking forward to about {match.post.title}?")
            log_event(ProductEvent.Name.CHAT_STARTED, match=match, post=match.post)
        return match


def phase_payload(match, user):
    from plusone.services.meetups import plan_payload
    now = timezone.now()
    threshold = now - timedelta(seconds=15)
    poster = user.pk == match.poster_id
    mine = match.poster_last_present_at if poster else match.swiper_last_present_at
    other = match.swiper_last_present_at if poster else match.poster_last_present_at
    return {
        "phase": match.status, "chat_status": match.status,
        "chat_active": match.status == Match.Status.CHATTING,
        "server_time": now.isoformat(),
        "phase_deadline": match.phase_deadline.isoformat() if match.phase_deadline else None,
        "viewer_agreed": match.poster_agreed if poster else match.swiper_agreed,
        "other_agreed": match.swiper_agreed if poster else match.poster_agreed,
        "viewer_present": bool(mine and mine >= threshold),
        "other_present": bool(other and other >= threshold),
        "plan": plan_payload(match, user),
    }
