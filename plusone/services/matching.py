from dataclasses import dataclass
from datetime import timedelta
from time import sleep

from django.db import OperationalError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from plusone.ai import generate_icebreaker
from plusone.models import ActivityPost, ChatMessage, Match, Swipe
from plusone.services.capacity import effective_capacity, holding_match_count

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

    # The external AI call is kept outside the transaction so a slow provider
    # does not hold a database row lock.
    icebreaker = generate_icebreaker(user, created_match.post)
    ChatMessage.objects.create(match=created_match, sender=None, message=icebreaker, is_system=True)
    return result


def _record_swipe(user, post_id, action):
    created_match = None

    with transaction.atomic():
        # Lock the post while recording a swipe so two users cannot create
        # competing matches for the same active card at the same time.
        post = get_object_or_404(
            ActivityPost.objects.select_for_update().select_related("user", "location"),
            id=post_id,
        )
        if post.user_id == user.id:
            return SwipeResult(SwipeOutcome.OWN_POST, post.id), created_match
        if action not in [Swipe.Action.INTERESTED, Swipe.Action.PASS]:
            return SwipeResult(SwipeOutcome.INVALID_ACTION, post.id), created_match

        if action == Swipe.Action.PASS:
            if post.is_expired:
                return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id), created_match
            Swipe.objects.update_or_create(user=user, post=post, defaults={"action": action})
            return SwipeResult(SwipeOutcome.PASSED, post.id), created_match

        if _post_is_full(post):
            return SwipeResult(SwipeOutcome.FULL_POST, post.id), created_match
        if post.status != ActivityPost.Status.ACTIVE:
            return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id), created_match

        Swipe.objects.update_or_create(user=user, post=post, defaults={"action": action})
        if _post_is_full(post):
            return SwipeResult(SwipeOutcome.FULL_POST, post.id), created_match

        match, created = Match.objects.get_or_create(
            post=post,
            swiper=user,
            defaults={
                "poster": post.user,
                "chat_expires_at": timezone.now() + timedelta(minutes=5),
            },
        )
        if not created:
            return SwipeResult(SwipeOutcome.MATCH_EXISTS, post.id, match.id), created_match

        post.status = (
            ActivityPost.Status.MATCHED
            if holding_match_count(post) >= effective_capacity(post)
            else ActivityPost.Status.ACTIVE
        )
        post.save(update_fields=["status", "updated_at"])
        created_match = match

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

    existing_match = Match.objects.filter(post=post, swiper=user).first()
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
    if post.status != ActivityPost.Status.ACTIVE:
        return SwipeResult(SwipeOutcome.INACTIVE_POST, post.id)
    return SwipeResult(SwipeOutcome.TRY_AGAIN, post.id)
