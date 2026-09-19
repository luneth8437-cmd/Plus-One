import hashlib
import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from plusone.models import RateLimitBucket
from plusone.services.lifecycle import lock_users


class RequestError(Exception):
    def __init__(self, message, status=400, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def request_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise RequestError("This page is out of date. Refresh before submitting again.")


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def check_replay(record, digest):
    if record and record.request_fingerprint != digest:
        raise RequestError("This request ID was already used for different content. Refresh to submit a new request.", 409)
    return record


LIMITS = {"ai": (6, 60), "publish": (10, 3600), "message": (30, 60)}


def consume_limit(user, scope, *, request_id=None, digest=None):
    limit, seconds = LIMITS[scope]
    now = timezone.now()
    start = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, tz=datetime_timezone.utc)
    expiry = start + timedelta(seconds=seconds)
    with transaction.atomic():
        lock_users([user.pk])
        bucket, _ = RateLimitBucket.objects.get_or_create(user=user, scope=scope, window_start=start, defaults={"expires_at": expiry})
        key = str(request_uuid(request_id)) if request_id else None
        if key and key in bucket.request_keys:
            if bucket.request_keys[key] != digest:
                raise RequestError("This request ID was already used for different content. Refresh to submit a new request.", 409)
            return
        if bucket.count >= limit:
            raise RequestError("Too many requests. Please wait before trying again.", 429, max(1, int((expiry - now).total_seconds()) + 1))
        bucket.count += 1
        if key:
            bucket.request_keys[key] = digest
        bucket.save(update_fields=["count", "request_keys"])
