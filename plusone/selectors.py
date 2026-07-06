from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import ActivityPost, CampusLocation, Match, Swipe
from .utils import positive_int


def discover_context_for_user(user, query_params, last_passed_post_id=None):
    # Keep Discover query construction here so views stay focused on request
    # routing and templates receive a stable context shape.
    activity_type = query_params.get("activity_type", "")
    location_id = positive_int(query_params.get("location"))
    time_window = query_params.get("time_window", "")
    matched_id = positive_int(query_params.get("matched"))
    swiped_ids = Swipe.objects.filter(user=user).values_list("post_id", flat=True)
    posts = (
        ActivityPost.objects.active()
        .exclude(id__in=swiped_ids)
        .select_related("location")
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
    open_matches = [match for match in matches if match.status == Match.Status.CHATTING]
    handoff_matches = [match for match in matches if match.status == Match.Status.AGREED]
    closed_matches = [match for match in matches if match.status in {Match.Status.DECLINED, Match.Status.EXPIRED}]

    return {
        "active_posts": active_posts,
        "active_posts_count": len(active_posts),
        "open_chats_count": len(open_matches),
        "handoff_count": len(handoff_matches),
        "open_matches": open_matches,
        "handoff_matches": handoff_matches,
        "closed_matches": closed_matches,
        "dashboard_state": dashboard_state(active_posts, open_matches, handoff_matches),
        "expired_posts": expired_posts,
        "cancelled_posts": cancelled_posts,
        "matches": matches,
    }


def dashboard_state(active_posts, open_matches, handoff_matches):
    if open_matches:
        match = open_matches[0]
        return {
            "tone": "urgent",
            "eyebrow": "Needs decision",
            "title": match.post.title,
            "body": f"{match.post.location.name} is waiting on a five-minute chat.",
            "deadline": match.chat_expires_at,
        }
    if active_posts:
        post = active_posts[0]
        return {
            "tone": "live",
            "eyebrow": "Live now",
            "title": post.title,
            "body": f"{post.spots_remaining} spot{'' if post.spots_remaining == 1 else 's'} left at {post.location.name}.",
            "deadline": post.expire_time,
        }
    if handoff_matches:
        match = handoff_matches[0]
        return {
            "tone": "handoff",
            "eyebrow": "Ready to meet",
            "title": match.post.title,
            "body": f"Both people agreed. Meet at {match.post.location.name}.",
            "deadline": None,
        }
    return {
        "tone": "empty",
        "eyebrow": "All clear",
        "title": "Nothing live right now.",
        "body": "Your live cards, decision chats, and meet handoffs will appear here.",
        "deadline": None,
    }
