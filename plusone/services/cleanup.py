from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from plusone.models import ActivityPost, LLMLog, Match, ProductEvent


def stale_anonymous_users(days=7):
    cutoff = timezone.now() - timedelta(days=days)
    User = get_user_model()
    active_post = Q(
        activity_posts__status__in=[ActivityPost.Status.ACTIVE, ActivityPost.Status.MATCHED],
        activity_posts__expire_time__gt=timezone.now(),
    )
    active_posted_match = Q(
        posted_matches__status=Match.Status.CHATTING,
        posted_matches__chat_expires_at__gt=timezone.now(),
    )
    active_swiped_match = Q(
        swiped_matches__status=Match.Status.CHATTING,
        swiped_matches__chat_expires_at__gt=timezone.now(),
    )
    stale_identity = Q(last_login__lt=cutoff) | Q(last_login__isnull=True, date_joined__lt=cutoff)
    return (
        User.objects.filter(username__startswith="anon_")
        .filter(stale_identity)
        .exclude(active_post | active_posted_match | active_swiped_match)
        .distinct()
    )


def cleanup_anonymous_users(days=7, dry_run=True):
    users = stale_anonymous_users(days=days)
    count = users.count()
    if dry_run:
        return count
    users.delete()
    return count


def cleanup_stale_records(user_days=7, llm_log_days=30, event_days=90, dry_run=True):
    """Apply independent retention windows to identities, AI logs, and analytics."""
    users = stale_anonymous_users(days=user_days)
    llm_logs = LLMLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=llm_log_days))
    events = ProductEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=event_days))
    counts = {
        "users": users.count(),
        "llm_logs": llm_logs.count(),
        "events": events.count(),
    }
    if dry_run:
        return counts

    # Delete content-bearing logs before identities; both foreign keys use
    # SET_NULL, so deleting a user alone would leave the historical content.
    llm_logs.delete()
    events.delete()
    users.delete()
    return counts
