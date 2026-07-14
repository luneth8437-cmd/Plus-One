"""Server-side product event logging (implements docs/product_validation/04).

Design rules:

- Events are logged server-side at the moment the state actually changes, so
  the funnel cannot be skewed by client-side loss.
- Logging must never break the user action: failures are swallowed after a
  best-effort write (events are analytics, not business state).
- Privacy: no user-written chat text is ever stored. Opener adoption is
  measured by storing the AI-generated suggestion texts on the
  ``opener_suggested`` event and comparing them against outgoing messages at
  send time; only the *comparison result* (verbatim/edited/none) is stored on
  the message event.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from plusone.models import ProductEvent

logger = logging.getLogger(__name__)

# How long a suggestion batch stays attributable to an outgoing message.
OPENER_ATTRIBUTION_WINDOW_MINUTES = 30
# Prefix length used to detect an edited (rather than verbatim) suggestion.
EDIT_DETECTION_PREFIX = 25


def log_event(name, user=None, post=None, match=None, properties=None):
    try:
        return ProductEvent.objects.create(
            name=name,
            user=user if getattr(user, "pk", None) else None,
            post=post,
            match=match,
            properties=properties or {},
        )
    except Exception:  # pragma: no cover - analytics must never break the flow
        logger.exception("Failed to log product event %s", name)
        return None


def _normalize(text):
    return " ".join(str(text or "").lower().split())


def classify_opener_usage(match, user, message_text):
    """Return 'verbatim' / 'edited' / 'none' for an outgoing message.

    Compares against the most recent opener_suggested event this user got for
    this match inside the attribution window.
    """
    window_start = timezone.now() - timedelta(minutes=OPENER_ATTRIBUTION_WINDOW_MINUTES)
    event = (
        ProductEvent.objects.filter(
            name=ProductEvent.Name.OPENER_SUGGESTED,
            match=match,
            user=user,
            created_at__gte=window_start,
        )
        .order_by("-created_at")
        .first()
    )
    if not event:
        return "none"
    sent = _normalize(message_text)
    for suggested in event.properties.get("texts", []):
        norm = _normalize(suggested)
        if not norm:
            continue
        if sent == norm:
            return "verbatim"
        prefix = norm[:EDIT_DETECTION_PREFIX]
        if prefix and (sent.startswith(prefix) or norm.startswith(sent[:EDIT_DETECTION_PREFIX])):
            return "edited"
    return "none"


def log_message_events(match, user, message_text):
    """Log message_sent plus derived first_message_sent / first_reply_received.

    Called after the ChatMessage row is created, so counts include the new
    message. Only comparison results are stored - never the message text.
    """
    user_messages = match.messages.filter(is_system=False)
    sender_count = user_messages.filter(sender=user).count()
    other_count = user_messages.exclude(sender=user).count()
    usage = classify_opener_usage(match, user, message_text)
    seconds_since_match = int((timezone.now() - match.created_at).total_seconds())

    props = {"opener_usage": usage, "seconds_since_match": seconds_since_match}
    log_event(ProductEvent.Name.MESSAGE_SENT, user=user, match=match, properties=props)
    if sender_count == 1 and other_count == 0:
        log_event(ProductEvent.Name.FIRST_MESSAGE_SENT, user=user, match=match, properties=props)
    if sender_count == 1 and other_count >= 1:
        log_event(ProductEvent.Name.FIRST_REPLY_RECEIVED, user=user, match=match, properties=props)
