from django.db.models import Count, Q
from django.utils import timezone

from plusone.models import ActivityPost, Match

ONE_TO_ONE_CAPACITY = 1


def holding_match_count(post):
    return Match.objects.filter(post=post, status__in=Match.HOLDING_STATUSES).count()


def effective_capacity(post=None):
    return ONE_TO_ONE_CAPACITY


def sync_post_status_for_capacity(post):
    if post.status == ActivityPost.Status.CANCELLED:
        return post.status
    if post.expire_time <= timezone.now():
        if post.status != ActivityPost.Status.EXPIRED:
            post.status = ActivityPost.Status.EXPIRED
            post.save(update_fields=["status", "updated_at"])
        return post.status

    desired_status = (
        ActivityPost.Status.MATCHED
        if holding_match_count(post) >= effective_capacity(post)
        else ActivityPost.Status.ACTIVE
    )
    if post.status != desired_status:
        post.status = desired_status
        post.save(update_fields=["status", "updated_at"])
    return post.status


def reopen_posts_with_available_capacity():
    return (
        ActivityPost.objects.filter(status=ActivityPost.Status.MATCHED, expire_time__gt=timezone.now())
        .annotate(holding_matches=Count("matches", filter=Q(matches__status__in=Match.HOLDING_STATUSES)))
        .filter(holding_matches__lt=effective_capacity())
        .update(status=ActivityPost.Status.ACTIVE, updated_at=timezone.now())
    )
