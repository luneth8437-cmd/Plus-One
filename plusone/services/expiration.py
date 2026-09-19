from django.db import OperationalError
from django.utils import timezone
from django.db.models import Q

from plusone.models import ActivityPost, Match
from plusone.services.capacity import reopen_posts_with_available_capacity
from plusone.services.lifecycle import expire_locked, locked_match


def refresh_expired_records():
    try:
        now = timezone.now()
        expired_posts = ActivityPost.objects.filter(
            status=ActivityPost.Status.ACTIVE,
            expire_time__lte=now,
        ).update(status=ActivityPost.Status.EXPIRED)
        ids = list(Match.objects.filter(
            Q(status=Match.Status.CHATTING, chat_expires_at__lte=now)
            | Q(status=Match.Status.WAITING, waiting_expires_at__lte=now)
            | Q(status=Match.Status.WAITING, post__expire_time__lte=now)
        ).values_list("pk", flat=True)[:200])
        expired_matches = 0
        for match_id in ids:
            with locked_match(match_id) as match:
                expired_matches += expire_locked(match, now)
        reopened_posts = reopen_posts_with_available_capacity()
        return {"posts": expired_posts, "matches": expired_matches, "reopened_posts": reopened_posts}
    except OperationalError as error:
        if "database is locked" not in str(error).lower() and "database table is locked" not in str(error).lower():
            raise
        return {"posts": 0, "matches": 0, "reopened_posts": 0, "deferred": True}
