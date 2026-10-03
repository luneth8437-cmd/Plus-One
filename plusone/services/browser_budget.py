"""A signed, random browser budget survives identity reset without fingerprinting."""
import secrets
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from plusone.context_processors import session_scope
from plusone.services.requests import RequestError, request_uuid

COOKIE_AGE = 86400
LIMITS = {"ai": (6, 60), "publish": (10, 3600), "message": (30, 60),
          "identity": (6, 3600), "reset": (5, 3600), "report": (10, 3600)}


def prepare_budget(request):
    if hasattr(request, "browser_budget_key"):
        return
    value = request.COOKIES.get(settings.PLUSONE_BROWSER_BUDGET_COOKIE)
    try:
        token = signing.loads(value or "", salt="plusone-usage", max_age=COOKIE_AGE)
        if not isinstance(token, str) or len(token) != 32:
            raise signing.BadSignature("Invalid usage token")
    except signing.BadSignature:
        token = secrets.token_hex(16)
        request.browser_budget_cookie = signing.dumps(token, salt="plusone-usage")
    request.browser_budget_key = salted_hmac("plusone-usage-budget", token, algorithm="sha256").hexdigest()


def consume_browser_limit(request, scope, *, request_id=None, digest=None):
    from plusone.models import BrowserBudgetBucket
    prepare_budget(request)
    limit, seconds = LIMITS[scope]
    now = timezone.now()
    start = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, tz=datetime_timezone.utc)
    expiry = start + timedelta(seconds=seconds)
    key = f"{session_scope(request.user)}:{request_uuid(request_id)}" if request_id else None
    with transaction.atomic():
        bucket, _ = BrowserBudgetBucket.objects.get_or_create(
            browser_key=request.browser_budget_key, scope=scope, window_start=start,
            defaults={"expires_at": expiry},
        )
        bucket = BrowserBudgetBucket.objects.select_for_update().get(pk=bucket.pk)
        if key and key in bucket.request_keys:
            if bucket.request_keys[key] != digest:
                raise RequestError("This request ID was already used for different content. Refresh before submitting again.", 409)
            return
        if bucket.count >= limit:
            raise RequestError("This browser has reached its temporary limit. Wait before trying again; starting a new identity does not reset it.",
                               429, max(1, int((expiry - now).total_seconds()) + 1))
        bucket.count += 1
        if key:
            bucket.request_keys[key] = digest
        bucket.save(update_fields=["count", "request_keys"])
