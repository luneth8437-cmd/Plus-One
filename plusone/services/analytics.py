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
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from plusone.models import ActivityPost, ProductEvent

logger = logging.getLogger(__name__)

# How long a suggestion batch stays attributable to an outgoing message.
OPENER_ATTRIBUTION_WINDOW_MINUTES = 30
# Prefix length used to detect an edited (rather than verbatim) suggestion.
EDIT_DETECTION_PREFIX = 25


def log_event(name, user=None, post=None, match=None, properties=None):
    try:
        post = post or (match.post if match else None)
        properties = dict(properties or {})
        if match and name in {ProductEvent.Name.BOTH_AGREED, ProductEvent.Name.MEETUP_OUTCOME,
                              ProductEvent.Name.MEETUP_CONFIRMED, ProductEvent.Name.MEETUP_CANCELLED}:
            # Preserve only coarse role and timing evidence after business-row
            # cleanup, never the point, chat content, or personal details.
            from plusone.services.meetups import _effective_times
            _, end_at = _effective_times(match)
            properties["meeting_end_at"] = end_at.isoformat()
            if user and getattr(user, "pk", None) in (match.poster_id, match.swiper_id):
                properties["actor_role"] = "publisher" if user.pk == match.poster_id else "guest"
        values = dict(
            name=name,
            user=user if getattr(user, "pk", None) else None,
            post=post,
            match=match,
            properties=properties,
            post_reference=post.pk if post else None,
            match_reference=match.pk if match else None,
            post_created_at=post.created_at if post else None,
            match_created_at=match.created_at if match else None,
        )
        once_match = {ProductEvent.Name.MATCH_CREATED, ProductEvent.Name.CHAT_STARTED, ProductEvent.Name.BOTH_AGREED,
                      ProductEvent.Name.WAIT_CLOSED,
                      ProductEvent.Name.FIRST_MESSAGE_SENT, ProductEvent.Name.FIRST_REPLY_RECEIVED}
        once_user = {ProductEvent.Name.AGREE_CLICKED, ProductEvent.Name.MEETUP_CONFIRMED}
        key = None
        if name == ProductEvent.Name.PUBLISH_CARD and post:
            key = f"{name}:post:{post.pk}"
        elif name in once_match and match:
            key = f"{name}:match:{match.pk}"
        elif name in once_user and match and user:
            key = f"{name}:match:{match.pk}:user:{user.pk}"
        with transaction.atomic():
            if key:
                return ProductEvent.objects.get_or_create(event_key=key, defaults=values)[0]
            return ProductEvent.objects.create(**values)
    except Exception:  # pragma: no cover - analytics must never break the flow
        logger.exception("Failed to log product event %s", name)
        return None


def log_discovery_visit(user, posts, filters=None):
    """Count rendered supply, explicitly not physical viewport visibility."""
    posts = list(posts)
    filters = filters or {}
    activity_type = filters.get("activity_type", "")
    time_window = filters.get("time_window", "")
    props = {
        "rendered_card_count": len(posts),
        "other_card_count": sum(post.user_id != user.pk for post in posts),
        "filters_active": bool(any(filters.get(key) for key in ("activity_type", "location", "time_window"))),
        "activity_type": activity_type if activity_type in ActivityPost.ActivityType.values else "",
        "time_window": time_window if time_window in {"now", "today"} else "",
    }
    log_event(ProductEvent.Name.DISCOVER_VISITED, user=user, properties=props)
    if not posts:
        log_event(ProductEvent.Name.DISCOVER_EMPTY, user=user, properties=props)
    for post in posts:
        log_event(ProductEvent.Name.CARD_IMPRESSION, user=user, post=post, properties={
            "basis": "server_rendered", "own_card": post.user_id == user.pk,
            "activity_type": post.activity_type,
        })


def log_create_started(user):
    return log_event(ProductEvent.Name.CREATE_STARTED, user=user)


def log_interested_result(user, post, outcome):
    allowed = {"own_post", "inactive_post", "full_post", "invalid_action", "match_created", "match_exists", "try_again"}
    return log_event(ProductEvent.Name.INTERESTED_RESULT, user=user, post=post,
                     properties={"outcome": outcome if outcome in allowed else "unknown"})


def log_opener_suggestions(user, match, openers, *, metadata=None):
    from plusone.ai_services.opening_assistant import PROMPT_VERSION
    metadata = metadata or {}
    return log_event(ProductEvent.Name.OPENER_SUGGESTED, user=user, match=match, properties={
        "batch_id": str(uuid4()), "count": len(openers),
        "texts": [opener["text"] for opener in openers],
        "prompt_version": str(metadata.get("prompt_version") or PROMPT_VERSION)[:80],
        "model": str(metadata.get("model") or "unknown")[:80],
        "generation": str(metadata.get("generation") or metadata.get("strategy") or "unknown")[:80],
    })


def log_opener_click(user, match, index, *, batch_id=None):
    """Accept a known batch or retain an explicitly unattributed legacy click."""
    if not isinstance(index, int) or index < 0:
        return None
    if not batch_id:
        return log_event(ProductEvent.Name.OPENER_CLICKED, user=user, match=match,
                         properties={"index": index, "attribution": "legacy_unknown"})
    try:
        batch_id = str(UUID(str(batch_id)))
        batch = ProductEvent.objects.filter(name=ProductEvent.Name.OPENER_SUGGESTED,
                                            match=match, user=user).filter(properties__batch_id=batch_id).first()
        if not batch or index >= batch.properties.get("count", 0):
            return None
        return log_event(ProductEvent.Name.OPENER_CLICKED, user=user, match=match, properties={
            "index": index, "batch_id": batch_id,
            "prompt_version": batch.properties.get("prompt_version", "unknown"),
        })
    except (ValueError, TypeError):
        return None
    except Exception:
        logger.exception("Failed to attribute an opener click")
        return None


def _normalize(text):
    return " ".join(str(text or "").lower().split())


def _opener_attribution(match, user, message_text):
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
        return "none", None
    sent = _normalize(message_text)
    for suggested in event.properties.get("texts", []):
        norm = _normalize(suggested)
        if not norm:
            continue
        if sent == norm:
            return "verbatim", event
        prefix = norm[:EDIT_DETECTION_PREFIX]
        if min(len(sent), len(norm)) >= EDIT_DETECTION_PREFIX and (sent.startswith(prefix) or norm.startswith(sent[:EDIT_DETECTION_PREFIX])):
            return "edited", event
    return "none", None


def classify_opener_usage(match, user, message_text):
    return _opener_attribution(match, user, message_text)[0]


def log_message_events(match, user, message_text):
    """Log message_sent plus derived first_message_sent / first_reply_received.

    Called after the ChatMessage row is created, so counts include the new
    message. Only comparison results are stored - never the message text.
    """
    user_messages = match.messages.filter(is_system=False)
    sender_count = user_messages.filter(sender=user).count()
    other_count = user_messages.exclude(sender=user).count()
    usage, opener_event = _opener_attribution(match, user, message_text)
    seconds_since_match = int((timezone.now() - match.created_at).total_seconds())

    props = {"opener_usage": usage, "seconds_since_match": seconds_since_match}
    if opener_event:
        props.update({key: opener_event.properties.get(key, "unknown")
                      for key in ("batch_id", "prompt_version", "model", "generation")})
    log_event(ProductEvent.Name.MESSAGE_SENT, user=user, match=match, properties=props)
    if sender_count == 1 and other_count == 0:
        log_event(ProductEvent.Name.FIRST_MESSAGE_SENT, user=user, match=match, properties=props)
    if sender_count == 1 and other_count >= 1:
        log_event(ProductEvent.Name.FIRST_REPLY_RECEIVED, user=user, match=match, properties=props)
