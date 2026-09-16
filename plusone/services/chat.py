from dataclasses import dataclass

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from plusone.ai import moderate_text
from plusone.models import ChatMessage, Match, ProductEvent
from plusone.services.analytics import log_event, log_message_events
from plusone.services.capacity import sync_post_status_for_capacity


@dataclass(frozen=True)
class AgreementResult:
    recorded: bool
    match_id: int


@dataclass(frozen=True)
class CloseMatchResult:
    closed: bool
    match_id: int


def record_agreement(match_id, user):
    with transaction.atomic():
        # Agreement is a two-sided state transition, so lock the match before
        # checking participant status and updating the agreed flags.
        match = get_object_or_404(
            Match.objects.select_for_update().select_related("post", "poster", "swiper", "post__location"),
            id=match_id,
        )
        if not match.is_participant(user):
            return AgreementResult(False, match.id)

        match.mark_chat_expired_if_needed()
        if match.status != Match.Status.CHATTING:
            return AgreementResult(False, match.id)

        match.mark_agreed(user)
        log_event(
            ProductEvent.Name.AGREE_CLICKED,
            user=user,
            match=match,
            properties={"both_agreed": match.poster_agreed and match.swiper_agreed},
        )
        return AgreementResult(True, match.id)


def close_match(match_id, user, reason):
    with transaction.atomic():
        match = get_object_or_404(
            Match.objects.select_for_update().select_related("post", "poster", "swiper", "post__location"),
            id=match_id,
        )
        if not match.is_participant(user):
            return CloseMatchResult(False, match.id)

        match.mark_chat_expired_if_needed()
        if match.status != Match.Status.CHATTING:
            return CloseMatchResult(False, match.id)

        reason = reason if reason in Match.CloseReason.values else Match.CloseReason.DECLINED
        match.status = Match.Status.DECLINED
        match.closed_by = user
        match.close_reason = reason
        match.closed_at = timezone.now()
        match.save(update_fields=["status", "closed_by", "close_reason", "closed_at"])
        ChatMessage.objects.create(
            match=match,
            sender=None,
            message=_close_message(reason),
            is_system=True,
        )

    sync_post_status_for_capacity(match.post)
    return CloseMatchResult(True, match.id)


def _close_message(reason):
    if reason == Match.CloseReason.REPORTED:
        return "Safety report submitted. This chat is now closed."
    return "One participant declined. This chat is now closed."


def create_chat_message(match, user, text):
    if not match.is_participant(user) or match.status != Match.Status.CHATTING or match.chat_expired:
        return None, {"flagged": False, "unavailable": True}

    moderation = moderate_text(user, text)
    if moderation.get("flagged"):
        # Unsafe messages are not persisted; logs still keep the moderation
        # decision for audit/debugging through LLMLog.
        return None, moderation
    # Moderation happens before taking the lock so a slow provider call cannot
    # block agreement/decline requests. Re-read the match under a row lock
    # afterwards so a message cannot be written to a chat that closed while
    # moderation was running.
    with transaction.atomic():
        locked_match = Match.objects.select_for_update().filter(id=match.id).first()
        if not locked_match or not locked_match.is_participant(user):
            return None, {**moderation, "unavailable": True}
        locked_match.mark_chat_expired_if_needed()
        if locked_match.status != Match.Status.CHATTING:
            return None, {**moderation, "unavailable": True}

        message = ChatMessage.objects.create(
            match=locked_match,
            sender=user,
            message=text,
            is_flagged=False,
        )
        # Funnel events are derived server-side after the write; only the
        # opener-usage classification is stored, never the message text.
        log_message_events(locked_match, user, text)
    return message, moderation
