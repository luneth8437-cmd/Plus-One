from dataclasses import dataclass
from datetime import timedelta
from time import sleep

from django.conf import settings
from django.db import OperationalError
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from plusone.models import ActivityPost, Match, ProductEvent, Swipe
from plusone.services.analytics import log_event
from plusone.services.capacity import effective_capacity, holding_match_count
from plusone.services.lifecycle import expire_locked, locked_post, identities_retired
from plusone.services.safety import blocked_between
from plusone.services.requests import RequestError

SQLITE_LOCK_RETRY_DELAYS = (0.05, 0.15)


class SwipeOutcome:
    OWN_POST = "own_post"
    INACTIVE_POST = "inactive_post"
    FULL_POST = "full_post"
    INVALID_ACTION = "invalid_action"
    PASSED = "passed"
    MATCH_CREATED = "match_created"
    MATCH_EXISTS = "match_exists"
    TRY_AGAIN = "try_again"


@dataclass(frozen=True)
class SwipeResult:
    outcome: str
    post_id: int
    match_id: int | None = None


def handle_swipe(user, post_id, action):
    for attempt, delay in enumerate((0, *SQLITE_LOCK_RETRY_DELAYS)):
        if delay:
            sleep(delay)
        try:
            result, created_match = _record_swipe(user, post_id, action)
            break
        except OperationalError as error:
            if not _is_database_lock_error(error):
                raise
            if attempt == len(SQLITE_LOCK_RETRY_DELAYS):
                try:
                    return _swipe_lock_fallback(user, post_id, action)
                except OperationalError as fallback_error:
                    if not _is_database_lock_error(fallback_error):
                        raise
                    return SwipeResult(SwipeOutcome.TRY_AGAIN, post_id)

    if result.outcome != SwipeOutcome.MATCH_CREATED:
        return result

    return result


def _record_swipe(user, post_id, action):
    created_match = None

    with locked_post(post_id, user.pk) as post:
        if identities_retired([user.pk, post.user_id]):
            return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id), created_match
        if blocked_between(user.pk, post.user_id):
            return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id), created_match
        # Lock the post while recording a swipe so two users cannot create
        # competing matches for the same active card at the same time.
        for current in Match.objects.select_for_update().filter(post=post, status__in=Match.LIVE_STATUSES).order_by("pk"):
            current.post = post
            expire_locked(current)
        if post.user_id == user.id:
            return SwipeResult(SwipeOutcome.OWN_POST, post.id), created_match
        if action not in [Swipe.Action.INTERESTED, Swipe.Action.PASS]:
            return SwipeResult(SwipeOutcome.INVALID_ACTION, post.id), created_match

        existing = Match.objects.filter(post=post, swiper=user).order_by("-created_at", "-pk").first()
        if action == Swipe.Action.INTERESTED and existing:
            return SwipeResult(SwipeOutcome.MATCH_EXISTS, post.id, existing.pk), created_match

        if action == Swipe.Action.PASS:
            if post.is_expired:
                return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id), created_match
            Swipe.objects.update_or_create(user=user, post=post, defaults={"action": action})
            return SwipeResult(SwipeOutcome.PASSED, post.id), created_match

        if _post_is_full(post):
            return SwipeResult(SwipeOutcome.FULL_POST, post.id), created_match
        if post.status != ActivityPost.Status.ACTIVE or post.is_expired or post.activity_window_end <= timezone.now():
            return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id), created_match

        if not settings.PLUSONE_NEW_MATCHES_ENABLED:
            return SwipeResult(SwipeOutcome.TRY_AGAIN, post.id), created_match
        Swipe.objects.update_or_create(user=user, post=post, defaults={"action": action})
        match = Match.objects.create(
            post=post,
            swiper=user,
            poster=post.user,
            status=Match.Status.WAITING,
            waiting_expires_at=min(timezone.now() + timedelta(minutes=10), post.matching_deadline),
        )

        from plusone.services.meetups import initialize_plan_locked
        initialize_plan_locked(match)

        post.status = (
            ActivityPost.Status.MATCHED
            if holding_match_count(post) >= effective_capacity(post)
            else ActivityPost.Status.ACTIVE
        )
        post.save(update_fields=["status", "updated_at"])
        created_match = match
        log_event(ProductEvent.Name.MATCH_CREATED, user=user, post=post, match=match, properties={"activity_type": post.activity_type})

    return SwipeResult(SwipeOutcome.MATCH_CREATED, post.id, created_match.id), created_match


def _is_database_lock_error(error):
    message = str(error).lower()
    return "database is locked" in message or "database table is locked" in message


def _post_is_full(post):
    return not post.is_expired and holding_match_count(post) >= effective_capacity(post)


def _swipe_lock_fallback(user, post_id, action):
    post = get_object_or_404(ActivityPost.objects.select_related("user", "location"), id=post_id)
    if post.user_id == user.id:
        return SwipeResult(SwipeOutcome.OWN_POST, post.id)
    if identities_retired([user.pk, post.user_id]) or blocked_between(user.pk, post.user_id):
        return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id)

    existing_match = Match.objects.filter(post=post, swiper=user).order_by("-created_at", "-pk").first()
    if existing_match:
        return SwipeResult(SwipeOutcome.MATCH_EXISTS, post.id, existing_match.id)

    if action not in [Swipe.Action.INTERESTED, Swipe.Action.PASS]:
        return SwipeResult(SwipeOutcome.INVALID_ACTION, post.id)
    if action == Swipe.Action.PASS:
        if post.is_expired:
            return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id)
        Swipe.objects.update_or_create(user=user, post=post, defaults={"action": action})
        return SwipeResult(SwipeOutcome.PASSED, post.id)
    if _post_is_full(post):
        return SwipeResult(SwipeOutcome.FULL_POST, post.id)
    if post.status != ActivityPost.Status.ACTIVE or post.matching_deadline <= timezone.now():
        return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id)
    return SwipeResult(SwipeOutcome.TRY_AGAIN, post.id)


def waiting_timed_out(match):
    return (match.status == Match.Status.EXPIRED and match.close_reason == Match.CloseReason.TIMEOUT
            and match.waiting_expires_at is not None and match.chat_started_at is None)


def waiting_retry_state(match, user):
    if not match.is_participant(user):
        return {"can_retry": False, "later_attempt_url": None,
                "reason": "Only the two participants can view this invitation's next steps."}
    child = Match.objects.filter(retry_of_id=match.pk).first()
    if child:
        return {"can_retry": False, "later_attempt_url": reverse("chat", args=[child.pk]),
                "reason": "A later attempt already exists. Open it to see its current status."}
    can_retry = bool(waiting_timed_out(match)
                     and match.post.status == ActivityPost.Status.ACTIVE
                     and match.post.matching_deadline > timezone.now()
                     and not identities_retired(match.participant_ids())
                     and not blocked_between(match.poster_id, match.swiper_id)
                     and not _post_is_full(match.post) and settings.PLUSONE_NEW_MATCHES_ENABLED)
    return {"can_retry": can_retry, "later_attempt_url": None,
            "reason": "You can invite the same Plus One again while this card is still available." if can_retry
                      else "This attempt stays in history. Browse other plans or publish a new card if you still want to meet."}


def retry_waiting_match(user, match_id):
    """An explicit, one-child retry; replay never restarts either attempt."""
    target = get_object_or_404(Match.objects.only("post_id", "poster_id", "swiper_id"), pk=match_id)
    if not target.is_participant(user):
        raise RequestError("Only the two participants can invite each other again.", 403)
    with locked_post(target.post_id, user.pk, target.participant_ids()) as post:
        prior = get_object_or_404(Match.objects.select_for_update(), pk=match_id)
        prior.post = post
        if identities_retired(prior.participant_ids()) or blocked_between(prior.poster_id, prior.swiper_id):
            raise RequestError("This invitation is no longer available.", 409)
        child = Match.objects.filter(retry_of=prior).first()
        if child:
            return SwipeResult(SwipeOutcome.MATCH_EXISTS, post.pk, child.pk)
        expire_locked(prior)
        if not waiting_timed_out(prior):
            raise RequestError("Only a waiting room that timed out before chat began can be invited again.", 409)
        # The card/user locks also fence another guest or a simultaneous retry.
        for current in Match.objects.select_for_update().filter(post=post, status__in=Match.LIVE_STATUSES).order_by("pk"):
            current.post = post
            expire_locked(current)
        if post.matching_deadline <= timezone.now() or post.status != ActivityPost.Status.ACTIVE or _post_is_full(post):
            raise RequestError("This card is no longer available for another invitation. Browse other plans or publish a new card.", 409)
        if not settings.PLUSONE_NEW_MATCHES_ENABLED:
            raise RequestError("New invitations are paused. Your earlier attempt is kept.", 409)
        child = Match.objects.create(post=post, poster_id=prior.poster_id, swiper_id=prior.swiper_id,
            status=Match.Status.WAITING, retry_of=prior,
            waiting_expires_at=min(timezone.now() + timedelta(minutes=10), post.matching_deadline))
        from plusone.services.meetups import initialize_plan_locked
        initialize_plan_locked(child)
        post.status = ActivityPost.Status.MATCHED
        post.save(update_fields=["status", "updated_at"])
        log_event(ProductEvent.Name.MATCH_CREATED, user=user, match=child,
                  properties={"activity_type": post.activity_type, "retry_of": prior.pk})
        return SwipeResult(SwipeOutcome.MATCH_CREATED, post.pk, child.pk)
