from django.db import OperationalError
from django.utils import timezone

from plusone.models import ActivityPost, Match
from plusone.services.capacity import reopen_posts_with_available_capacity


def refresh_expired_records():
    try:
        now = timezone.now()
        expired_posts = ActivityPost.objects.filter(
            status=ActivityPost.Status.ACTIVE,
            expire_time__lte=now,
        ).update(status=ActivityPost.Status.EXPIRED)
        expired_matches = Match.objects.filter(
            status=Match.Status.CHATTING,
            chat_expires_at__lte=now,
        ).update(status=Match.Status.EXPIRED)
        reopened_posts = reopen_posts_with_available_capacity() if expired_matches else 0
        return {"posts": expired_posts, "matches": expired_matches, "reopened_posts": reopened_posts}
    except OperationalError as error:
        if "database is locked" not in str(error).lower() and "database table is locked" not in str(error).lower():
            raise
        return {"posts": 0, "matches": 0, "reopened_posts": 0, "deferred": True}
