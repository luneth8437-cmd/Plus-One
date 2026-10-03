"""Participant-only tasks and updates, with explicit attention lifetimes.

Reading a task acknowledges its notice; it does not complete the underlying
action. Historical updates remain available without becoming active tasks.
"""
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from plusone.models import Match, ProductEvent
from plusone.services.meetups import _effective_times, plan_payload


def notification_rows(user, *, seen_before=None, read_ids=()):
    cutoff = parse_datetime(seen_before) if isinstance(seen_before, str) else seen_before
    if cutoff and timezone.is_naive(cutoff):
        cutoff = timezone.make_aware(cutoff)
    now = timezone.now()
    participant = Q(poster=user) | Q(swiper=user)
    matches = list(Match.objects.filter(participant).select_related("post", "post__location").order_by("-created_at")[:100])
    rows = []
    read_ids = set(read_ids)

    def add(key, title, body, match, instant, severity="info", *, task_type="", is_current=True,
            expires_at=None, **details):
        is_read = key in read_ids or bool(cutoff and instant <= cutoff)
        deadline_label = {"waiting": "Join by", "chatting": "Decide by", "feedback": "Feedback closes",
                          "reminder": "Meeting time"}.get(task_type, "") if is_current else ""
        deadline_display = timezone.localtime(expires_at).strftime("%b %d, %H:%M") if deadline_label and expires_at else ""
        rows.append({"id": key, "title": title, "activity_title": match.post.title, "message": f"{title}: {match.post.title}", "body": body,
                     "url": reverse("chat", args=[match.pk]), "created_at": instant.isoformat(),
                     "is_read": is_read, "severity": severity, "match_id": match.pk,
                     "kind": "task" if task_type else "update", "task_type": task_type,
                     "is_current": is_current, "attention_required": is_current and (bool(task_type) or not is_read),
                     "deadline_label": deadline_label, "deadline_display": deadline_display,
                     "expires_at": expires_at.isoformat() if expires_at else None, **details})

    for match in matches:
        if match.status == Match.Status.WAITING:
            deadline = min(match.waiting_expires_at, match.post.matching_deadline) if match.waiting_expires_at else match.post.matching_deadline
            if now < deadline:
                add(f"waiting:{match.pk}", "A match is waiting", "Open the waiting room before its deadline.", match, match.created_at,
                    task_type="waiting", expires_at=deadline)
        elif match.status == Match.Status.CHATTING:
            if match.chat_expires_at and now < match.chat_expires_at:
                add(f"chatting:{match.pk}", "Your five-minute chat is open", "Its clock continues when you leave the page.", match, match.chat_started_at or match.created_at,
                    task_type="chatting", expires_at=match.chat_expires_at)
        elif match.status == Match.Status.AGREED:
            meeting, end = _effective_times(match)
            deadline = end + timedelta(hours=24)
            plan = None
            if now <= deadline and (match.meetup_cancelled_at or meeting <= now):
                plan = plan_payload(match, user)
            if plan and plan["can_outcome"]:
                if match.meetup_cancelled_at:
                    title = "Cancelled meetup: feedback is missing"
                    body = "Do not travel for this cancelled plan. Record your own meetup outcome before feedback closes."
                elif now < end:
                    title = "Meetup feedback is available"
                    body = "The planned meeting has started. You can report that you met; report no meetup after the window ends."
                else:
                    title = "Your meetup feedback is missing"
                    body = "The meeting window ended. Record your own outcome before feedback closes."
                add(f"feedback:{match.pk}:{match.plan_revision}", title, body, match, match.meetup_cancelled_at or meeting,
                    task_type="feedback", expires_at=deadline, can_met=plan["can_met"], can_not_met=plan["can_not_met"])
            elif not match.meetup_cancelled_at and now < end:
                reminder = meeting - timedelta(minutes=30)
                if reminder <= now < meeting:
                    add(f"reminder:{match.pk}:{match.plan_revision}", "Your meeting time is approaching", "Review the plan and any arrival or delay updates.", match, reminder,
                        task_type="reminder", expires_at=meeting)
                else:
                    add(f"plan:{match.pk}:{match.plan_revision}", "Your meeting plan is ready", "Revisit the confirmed point and time.", match, match.plan_confirmed_at or match.created_at,
                        expires_at=end)

    events = ProductEvent.objects.filter(
        Q(match__poster=user) | Q(match__swiper=user),
        name__in=[ProductEvent.Name.MEETUP_CANCELLED, ProductEvent.Name.MEETUP_STATUS_UPDATED, ProductEvent.Name.PLAN_UPDATED],
        created_at__gte=now - timedelta(days=30),
    ).exclude(user=user).select_related("match", "match__post").order_by("-created_at", "-pk")[:50]
    labels = {
        ProductEvent.Name.MEETUP_CANCELLED: ("Meetup cancelled", "Do not travel based on the previous arrangement.", "warning"),
        ProductEvent.Name.MEETUP_STATUS_UPDATED: ("Your Plus One updated their arrival", "Open your plan to see their self-reported arrival or delay.", "info"),
        ProductEvent.Name.PLAN_UPDATED: ("Meeting details changed", "Review the new details before confirming.", "info"),
    }
    latest_updates = set()
    for event in events:
        if event.match:
            # A sequence of arrival edits or plan revisions needs one current
            # update per type. Reports live in a separate participant-only list.
            group = (event.match_id, event.name)
            if group in latest_updates:
                continue
            latest_updates.add(group)
            match = event.match
            _, end = _effective_times(match)
            if event.name == ProductEvent.Name.MEETUP_CANCELLED:
                expiry = end + timedelta(hours=24)
                is_current = bool(match.meetup_cancelled_at and now <= expiry)
            elif event.name == ProductEvent.Name.MEETUP_STATUS_UPDATED:
                expiry = end
                is_current = match.status == Match.Status.AGREED and not match.meetup_cancelled_at and now < expiry
            else:
                expiry = match.chat_expires_at
                revision = (event.properties or {}).get("plan_revision")
                is_current = bool(match.status == Match.Status.CHATTING and expiry and now < expiry
                                  and (revision is None or str(revision) == str(match.plan_revision)))
            title, body, severity = labels[event.name]
            add(f"event:{event.pk}", title, body, match, event.created_at, severity,
                is_current=is_current, expires_at=expiry)
    # Recent history must not push an unfinished, expiring action off the page.
    tasks = [row for row in rows if row["kind"] == "task" and row["is_current"]]
    tasks.sort(key=lambda row: parse_datetime(row["expires_at"]))
    updates = [row for row in rows if row["kind"] != "task" or not row["is_current"]]
    updates.sort(key=lambda row: row["created_at"], reverse=True)
    return (tasks + updates)[:50]
