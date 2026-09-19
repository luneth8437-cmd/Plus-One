from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from plusone.models import UserProfile


LAST_SEEN_WRITE_INTERVAL = timedelta(minutes=5)
ALWAYS_EXCLUDED_VIEW_NAMES = {
    "healthz",
    "readyz",
    "opener_click",
    "presence",
    "match_presence",
    "chat_presence",
    "session_update",
    "session_updates",
}
ALWAYS_EXCLUDED_PATHS = {"/healthz/", "/readyz/"}


def _leaf_view_name(request):
    match = getattr(request, "resolver_match", None)
    view_name = getattr(match, "view_name", "") or ""
    return view_name.rsplit(":", 1)[-1]


def _is_background_request(request):
    if request.method in {"HEAD", "OPTIONS"}:
        return True

    path = request.path_info
    if path in ALWAYS_EXCLUDED_PATHS:
        return True

    view_name = _leaf_view_name(request)
    if view_name in ALWAYS_EXCLUDED_VIEW_NAMES:
        return True
    if view_name == "chat_messages" and request.method == "GET":
        return True

    # Keep new polling endpoints safe by default even before their final URL
    # names settle. The normal /session/ page is intentionally not excluded.
    normalized = path.rstrip("/")
    return (
        normalized.endswith("/presence")
        or normalized.endswith("/session-update")
        or normalized.endswith("/session-updates")
    )


def record_last_seen(request, *, now=None):
    """Record a genuine page/action after its view has finished.

    The user row is the cleanup coordination lock. This function deliberately
    does not create a user or profile; anonymous identity creation belongs to
    the view/session flow.
    """
    if _is_background_request(request):
        return False

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.pk is None:
        return False

    now = now or timezone.now()
    stale_before = now - LAST_SEEN_WRITE_INTERVAL
    User = get_user_model()
    with transaction.atomic():
        locked_user = User.objects.select_for_update().filter(pk=user.pk).first()
        if locked_user is None:
            return False
        updated = UserProfile.objects.filter(user_id=locked_user.pk, retired_at__isnull=True).filter(
            Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=stale_before)
        ).update(last_seen_at=now)
    return bool(updated)


class LastSeenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        record_last_seen(request)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Protect an existing identity before a slow page/AI action begins.
        # The after-response pass also covers identities created by the view.
        record_last_seen(request)
