from django.db.models import Count, Q
from django.utils import timezone

from plusone.models import ActivityPost, Match

ONE_TO_ONE_CAPACITY = 1


def holding_match_count(post):
    return Match.objects.filter(post=post, status__in=Match.HOLDING_STATUSES, meetup_cancelled_at__isnull=True).count()


def effective_capacity(post=None):
    return ONE_TO_ONE_CAPACITY


def sync_locked_post(post):
    if post.status == ActivityPost.Status.CANCELLED:
        return post.status
    if post.expire_time <= timezone.now():
        if post.status != ActivityPost.Status.EXPIRED:
            post.status = ActivityPost.Status.EXPIRED
            post.save(update_fields=["status", "updated_at"])
        return post.status
    if post.status == ActivityPost.Status.PAUSED:
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


def sync_post_status_for_capacity(post):
    # Reload under the same lock as matching; a stale object must never revive
    # a card that was cancelled in another request.
    from plusone.services.lifecycle import locked_post
    with locked_post(post.pk) as current:
        status = sync_locked_post(current)
    post.status = status
    return status


def reopen_posts_with_available_capacity():
    ids = list(
        ActivityPost.objects.filter(status=ActivityPost.Status.MATCHED, expire_time__gt=timezone.now())
        .annotate(holding_matches=Count("matches", filter=Q(matches__status__in=Match.HOLDING_STATUSES, matches__meetup_cancelled_at__isnull=True)))
        .filter(holding_matches__lt=effective_capacity())
        .values_list("pk", flat=True)
    )
    reopened = 0
    from plusone.services.lifecycle import locked_post
    for post_id in ids:
        with locked_post(post_id) as post:
            before = post.status
            sync_locked_post(post)
            reopened += before == ActivityPost.Status.MATCHED and post.status == ActivityPost.Status.ACTIVE
    return reopened
