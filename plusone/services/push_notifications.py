"""Opt-in Web Push with participant checks, short-lived updates and safe retries.

The hosted worker invokes delivery explicitly. Web views only save a browser's
subscription; keys, endpoints and provider errors never become public payloads.
"""

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from plusone.models import Match, PushDelivery, PushSubscription, UserProfile
from plusone.services.lifecycle import expire_locked, identities_retired, locked_match, lock_users
from plusone.services.meetups import _effective_times
from plusone.services.notifications import notification_rows
from plusone.services.requests import RequestError


PROVIDER_HOSTS = (
    "fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
    "*.notify.windows.com", "web.push.apple.com",
)
MAX_SUBSCRIPTIONS = 5
MAX_ATTEMPTS = 5
MAX_BATCH_SIZE = 200
FRESH_FOR = timedelta(minutes=10)
CLAIM_LEASE = timedelta(seconds=60)


def _matches_host(host, allowed):
    if allowed.startswith("*."):
        return host.endswith(allowed[1:]) and host != allowed[2:]
    return host == allowed


def _validated_endpoint(endpoint):
    if (not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 2048
            or any(ord(character) <= 32 for character in endpoint)):
        raise RequestError("A valid browser push endpoint is required.")
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and parsed.port in {None, 443}
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and bool(parsed.path))
    except ValueError:
        valid, host = False, ""
    configured = getattr(settings, "PLUSONE_WEB_PUSH_ALLOWED_HOSTS", PROVIDER_HOSTS)
    if isinstance(configured, str):
        configured = [item.strip().lower() for item in configured.split(",") if item.strip()]
    if (not valid or not any(_matches_host(host, known) for known in PROVIDER_HOSTS)
            or not any(_matches_host(host, str(allowed).lower()) for allowed in configured)):
        raise RequestError("This browser's push provider is not supported.")
    return endpoint


def _decode_key(value, expected_size):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("Invalid browser encryption key.")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid browser encryption key.") from exc
    if len(decoded) != expected_size:
        raise ValueError("Invalid browser encryption key.")
    return decoded


def _validated_browser_keys(subscription):
    keys = subscription.get("keys")
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        if not isinstance(keys, dict):
            raise ValueError
        point = _decode_key(keys.get("p256dh"), 65)
        _decode_key(keys.get("auth"), 16)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except (ValueError, TypeError):
        raise RequestError("Your browser's push encryption keys are invalid. Enable reminders again.")
    return keys["p256dh"], keys["auth"]


def push_status():
    """Expose only the public application key when the complete setup is valid."""
    public = getattr(settings, "PLUSONE_VAPID_PUBLIC_KEY", "")
    private = getattr(settings, "PLUSONE_VAPID_PRIVATE_KEY", "")
    subject = getattr(settings, "PLUSONE_VAPID_SUBJECT", "")
    ready = bool(getattr(settings, "PLUSONE_WEB_PUSH_ENABLED", False))
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from pywebpush import webpush  # noqa: F401: deployment needs the actual sender
        point = _decode_key(public, 65)
        scalar = _decode_key(private, 32)
        derived = ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        )
        ready = ready and hmac.compare_digest(point, derived)
        contact_url = urlsplit(subject)
        contact_host = contact_url.hostname or ""
        try:
            ipaddress.ip_address(contact_host)
            public_contact = False
        except ValueError:
            public_contact = "." in contact_host and not contact_host.endswith((".local", ".localhost", ".internal"))
        ready = ready and bool(
            re.fullmatch(r"mailto:[^\s@]+@[^\s@]+\.[^\s@]+", subject)
            or contact_url.scheme == "https" and public_contact and not contact_url.username and not contact_url.password
        )
    except (ImportError, ValueError, TypeError):
        ready = False
    return {"available": bool(ready), "public_key": public if ready else ""}


def subscribe(user, subscription):
    if not push_status()["available"]:
        raise RequestError("Background reminders are not configured on this deployment yet.", 503)
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        raise RequestError("Start your own session before enabling reminders.", 403)
    if not isinstance(subscription, dict):
        raise RequestError("Your browser subscription is invalid.")
    endpoint = _validated_endpoint(subscription.get("endpoint"))
    p256dh, auth = _validated_browser_keys(subscription)
    digest = hashlib.sha256(endpoint.encode()).hexdigest()
    previous_owner = PushSubscription.objects.filter(endpoint_digest=digest).values_list("user_id", flat=True).first()
    with transaction.atomic():
        lock_users({user.pk, previous_owner} - {None})
        if UserProfile.objects.filter(user=user, retired_at__isnull=False).exists():
            raise RequestError("This session ended. Refresh before enabling reminders.", 409)
        existing = PushSubscription.objects.select_for_update().filter(endpoint_digest=digest).first()
        if existing and existing.user_id != user.pk:
            can_rebind = (
                existing.user_id == previous_owner and not existing.is_active
                and UserProfile.objects.filter(user_id=existing.user_id, retired_at__isnull=False).exists()
                and hmac.compare_digest(existing.p256dh, p256dh) and hmac.compare_digest(existing.auth, auth)
            )
            if not can_rebind:
                raise RequestError("This browser subscription belongs to another session. Disable it before enabling reminders here.", 409)
            existing.push_deliveries.all().delete()
            existing.user = user
        if not existing or not existing.is_active:
            if PushSubscription.objects.filter(user=user, is_active=True).count() >= MAX_SUBSCRIPTIONS:
                raise RequestError("You have reached the reminder device limit. Disable an old subscription first.", 429)
        if existing is None:
            existing, _ = PushSubscription.objects.get_or_create(endpoint_digest=digest, defaults={
                "user": user, "endpoint": endpoint, "p256dh": p256dh, "auth": auth, "is_active": True,
            })
            existing = PushSubscription.objects.select_for_update().get(pk=existing.pk)
            if existing.user_id != user.pk:
                raise RequestError("This browser subscription belongs to another session.", 409)
        existing.endpoint, existing.p256dh, existing.auth = endpoint, p256dh, auth
        existing.is_active = True
        existing.save(update_fields=["user", "endpoint", "p256dh", "auth", "is_active", "updated_at"])
    return existing


def unsubscribe(user, endpoint):
    """A foreign or already-removed endpoint has the same harmless result."""
    if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 2048:
        raise RequestError("A browser subscription endpoint is required.")
    digest = hashlib.sha256(endpoint.encode()).hexdigest()
    return bool(PushSubscription.objects.filter(user=user, endpoint_digest=digest).update(is_active=False, updated_at=timezone.now()))


def _eligible_rows(user, now, *, refresh=False):
    if UserProfile.objects.filter(user=user, retired_at__isnull=False).exists():
        return []
    if refresh:
        ids = list(Match.objects.filter(
            Q(poster=user) | Q(swiper=user), status__in=Match.HOLDING_STATUSES,
        ).order_by("-created_at").values_list("pk", flat=True)[:100])
        for match_id in ids:
            with locked_match(match_id) as current:
                expire_locked(current, now)
    rows = []
    for row in notification_rows(user):
        match_url = re.fullmatch(r"/chat/([1-9]\d*)/", str(row.get("url", "")))
        key = row.get("id")
        instant = parse_datetime(str(row.get("created_at", "")))
        if (not match_url or not isinstance(key, str) or not 1 <= len(key) <= 120
                or row.get("is_read") or instant is None or timezone.is_naive(instant) or instant > now):
            continue
        match = Match.objects.filter(Q(poster=user) | Q(swiper=user), pk=int(match_url.group(1))).select_related("post").first()
        if match is None:
            continue
        kind = key.split(":", 1)[0]
        from plusone.services.safety import blocked_between
        if kind != "event" and (blocked_between(match.poster_id, match.swiper_id) or identities_retired(match.participant_ids())):
            continue
        if kind == "waiting":
            deadline = min(match.waiting_expires_at, match.post.matching_deadline) if match.waiting_expires_at else match.post.matching_deadline
            if match.status != Match.Status.WAITING or deadline <= now:
                continue
        elif kind == "chatting":
            if match.status != Match.Status.CHATTING or not match.chat_expires_at or match.chat_expires_at <= now:
                continue
        elif kind in {"plan", "reminder"}:
            meeting, end = _effective_times(match)
            if match.status != Match.Status.AGREED or match.meetup_cancelled_at or end <= now:
                continue
            if kind == "reminder" and not meeting - timedelta(minutes=30) <= now <= meeting + timedelta(minutes=15):
                continue
        elif kind != "event":
            continue
        if instant < now - FRESH_FOR and kind != "reminder":
            continue
        rows.append({
            "notification_id": key, "tag": f"plusone:{key}", "title": str(row.get("title", "Plus One"))[:120],
            "body": str(row.get("body", "Open My Plus Ones to review your update."))[:240], "url": row["url"],
        })
    return rows


def _claim(subscription_id, key, now, *, expected_user_id=None):
    with transaction.atomic():
        subscriptions = PushSubscription.objects.select_for_update().filter(pk=subscription_id, is_active=True)
        if expected_user_id is not None:
            subscriptions = subscriptions.filter(user_id=expected_user_id)
        subscription = subscriptions.first()
        if subscription is None or UserProfile.objects.filter(user_id=subscription.user_id, retired_at__isnull=False).exists():
            return None
        delivery, _ = PushDelivery.objects.get_or_create(subscription=subscription, notification_key=key)
        delivery = PushDelivery.objects.select_for_update().get(pk=delivery.pk)
        if (delivery.delivered_at or delivery.attempts >= MAX_ATTEMPTS
                or delivery.next_attempt_at and delivery.next_attempt_at > now):
            return None
        delivery.attempts += 1
        delivery.next_attempt_at = now + CLAIM_LEASE
        delivery.save(update_fields=["attempts", "next_attempt_at"])
        return subscription, delivery


def _provider_send(subscription, payload):
    import requests
    from pywebpush import webpush

    class PushSession(requests.Session):
        def request(self, method, url, **kwargs):
            _validated_endpoint(url)
            kwargs["allow_redirects"] = False
            return super().request(method, url, **kwargs)

    _validated_endpoint(subscription.endpoint)
    session = PushSession()
    session.trust_env = False
    try:
        return webpush(
            subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
            data=json.dumps(payload, separators=(",", ":")),
            vapid_private_key=settings.PLUSONE_VAPID_PRIVATE_KEY, vapid_claims={"sub": settings.PLUSONE_VAPID_SUBJECT},
            ttl=300, timeout=10, requests_session=session,
        )
    finally:
        session.close()


def run_push_updates(*, send=False, batch_size=100, sender=None):
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}.")
    report = {"mode": "send" if send else "dry_run", "available": push_status()["available"],
              "subscriptions_scanned": 0, "eligible": 0, "delivered": 0, "failed": 0, "expired": 0}
    if not report["available"]:
        return report
    now = timezone.now()
    subscriptions = PushSubscription.objects.filter(is_active=True).exclude(
        user__userprofile__retired_at__isnull=False,
    ).select_related("user").order_by("updated_at", "pk")[:batch_size]
    sender = sender or _provider_send
    for subscription in subscriptions:
        report["subscriptions_scanned"] += 1
        if send:
            # Rotate a bounded scan fairly, including devices with no new rows.
            PushSubscription.objects.filter(pk=subscription.pk).update(updated_at=now)
        for payload in _eligible_rows(subscription.user, now, refresh=send):
            prior = PushDelivery.objects.filter(subscription=subscription, notification_key=payload["notification_id"]).first()
            if prior and (prior.delivered_at or prior.attempts >= MAX_ATTEMPTS or prior.next_attempt_at and prior.next_attempt_at > now):
                continue
            report["eligible"] += 1
            if send:
                original_owner = subscription.user_id
                claimed = _claim(subscription.pk, payload["notification_id"], now, expected_user_id=original_owner)
                if claimed:
                    current, delivery = claimed
                    if (current.user_id != original_owner
                            or not PushSubscription.objects.filter(pk=current.pk, user_id=original_owner, is_active=True).exists()
                            or UserProfile.objects.filter(user_id=original_owner, retired_at__isnull=False).exists()):
                        continue
                    error_code, status = "", 0
                    try:
                        response = sender(current, payload)
                        status = getattr(response, "status_code", 201)
                        if not 200 <= status <= 202:
                            error_code = f"provider_{status}"
                    except Exception as exc:
                        response = getattr(exc, "response", None)
                        status = getattr(response, "status_code", 0) if response is not None else 0
                        error_code = f"provider_{status}" if status else "network_error"
                    finished_at = timezone.now()
                    if not error_code:
                        PushDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts).update(
                            delivered_at=finished_at, next_attempt_at=None, last_error_code="",
                        )
                        report["delivered"] += 1
                    else:
                        PushDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts).update(
                            next_attempt_at=finished_at + timedelta(seconds=min(3600, 30 * 2 ** delivery.attempts)),
                            last_error_code=error_code,
                        )
                        if status in {404, 410}:
                            PushSubscription.objects.filter(pk=current.pk).update(is_active=False, updated_at=finished_at)
                            report["expired"] += 1
                        report["failed"] += 1
            if report["eligible"] >= batch_size:
                return report
    return report
