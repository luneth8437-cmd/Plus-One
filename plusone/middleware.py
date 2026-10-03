from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from plusone.models import UserProfile


class BrowserBudgetMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info in ALWAYS_EXCLUDED_PATHS or request.path_info.startswith("/static/"):
            return self.get_response(request)
        from django.conf import settings
        from plusone.services.browser_budget import COOKIE_AGE, prepare_budget
        prepare_budget(request)
        response = self.get_response(request)
        value = getattr(request, "browser_budget_cookie", None)
        if value:
            response.set_cookie(settings.PLUSONE_BROWSER_BUDGET_COOKIE, value, max_age=COOKIE_AGE,
                                httponly=True, secure=not settings.DEBUG, samesite="Lax")
        return response

    def process_exception(self, request, exception):
        from django.http import HttpResponse
        from plusone.services.requests import RequestError
        if not isinstance(exception, RequestError):
            return None
        response = HttpResponse(str(exception), status=exception.status, content_type="text/plain")
        if exception.retry_after:
            response["Retry-After"] = str(exception.retry_after)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != "POST":
            return None
        import json
        from django.http import HttpResponse, JsonResponse
        from plusone.context_processors import session_scope
        if request.content_type == "application/json":
            try:
                payload = json.loads(request.body)
            except (ValueError, UnicodeDecodeError):
                return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
            scope = payload.get("session_scope") if isinstance(payload, dict) else None
        else:
            scope = request.POST.get("session_scope")
        # Old API clients can omit this UI fence; current forms and JS always
        # include it, so a tab belonging to a retired identity cannot mutate
        # the new identity even with this browser's refreshed CSRF cookie.
        if scope is not None and scope != session_scope(request.user):
            message = "Your browser identity changed. Refresh this page before continuing."
            if "application/json" in request.headers.get("Accept", "") or request.content_type == "application/json":
                return JsonResponse({"ok": False, "error": message, "identity_changed": True}, status=409)
            return HttpResponse(message, status=409, content_type="text/plain")
        return None


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
    """Record a genuine page/action, before and after its view as needed.

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
