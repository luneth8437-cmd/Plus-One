"""A pair's versioned arrangement and explicitly self-reported meetup state."""
from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db.models import Q

from plusone.ai import moderate_text
from plusone.models import ActivityPost, Match, MeetupAction, ProductEvent
from plusone.services.analytics import log_event
from plusone.services.capacity import holding_match_count
from plusone.services.lifecycle import expire_locked, identities_retired, locked_match, system_message_locked
from plusone.services.requests import RequestError, check_replay, consume_limit, fingerprint, request_uuid


ACTIONS = {"update_plan", "confirm_plan", "arrived", "delayed", "coordination", "cancel_meetup", "outcome", "reopen_card"}
OUTCOME_REASONS = {"no_show", "time_conflict", "cancelled", "other"}


def _arrival_details(match, actor, now, historical=False, viewer=False):
    eta = getattr(match, f"{actor}_arrival_eta")
    updated = getattr(match, f"{actor}_arrival_updated_at")
    signal = getattr(match, f"{actor}_coordination_signal")
    status = getattr(match, f"{actor}_arrival_status")
    eta_display = timezone.localtime(eta).strftime("%b %d, %H:%M") if eta else ""
    updated_display = timezone.localtime(updated).strftime("%b %d, %H:%M") if updated else ""
    expired = bool(eta and eta <= now)
    if status == Match.ArrivalStatus.ARRIVED:
        label = dict(Match.CoordinationSignal.choices).get(signal, "Marked arrived")
    elif status == Match.ArrivalStatus.DELAYED:
        expired_label = "Arrival estimate has passed. Update your estimate." if viewer else "Arrival estimate has passed. Awaiting a fresh update."
        label = (expired_label if expired else f"Expected around {eta_display}") if eta else "Delay reported; arrival estimate not recorded."
    else:
        label = "Not marked arrived"
    if historical and status != Match.ArrivalStatus.PENDING:
        label = f"Last report: {dict(Match.CoordinationSignal.choices).get(signal, 'marked arrived')}" if status == Match.ArrivalStatus.ARRIVED else (
            f"Last report: arrival estimate {eta_display}" if eta else "Last report: delay, without a recorded estimate."
        )
    return {"arrival_eta": eta.isoformat() if eta else None, "arrival_eta_display": eta_display,
            "arrival_updated_at": updated.isoformat() if updated else None, "arrival_updated_at_display": updated_display,
            "delay_expired": expired, "coordination_signal": signal, "arrival_label": label}


def initialize_plan_locked(match, legacy=False):
    """Snapshot once; callers creating/altering a match already hold its lock."""
    if match.plan_meeting_at is not None:
        return match
    match.meeting_point = match.post.location.name
    match.plan_meeting_at = match.post.start_time
    match.plan_expected_end_at = match.post.expected_end_time
    if not legacy and match.plan_expected_end_at is None:
        match.plan_expected_end_at = match.post.start_time + timedelta(hours=1)
    match.plan_legacy = legacy
    match.poster_agreed_revision = match.plan_revision if match.poster_agreed else None
    match.swiper_agreed_revision = match.plan_revision if match.swiper_agreed else None
    match.save(update_fields=["meeting_point", "plan_meeting_at", "plan_expected_end_at", "plan_legacy", "poster_agreed_revision", "swiper_agreed_revision"])
    return match


def _effective_times(match):
    meeting_at = match.plan_meeting_at or match.post.start_time
    end_at = match.plan_expected_end_at or match.post.expected_end_time or meeting_at + timedelta(hours=1)
    return meeting_at, end_at


def _can_reopen(match, user, now):
    return bool(user.pk == match.poster_id and match.meetup_cancelled_at
                and match.post.status == ActivityPost.Status.PAUSED
                and match.post.expire_time > now and match.post.start_time > now
                and not holding_match_count(match.post)
                and not identities_retired([match.poster_id]))


def effective_outcome(match, user_id):
    mine = "poster" if user_id == match.poster_id else "swiper"
    recorded = getattr(match, f"{mine}_meetup_outcome")
    if recorded:
        return recorded
    # Existing outcome evidence remains authoritative across the migration.
    # Read it without inventing a new field value, timestamp, or analytics row.
    if ProductEvent.objects.filter(name=ProductEvent.Name.MEETUP_CONFIRMED, match=match, user_id=user_id).exists():
        return "met"
    return ""


def conflicting_participants(match, now=None):
    """Accepted half-open windows conflict regardless of participant role."""
    now = now or timezone.now()
    meeting_at, end_at = _effective_times(match)
    if end_at <= now:
        return set()
    participants = match.participant_ids()
    others = Match.objects.filter(
        Q(poster_id__in=participants) | Q(swiper_id__in=participants),
        status=Match.Status.AGREED, meetup_cancelled_at__isnull=True,
    ).exclude(pk=match.pk).filter(
        Q(plan_meeting_at__lt=end_at) | Q(plan_meeting_at__isnull=True, post__start_time__lt=end_at)
    ).select_related("post")
    conflicts = set()
    for other in others:
        other_start, other_end = _effective_times(other)
        if other_end > now and meeting_at < other_end and other_start < end_at:
            conflicts.update(participants.intersection(other.participant_ids()))
    return conflicts


def plan_payload(match, user):
    if not match.is_participant(user):
        raise RequestError("Only participants can view this plan.", 403)
    now = timezone.now()
    meeting_at, end_at = _effective_times(match)
    poster = user.pk == match.poster_id
    mine, other = ("poster", "swiper") if poster else ("swiper", "poster")
    cancelled = bool(match.meetup_cancelled_at)
    agreed = match.status == Match.Status.AGREED
    active = agreed and not cancelled
    retired = identities_retired(match.participant_ids())
    can_chat = match.status == Match.Status.CHATTING and not retired and bool(match.chat_expires_at and match.chat_expires_at > now)
    near_meeting = meeting_at - timedelta(minutes=30) <= now <= end_at
    feedback_open = agreed and now <= end_at + timedelta(hours=24)
    can_met = feedback_open and meeting_at <= now
    can_not_met = feedback_open and (cancelled or end_at <= now)
    can_reopen = _can_reopen(match, user, now)
    my_outcome = effective_outcome(match, user.pk)
    their_outcome = effective_outcome(match, match.swiper_id if poster else match.poster_id)
    feedback_actor = not identities_retired([user.pk]) and not my_outcome
    conflicts = conflicting_participants(match, now) if match.status in Match.LIVE_STATUSES else set()
    window_finished = now >= end_at
    status = "cancelled" if cancelled else ("finished" if active and window_finished else "confirmed" if active else "draft")
    arrival_allowed = active and near_meeting and not window_finished and not retired
    mine_arrival = _arrival_details(match, mine, now, status in {"cancelled", "finished"}, viewer=True)
    other_arrival = _arrival_details(match, other, now, status in {"cancelled", "finished"})

    def iso(value):
        return value.isoformat() if value else None

    def local(value, display=False):
        return timezone.localtime(value).strftime("%b %d, %H:%M" if display else "%Y-%m-%dT%H:%M") if value else ""

    expected_end = match.plan_expected_end_at or match.post.expected_end_time
    flags = {
        "can_edit": can_chat and not window_finished,
        "can_confirm": can_chat and not window_finished and not conflicts and getattr(match, f"{mine}_agreed_revision") != match.plan_revision,
        "can_arrive": arrival_allowed,
        "can_delay": arrival_allowed,
        "can_delay_5": arrival_allowed and now + timedelta(minutes=5) < end_at,
        "can_delay_10": arrival_allowed and now + timedelta(minutes=10) < end_at,
        "can_coordinate": arrival_allowed,
        "can_cancel": active and not window_finished and not retired,
        "can_met": can_met and feedback_actor,
        "can_not_met": can_not_met and feedback_actor,
        "can_outcome": (can_met or can_not_met) and feedback_actor,
        "can_reopen_card": can_reopen,
    }
    action_flags = {"update_plan": "can_edit", "confirm_plan": "can_confirm", "arrived": "can_arrive", "delayed": "can_delay", "coordination": "can_coordinate", "cancel_meetup": "can_cancel", "outcome": "can_outcome", "reopen_card": "can_reopen_card"}
    return {
        "revision": match.plan_revision,
        "meeting_point": match.meeting_point or match.post.location.name,
        "location_name": match.post.location.name,
        "meeting_at": iso(meeting_at), "expected_end_at": iso(expected_end),
        "meeting_at_input": local(meeting_at), "expected_end_at_input": local(expected_end),
        "meeting_at_display": local(meeting_at, True), "expected_end_at_display": local(expected_end, True),
        "viewer_confirmed": bool(getattr(match, f"{mine}_agreed") and (getattr(match, f"{mine}_agreed_revision") == match.plan_revision or (match.plan_legacy and match.plan_revision == 1))),
        "other_confirmed": bool(getattr(match, f"{other}_agreed") and (getattr(match, f"{other}_agreed_revision") == match.plan_revision or (match.plan_legacy and match.plan_revision == 1))),
        "legacy": match.plan_legacy, "meetup_status": status, "window_finished": window_finished,
        "viewer_status": getattr(match, f"{mine}_arrival_status"), "other_status": getattr(match, f"{other}_arrival_status"),
        "viewer_delay_minutes": getattr(match, f"{mine}_delay_minutes"), "other_delay_minutes": getattr(match, f"{other}_delay_minutes"),
        **{f"viewer_{key}": value for key, value in mine_arrival.items()},
        **{f"other_{key}": value for key, value in other_arrival.items()},
        "viewer_outcome": my_outcome, "other_outcome": their_outcome,
        "viewer_outcome_reason": getattr(match, f"{mine}_outcome_reason"), "other_outcome_reason": getattr(match, f"{other}_outcome_reason"),
        "is_publisher": poster,
        "schedule_conflict": bool(conflicts),
        "viewer_schedule_conflict": user.pk in conflicts,
        "other_schedule_conflict": (match.swiper_id if poster else match.poster_id) in conflicts,
        "earliest_meeting_at_input": local(match.post.start_time - timedelta(minutes=15)),
        "latest_end_at_input": local(match.post.expected_end_time or match.post.start_time + timedelta(hours=1)),
        "feedback_deadline": iso(end_at + timedelta(hours=24)),
        "feedback_deadline_display": local(end_at + timedelta(hours=24), True),
        "feedback_deadline_input": local(end_at + timedelta(hours=24)),
        "action_ready": "cancelled" if cancelled else "chatting" if can_chat else "near_meeting" if active and near_meeting else "after_meeting" if (can_met or can_not_met) else "waiting" if match.status == Match.Status.WAITING else "not_available_yet",
        **flags, "allowed_actions": [action for action, flag in action_flags.items() if flags[flag]],
    }


def _parse_time(value, label):
    if isinstance(value, str):
        try:
            value = parse_datetime(value)
        except ValueError:
            value = None
    if not isinstance(value, datetime):
        raise RequestError(f"Choose a valid {label}.")
    return timezone.make_aware(value, timezone.get_current_timezone()) if timezone.is_naive(value) else value


def _revision(value):
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise RequestError("This plan changed. Refresh before submitting again.", 409)
    if result < 1:
        raise RequestError("This plan changed. Refresh before submitting again.", 409)
    return result


def _delay_eta(match, minutes, now):
    if not minutes:
        return None
    eta = now + timedelta(minutes=minutes)
    if eta >= _effective_times(match)[1]:
        raise RequestError("That estimate is too late for the agreed meeting window. Cancel the meetup if you cannot arrive before its end.", 409)
    return eta


def _check_current(match, user, action, revision, *, outcome=None):
    if not match.is_participant(user):
        raise RequestError("Only participants can update this plan.", 403)
    if action == "reopen_card" and user.pk != match.poster_id:
        raise RequestError("Only the publisher can continue recruiting.", 403)
    if identities_retired([user.pk]) or (action not in {"outcome", "reopen_card"} and identities_retired(match.participant_ids())):
        raise RequestError("This identity has been reset. This plan cannot be changed.", 409)
    if match.plan_revision != revision:
        raise RequestError("The plan changed. Review the latest point and time before confirming.", 409)
    if action == "confirm_plan" and match.status == Match.Status.CHATTING and _effective_times(match)[1] <= timezone.now():
        raise RequestError("This activity has ended. Its meeting plan can no longer be confirmed.", 409)
    plan = plan_payload(match, user)
    allowed = plan["allowed_actions"]
    if action == "confirm_plan" and match.status == Match.Status.CHATTING and plan["schedule_conflict"]:
        raise RequestError("This time overlaps another confirmed meetup. Choose a different time within this activity's window.", 409)
    if action == "outcome" and not plan["can_met" if outcome == "met" else "can_not_met"]:
        if plan["viewer_outcome"]:
            raise RequestError("Your meetup feedback is already recorded.", 409)
        raise RequestError("You can report not met after the meeting window ends or the meetup is cancelled." if outcome == "not_met" else "Meeting feedback is not available at this time.", 409)
    # A fresh UUID confirming one's already-accepted current plan is harmless.
    already_confirmed = action == "confirm_plan" and match.status in {Match.Status.CHATTING, Match.Status.AGREED} and not match.meetup_cancelled_at and getattr(match, "poster_agreed_revision" if user.pk == match.poster_id else "swiper_agreed_revision") == revision
    if action not in allowed and not already_confirmed:
        raise RequestError("This action is no longer available. Review the current plan.", 409)


def _validate_plan(match, data):
    # datetime-local controls operate at minute precision. Keep the original
    # seconds for an unchanged displayed minute rather than interpreting a
    # point-only edit as moving the meeting backwards into the past.
    for input_key, original in (("meeting_at", match.plan_meeting_at or match.post.start_time),
                                ("expected_end_at", match.plan_expected_end_at or match.post.expected_end_time)):
        value = data[input_key]
        if original and value.second == 0 and value.microsecond == 0 and value.replace(second=0, microsecond=0) == original.replace(second=0, microsecond=0):
            data[input_key] = original
    meeting_at, end_at = data["meeting_at"], data["expected_end_at"]
    latest_end = match.post.expected_end_time or match.post.start_time + timedelta(hours=1)
    earliest = match.post.start_time - timedelta(minutes=15)
    if meeting_at < earliest or meeting_at >= end_at or end_at > latest_end:
        raise RequestError("Keep the meeting and end time within this activity's original time window.")
    current_meeting, _ = _effective_times(match)
    if meeting_at != current_meeting and meeting_at <= timezone.now():
        raise RequestError("Choose a future meeting time.")
    if end_at <= timezone.now():
        raise RequestError("This activity's time window has ended.", 409)
    if not data["meeting_point"] or len(data["meeting_point"]) > 160:
        raise RequestError("Use a specific public meeting point of 1–160 characters.")


def confirm_plan_locked(match, user, revision):
    """Shared by versioned actions and the untouched original legacy path."""
    initialize_plan_locked(match)
    _check_current(match, user, "confirm_plan", revision)
    mine = "poster" if user.pk == match.poster_id else "swiper"
    if getattr(match, f"{mine}_agreed_revision") == revision:
        return
    setattr(match, f"{mine}_agreed", True)
    setattr(match, f"{mine}_agreed_revision", revision)
    fields = [f"{mine}_agreed", f"{mine}_agreed_revision"]
    both = match.poster_agreed_revision == revision and match.swiper_agreed_revision == revision
    if both:
        match.status = Match.Status.AGREED
        match.plan_confirmed_at = timezone.now()
        fields.extend(["status", "plan_confirmed_at"])
    match.save(update_fields=fields)
    log_event(ProductEvent.Name.AGREE_CLICKED, user=user, match=match, properties={"both_agreed": both, "plan_revision": revision})
    if both:
        log_event(ProductEvent.Name.BOTH_AGREED, match=match, properties={"plan_revision": revision})
        system_message_locked(match, "Both people confirmed this meeting point and time. Your meetup plan is ready.")


def cancel_meetup_locked(match, user, reason="cancelled"):
    """Retain the historical agreement, close current instructions, pause supply."""
    if match.status != Match.Status.AGREED or match.meetup_cancelled_at:
        return False
    match.meetup_cancelled_at = timezone.now()
    match.meetup_cancelled_by = user
    match.save(update_fields=["meetup_cancelled_at", "meetup_cancelled_by"])
    if match.post.status != ActivityPost.Status.CANCELLED:
        match.post.status = ActivityPost.Status.EXPIRED if match.post.expire_time <= timezone.now() else ActivityPost.Status.PAUSED
        match.post.save(update_fields=["status", "updated_at"])
    system_message_locked(match, "This meetup has been cancelled. Do not travel based on the earlier arrangement.")
    log_event(ProductEvent.Name.MEETUP_CANCELLED, user=user, match=match, properties={"reason": reason, "plan_revision": match.plan_revision})
    return True


def _reply(match, user, result, replayed):
    from plusone.services.lifecycle import phase_payload
    return {"ok": True, **phase_payload(match, user), "action_result": result, "replayed": replayed}


def perform_meetup_action(match_id, user, action, request_id, revision, *, meeting_point=None,
                         meeting_at=None, expected_end_at=None, delay_minutes=None, coordination_signal=None, outcome=None, outcome_reason=None,
                         before_moderation=None):
    if action not in ACTIONS:
        raise RequestError("Choose a valid meetup action.")
    key, revision = request_uuid(request_id), _revision(revision)
    data = {}
    if action == "update_plan":
        data = {"meeting_point": str(meeting_point or "").strip(), "meeting_at": _parse_time(meeting_at, "meeting time"), "expected_end_at": _parse_time(expected_end_at, "end time")}
    elif action == "delayed":
        try:
            delay_minutes = int(delay_minutes)
        except (ValueError, TypeError):
            raise RequestError("Choose five or ten minutes, or clear the delay.")
        if delay_minutes not in {0, 5, 10}:
            raise RequestError("Choose five or ten minutes, or clear the delay.")
        data = {"delay_minutes": delay_minutes}
    elif action == "coordination":
        if coordination_signal not in {*Match.CoordinationSignal.values, "clear"}:
            raise RequestError("Choose one of the available arrival signals.")
        data = {"coordination_signal": coordination_signal}
    elif action == "outcome":
        if outcome not in {"met", "not_met"}:
            raise RequestError("Choose whether you met.")
        reason = str(outcome_reason or "") if outcome == "not_met" else ""
        if outcome == "not_met" and reason not in OUTCOME_REASONS:
            raise RequestError("Choose why the meetup did not happen.")
        data = {"outcome": outcome, "outcome_reason": reason}
    digest = fingerprint({"action": action, "revision": revision, **data})

    # Authenticate, replay, and detect obsolete versions before any model call.
    with locked_match(match_id) as current:
        if not current.is_participant(user):
            raise RequestError("Only participants can update this plan.", 403)
        existing = check_replay(MeetupAction.objects.filter(match=current, actor=user, request_id=key).first(), digest)
        if existing:
            expire_locked(current)
            return _reply(current, user, existing.result, True)
        expire_locked(current)
        initialize_plan_locked(current)
        denial = None
        try:
            _check_current(current, user, action, revision, outcome=data.get("outcome"))
            if action == "update_plan":
                _validate_plan(current, data)
            elif action == "delayed":
                _delay_eta(current, data["delay_minutes"], timezone.now())
        except RequestError as error:
            denial = error
    if denial:
        raise denial

    # Never wait for moderation while holding the pair/card locks.
    if action == "update_plan":
        if before_moderation is not None:
            before_moderation()
        # A refused/failed provider attempt can be retried with the same UUID,
        # but it still uses quota. Accepted actions replay before this point.
        consume_limit(user, "ai")
        moderation = moderate_text(user, data["meeting_point"])
        if moderation.get("service_unavailable"):
            raise RequestError("The safety check is temporarily unavailable. Your plan was not changed.", 503)
        if moderation.get("flagged"):
            raise RequestError("Choose an appropriate public meeting point.")

    with locked_match(match_id) as current:
        existing = check_replay(MeetupAction.objects.filter(match=current, actor=user, request_id=key).first(), digest)
        if existing:
            expire_locked(current)
            return _reply(current, user, existing.result, True)
        expire_locked(current)
        denial = None
        try:
            _check_current(current, user, action, revision, outcome=data.get("outcome"))
            arrival_now = timezone.now()
            if action == "delayed":
                arrival_eta = _delay_eta(current, data["delay_minutes"], arrival_now)
        except RequestError as error:
            denial = error
        if denial is None:
            mine = "poster" if user.pk == current.poster_id else "swiper"
            if action == "update_plan":
                _validate_plan(current, data)
                changed = (current.meeting_point != data["meeting_point"] or current.plan_meeting_at != data["meeting_at"] or current.plan_expected_end_at != data["expected_end_at"])
                if changed:
                    current.meeting_point = data["meeting_point"]
                    current.plan_meeting_at = data["meeting_at"]
                    current.plan_expected_end_at = data["expected_end_at"]
                    current.plan_revision += 1
                    current.plan_legacy = False
                    current.poster_agreed = current.swiper_agreed = False
                    current.poster_agreed_revision = current.swiper_agreed_revision = None
                    current.save(update_fields=["meeting_point", "plan_meeting_at", "plan_expected_end_at", "plan_revision", "plan_legacy", "poster_agreed", "swiper_agreed", "poster_agreed_revision", "swiper_agreed_revision"])
                    system_message_locked(current, "The meeting point or time changed. Both people need to confirm the updated plan.")
                    log_event(ProductEvent.Name.PLAN_UPDATED, user=user, match=current, properties={"plan_revision": current.plan_revision})
            elif action == "confirm_plan":
                confirm_plan_locked(current, user, revision)
            elif action in {"arrived", "delayed", "coordination"}:
                cleared = action == "delayed" and not data["delay_minutes"] or action == "coordination" and data["coordination_signal"] == "clear"
                status = Match.ArrivalStatus.PENDING if cleared else Match.ArrivalStatus.DELAYED if action == "delayed" else Match.ArrivalStatus.ARRIVED
                signal = Match.CoordinationSignal.AT_POINT if action == "arrived" else data.get("coordination_signal", "")
                setattr(current, f"{mine}_arrival_status", status)
                setattr(current, f"{mine}_delay_minutes", data.get("delay_minutes", 0))
                setattr(current, f"{mine}_arrival_eta", arrival_eta if action == "delayed" else None)
                setattr(current, f"{mine}_arrival_updated_at", None if cleared else arrival_now)
                setattr(current, f"{mine}_coordination_signal", "" if cleared else signal)
                current.save(update_fields=[f"{mine}_{field}" for field in ("arrival_status", "delay_minutes", "arrival_eta", "arrival_updated_at", "coordination_signal")])
                log_event(ProductEvent.Name.MEETUP_STATUS_UPDATED, user=user, match=current, properties={
                    "status": status, "delay_minutes": getattr(current, f"{mine}_delay_minutes"),
                    "coordination_signal": getattr(current, f"{mine}_coordination_signal"),
                    "arrival_eta": getattr(current, f"{mine}_arrival_eta").isoformat() if getattr(current, f"{mine}_arrival_eta") else None,
                })
            elif action == "cancel_meetup":
                cancel_meetup_locked(current, user)
            elif action == "outcome":
                setattr(current, f"{mine}_meetup_outcome", data["outcome"])
                setattr(current, f"{mine}_outcome_reason", data["outcome_reason"])
                current.save(update_fields=[f"{mine}_meetup_outcome", f"{mine}_outcome_reason"])
                log_event(ProductEvent.Name.MEETUP_OUTCOME, user=user, match=current, properties=data)
                if data["outcome"] == "met":
                    log_event(ProductEvent.Name.MEETUP_CONFIRMED, user=user, match=current)
            elif action == "reopen_card":
                current.post.status = ActivityPost.Status.ACTIVE
                current.post.save(update_fields=["status", "updated_at"])
            result = {"action": action, "revision": current.plan_revision, "accepted": True}
            MeetupAction.objects.create(match=current, actor=user, request_id=key, request_fingerprint=digest, result=result)
            return _reply(current, user, result, False)
    raise denial
