from dataclasses import dataclass

from plusone.ai import moderate_text
from plusone.models import ChatMessage, Match, ProductEvent, SafetyReport
from plusone.services.analytics import log_event, log_message_events
from plusone.services.lifecycle import end_locked, expire_locked, locked_match, identities_retired
from plusone.services.requests import RequestError, check_replay, consume_limit, fingerprint, request_uuid


@dataclass(frozen=True)
class AgreementResult:
    recorded: bool
    match_id: int


@dataclass(frozen=True)
class CloseMatchResult:
    closed: bool
    match_id: int


def record_agreement(match_id, user):
    with locked_match(match_id) as match:
        if not match.is_participant(user):
            return AgreementResult(False, match.id)
        expire_locked(match)
        if identities_retired(match.participant_ids()):
            end_locked(match, reason=Match.CloseReason.RESET)
        if match.status != Match.Status.CHATTING:
            return AgreementResult(False, match.id)
        field = "poster_agreed" if user.pk == match.poster_id else "swiper_agreed"
        if not getattr(match, field):
            setattr(match, field, True)
            match.status = Match.Status.AGREED if match.poster_agreed and match.swiper_agreed else Match.Status.CHATTING
            match.save(update_fields=[field, "status"])
            log_event(ProductEvent.Name.AGREE_CLICKED, user=user, match=match, properties={"both_agreed": match.status == Match.Status.AGREED})
            if match.status == Match.Status.AGREED:
                log_event(ProductEvent.Name.BOTH_AGREED, match=match)
        return AgreementResult(True, match.id)


def close_match(match_id, user, reason):
    if reason == Match.CloseReason.REPORTED:
        report_match(match_id, user)
        return CloseMatchResult(True, match_id)
    with locked_match(match_id) as match:
        if not match.is_participant(user):
            return CloseMatchResult(False, match.id)
        expire_locked(match)
        closed = end_locked(match, status=Match.Status.DECLINED, reason=Match.CloseReason.DECLINED, user=user)
        return CloseMatchResult(closed, match.id)


def report_match(match_id, user, category="other", reason=""):
    if category not in SafetyReport.Category.values or len(reason) > 500:
        raise RequestError("Choose a report category and use at most 500 characters.")
    with locked_match(match_id) as match:
        if not match.is_participant(user):
            raise RequestError("Only participants can report this match.", 403)
        report, created = SafetyReport.objects.get_or_create(match=match, reporter=user, defaults={"category": category, "reason": reason})
        if not created:
            from datetime import timedelta
            from django.utils import timezone
            if report.created_at <= timezone.now() - timedelta(days=90):
                raise RequestError("This report has reached its retention deadline. It cannot be supplemented; contact site support for a new concern.", 410)
            if report.status == SafetyReport.Status.RESOLVED:
                report.status = SafetyReport.Status.PENDING
            report.category = category
            report.reason = reason
            report.save(update_fields=["category", "reason", "status", "updated_at"])
        expire_locked(match)
        end_locked(match, status=Match.Status.DECLINED, reason=Match.CloseReason.REPORTED, user=user)
        return report


def message_replay(match, user, request_id, digest):
    return check_replay(ChatMessage.objects.filter(match=match, sender=user, request_id=request_id).first(), digest)


def create_chat_message(match, user, text, request_id=None):
    if not match.is_participant(user):
        return None, {"flagged": False, "unavailable": True}
    request_id = request_uuid(request_id)
    digest = fingerprint({"message": text})
    replay = message_replay(match, user, request_id, digest)
    if replay:
        return replay, {"flagged": False, "replayed": True}
    if len(text) > 500 or not text.strip():
        raise RequestError("Messages must contain 1–500 characters.")
    with locked_match(match.pk) as current:
        expire_locked(current)
        if identities_retired(current.participant_ids()):
            end_locked(current, reason=Match.CloseReason.RESET)
        if current.status != Match.Status.CHATTING:
            return None, {"flagged": False, "unavailable": True}
    consume_limit(user, "message", request_id=request_id, digest=digest)
    moderation = moderate_text(user, text)
    if moderation.get("flagged") or moderation.get("service_unavailable"):
        return None, moderation
    with locked_match(match.pk) as current:
        replay = message_replay(current, user, request_id, digest)
        if replay:
            return replay, {"flagged": False, "replayed": True}
        expire_locked(current)
        if identities_retired(current.participant_ids()):
            end_locked(current, reason=Match.CloseReason.RESET)
        if current.status != Match.Status.CHATTING:
            return None, {**moderation, "unavailable": True}
        message = ChatMessage.objects.create(match=current, sender=user, message=text, request_id=request_id, request_fingerprint=digest)
        log_message_events(current, user, text)
        return message, moderation


def confirm_meetup(match_id, user):
    with locked_match(match_id) as match:
        if not match.is_participant(user) or match.status != Match.Status.AGREED:
            return False
        log_event(ProductEvent.Name.MEETUP_CONFIRMED, user=user, match=match)
        return True
