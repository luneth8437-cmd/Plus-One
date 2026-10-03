from datetime import timedelta

from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from .models import ActivityPost, CampusLocation, Match, Swipe
from .utils import positive_int
from .services.safety import blocked_user_ids


def discover_context_for_user(user, query_params, last_passed_post_id=None):
    # Keep Discover query construction here so views stay focused on request
    # routing and templates receive a stable context shape.
    activity_type = query_params.get("activity_type", "")
    location_id = positive_int(query_params.get("location"))
    time_window = query_params.get("time_window", "")
    matched_id = positive_int(query_params.get("matched"))
    latest = Match.objects.filter(post_id=OuterRef("post_id"), swiper=user).order_by("-created_at", "-pk")
    retry_matches = Match.objects.filter(swiper=user, status=Match.Status.EXPIRED,
        close_reason=Match.CloseReason.TIMEOUT, waiting_expires_at__isnull=False, chat_started_at__isnull=True
    ).filter(pk=Subquery(latest.values("pk")[:1]))
    swiped_ids = Swipe.objects.filter(user=user).exclude(
        action=Swipe.Action.INTERESTED, post_id__in=retry_matches.values("post_id")
    ).values_list("post_id", flat=True)
    posts = (
        ActivityPost.objects.active()
        .exclude(id__in=swiped_ids)
        .exclude(user_id__in=blocked_user_ids(user))
        .select_related("location")
        .annotate(retry_match_id=Subquery(retry_matches.filter(post_id=OuterRef("pk")).values("pk")[:1]))
    )
    if activity_type:
        posts = posts.filter(activity_type=activity_type)
    if location_id:
        posts = posts.filter(location_id=location_id)
    if time_window == "now":
        posts = posts.filter(start_time__lte=timezone.now() + timedelta(hours=2))
    if time_window == "today":
        posts = posts.filter(start_time__date=timezone.localdate())

    matched_match = None
    if matched_id:
        matched_match = (
            Match.objects.filter(id=matched_id)
            .filter(Q(poster=user) | Q(swiper=user))
            .select_related("post", "post__location")
            .first()
        )

    undo_pass_post = None
    last_passed_post_id = positive_int(last_passed_post_id)
    if last_passed_post_id:
        undo_pass_post = (
            ActivityPost.objects.filter(id=last_passed_post_id, expire_time__gt=timezone.now())
            .filter(
                status__in=[ActivityPost.Status.ACTIVE, ActivityPost.Status.MATCHED],
                swipes__user=user,
                swipes__action=Swipe.Action.PASS,
            )
            .select_related("location")
            .first()
        )

    return {
        "posts": posts,
        "activity_types": ActivityPost.ActivityType.choices,
        "locations": CampusLocation.objects.all(),
        "selected_activity_type": activity_type,
        "selected_location": str(location_id or ""),
        "selected_time_window": time_window,
        "filters_active": bool(activity_type or location_id or time_window),
        "matched_match": matched_match,
        "undo_pass_post": undo_pass_post,
    }


def dashboard_context_for_user(user):
    # Dashboard renders several counters from the same match/post snapshots;
    # evaluating them once avoids repeated template-time database queries.
    now = timezone.now()
    active_posts = list(
        ActivityPost.objects.active()
        .filter(user=user)
        .select_related("location")
    )
    expired_posts = list(
        ActivityPost.objects.filter(user=user)
        .filter(Q(status=ActivityPost.Status.EXPIRED) | Q(expire_time__lte=now))
        .select_related("location")
    )
    cancelled_posts = list(
        ActivityPost.objects.filter(user=user, status=ActivityPost.Status.CANCELLED)
        .select_related("location")
    )
    matches = list(
        Match.objects.filter(Q(poster=user) | Q(swiper=user))
        .select_related("post", "poster", "swiper", "post__location")
    )
    from plusone.services.meetups import _effective_times, plan_payload
    for match in matches:
        meeting_at, end_at = _effective_times(match)
        match.dashboard_plan_point = match.meeting_point or match.post.location.name
        match.dashboard_meeting_at = meeting_at
        # The one-hour legacy fallback sets a boundary, not a recorded plan end.
        match.dashboard_expected_end_at = match.plan_expected_end_at or match.post.expected_end_time
        match.meetup_finished = bool(match.status == Match.Status.AGREED and not match.meetup_cancelled_at and end_at <= now)
        match.feedback_deadline = end_at + timedelta(hours=24)
        match.feedback_can_met = False
        match.feedback_can_not_met = False
        if (match.status == Match.Status.AGREED and now <= match.feedback_deadline
                and (match.meetup_cancelled_at or meeting_at <= now)):
            plan = plan_payload(match, user)
            match.feedback_can_met = plan["can_met"]
            match.feedback_can_not_met = plan["can_not_met"]
        match.feedback_pending = match.feedback_can_met or match.feedback_can_not_met
    open_matches = [match for match in matches if match.status in Match.LIVE_STATUSES]
    handoff_matches = [match for match in matches if match.status == Match.Status.AGREED and not match.meetup_cancelled_at and not match.meetup_finished]
    finished_matches = [match for match in matches if match.meetup_finished]
    closed_matches = [match for match in matches if match.status in {Match.Status.DECLINED, Match.Status.EXPIRED} or match.meetup_cancelled_at or match.meetup_finished]
    feedback_matches = sorted(
        (match for match in matches if match.feedback_pending),
        key=lambda match: match.feedback_deadline,
    )

    return {
        "active_posts": active_posts,
        "active_posts_count": len(active_posts),
        "open_chats_count": len(open_matches),
        "waiting_count": sum(match.status == Match.Status.WAITING for match in open_matches),
        "handoff_count": len(handoff_matches),
        "open_matches": open_matches,
        "handoff_matches": handoff_matches,
        "finished_matches": finished_matches,
        "closed_matches": closed_matches,
        "feedback_matches": feedback_matches,
        "feedback_count": len(feedback_matches),
        "dashboard_state": dashboard_state(active_posts, open_matches, handoff_matches, feedback_matches),
        "expired_posts": expired_posts,
        "cancelled_posts": cancelled_posts,
        "matches": matches,
    }


def dashboard_state(active_posts, open_matches, handoff_matches, feedback_matches=()):
    if open_matches:
        match = open_matches[0]
        return {
            "tone": "urgent",
            "eyebrow": "Needs decision",
            "title": match.post.title,
            "body": "Open the chat. Your five minutes begin when both people are recently online." if match.status == Match.Status.WAITING else f"{match.post.location.name} is waiting on a five-minute chat.",
            "deadline": match.phase_deadline,
        }
    if feedback_matches:
        match = feedback_matches[0]
        return {
            "tone": "feedback",
            "eyebrow": "Feedback needed",
            "title": match.post.title,
            "body": "This meetup was cancelled. Record whether you met before feedback closes."
                if match.meetup_cancelled_at else "Tell us whether you met. Your own feedback is still missing.",
            "deadline": match.feedback_deadline,
        }
    if active_posts:
        post = active_posts[0]
        return {
            "tone": "live",
            "eyebrow": "Live now",
            "title": post.title,
            "body": f"One-to-one plan at {post.location.name}.",
            "deadline": post.matching_deadline,
        }
    if handoff_matches:
        match = handoff_matches[0]
        return {
            "tone": "handoff",
            "eyebrow": "Ready to meet",
            "title": match.post.title,
            "body": f"Both people agreed. Meet at {match.meeting_point or match.post.location.name}.",
            "deadline": None,
        }
    return {
        "tone": "empty",
        "eyebrow": "All clear",
        "title": "Nothing live right now.",
        "body": "Your live cards, decision chats, and meet handoffs will appear here.",
        "deadline": None,
    }
