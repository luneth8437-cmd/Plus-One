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
        # Old pages can confirm only the untouched original arrangement.
        # An edited plan must be explicitly reviewed with its current revision.
        if match.plan_revision != 1:
            return AgreementResult(False, match.id)
        from plusone.services.meetups import confirm_plan_locked
        confirm_plan_locked(match, user, 1)
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
        if match.status == Match.Status.AGREED:
            from plusone.services.meetups import cancel_meetup_locked
            cancel_meetup_locked(match, user, reason="reported")
        else:
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
        if not match.is_participant(user) or match.status != Match.Status.AGREED or match.meetup_cancelled_at or match.plan_revision != 1:
            return False
        if identities_retired([user.pk]):
            return False
        expire_locked(match)
        from plusone.services.meetups import _effective_times, effective_outcome
        from datetime import timedelta
        from django.utils import timezone
        meeting_at, end_at = _effective_times(match)
        if not meeting_at <= timezone.now() <= end_at + timedelta(hours=24):
            return False
        # Preserve old original-plan pages while synchronizing their outcome
        # with the new handoff UI. Edited plans use the versioned dispatcher.
        mine = "poster" if user.pk == match.poster_id else "swiper"
        existing_outcome = effective_outcome(match, user.pk)
        if existing_outcome == "met":
            return True
        if existing_outcome == "not_met":
            return False
        if not getattr(match, f"{mine}_meetup_outcome"):
            setattr(match, f"{mine}_meetup_outcome", "met")
            match.save(update_fields=[f"{mine}_meetup_outcome"])
        log_event(ProductEvent.Name.MEETUP_CONFIRMED, user=user, match=match)
        return True
