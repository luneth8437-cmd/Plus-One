from uuid import uuid4
from datetime import timezone as datetime_timezone
import json
from urllib.parse import urlencode, urlsplit, parse_qsl

from django.contrib import messages
from django.db import connection, DatabaseError
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse, FileResponse
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .ai import generate_openers, parse_activity_text, suggest_ambiguous_time_options
from .forms import ActivityAssistForm, ActivityPostForm, ChatMessageForm
from .models import ActivityPost, Match, ProductEvent, Swipe, ChatMessage, LLMLog, UserBlock
from .services.analytics import log_event
from .context_processors import session_scope
from .services.browser_budget import consume_browser_limit
from .presenters import chat_message_payload, post_edit_initial, post_form_preview, post_initial_from_ai
from .selectors import dashboard_context_for_user, discover_context_for_user
from .services.chat import close_match, create_chat_message, record_agreement, report_match, confirm_meetup
from .services.meetups import perform_meetup_action
from .services.lifecycle import locked_match, expire_locked, phase_payload, set_presence
from .services.requests import RequestError, consume_limit, request_uuid
from .services.expiration import refresh_expired_records
from .services.identity import ensure_anonymous_session, ensure_user_profile, reset_anonymous_identity_for_request
from .services.matching import SwipeOutcome, handle_swipe
from .services.posts import cancel_activity_post, moderate_activity_form, moderate_activity_text, save_activity_post_for_user, published_replay, publish_fingerprint
from .ai_services.validation import check_publish_date


def _queue_return(request, value=None):
    """Only retain supported Discover filters, never arbitrary redirect targets."""
    value = value or request.POST.get("return_to") or request.GET.get("return_to") or request.get_full_path()
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.path != reverse("discover"):
        return reverse("discover")
    query = dict(parse_qsl(parts.query))
    allowed = {key: query[key] for key in ("activity_type", "location", "time_window") if query.get(key)}
    return reverse("discover") + (f"?{urlencode(allowed)}" if allowed else "")


def _reviewed_fields(raw):
    fields = set(ActivityPostForm().fields) | {"raw_text"}
    if not raw:
        return {}
    if len(raw) > 16000:
        raise RequestError("This draft is too large. Review the fields before trying again.")
    try:
        values = json.loads(raw)
        if isinstance(values, list):
            values = dict(values)
        if not isinstance(values, dict):
            raise ValueError
        return {key: "" if value is None else str(value) for key, value in values.items() if key in fields and (value is None or isinstance(value, (str, int, float)))}
    except (ValueError, TypeError):
        raise RequestError("This draft could not be read. Your current card has not been published.")


def _create_review_store_key(user):
    return f"plusone_create_reviews:{session_scope(user)}"


def _create_draft_revision(value):
    try:
        return max(0, min(int(value), 2**31 - 1))
    except (ValueError, TypeError):
        return 0


def _save_create_review(request, snapshot):
    """Keep bounded, identity-owned review pages behind safe GET URLs."""
    key = _create_review_store_key(request.user)
    reviews = dict(request.session.get(key, {}))
    token = str(uuid4())
    reviews[token] = json.loads(json.dumps(snapshot, default=str))
    request.session[key] = dict(list(reviews.items())[-5:])
    return redirect(f"{reverse('create_post')}?{urlencode({'draft': token})}")


def _safe_next_redirect(request, target, fallback):
    # Login/profile pages accept a next= target, but only same-host redirects
    # are allowed to avoid open redirect behavior.
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect(fallback)


def healthz(request):
    """Lightweight liveness endpoint with no session or database access."""
    return HttpResponse("ok", content_type="text/plain")


def readyz(request):
    """Readiness deliberately checks only the database, never AI or sessions."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    return HttpResponse("ready", content_type="text/plain")


@require_GET
def privacy(request):
    ensure_anonymous_session(request)
    return render(request, "plusone/privacy.html")


@require_GET
def support(request):
    ensure_anonymous_session(request)
    return render(request, "plusone/support.html")


def _notifications_for(request):
    from .services.notifications import notification_rows
    _refresh_user_matches(request.user)
    return notification_rows(request.user, seen_before=request.session.get("updates_read_before"),
                             read_ids=request.session.get("updates_read_ids", []))


@login_required
def notifications(request):
    from .services.safety import own_report_statuses, unblock_user
    if request.method == "POST":
        action = request.POST.get("action")
        rows = _notifications_for(request)
        if action == "mark_all_read":
            request.session["updates_read_before"] = timezone.now().isoformat()
            request.session["updates_read_ids"] = []
        elif action == "mark_read":
            key = request.POST.get("notification_id")
            if key in {row["id"] for row in rows}:
                request.session["updates_read_ids"] = (request.session.get("updates_read_ids", []) + [key])[-100:]
        elif action == "unblock":
            try:
                unblock_user(request.user, request.POST.get("block_id"))
            except (RequestError, ValueError, TypeError) as error:
                messages.error(request, str(error))
            else:
                messages.success(request, "This guest identity is unblocked.")
        else:
            return HttpResponse("Unknown update action.", status=400)
        return redirect("notifications")
    if request.method != "GET":
        return HttpResponse(status=405)
    rows = _notifications_for(request)
    return render(request, "plusone/notifications.html", {
        "notifications": rows, "unread_count": sum(not row["is_read"] for row in rows),
        "reports": own_report_statuses(request.user),
        "blocks": UserBlock.objects.filter(blocker=request.user).select_related("target", "target__userprofile"),
    })


@login_required
@require_POST
def post_safety(request, post_id):
    from .services.safety import report_post, block_user, blocked_between
    post = get_object_or_404(ActivityPost, pk=post_id)
    try:
        consume_browser_limit(request, "report")
        if request.POST.get("action") == "block":
            block_user(request.user, post.user_id)
            messages.success(request, "This guest identity is blocked. Open matches have ended and any confirmed meetups that have not ended are cancelled. Completed plans remain in history.")
        elif request.POST.get("action") == "report":
            report_post(post_id, request.user, request.POST.get("category", "other"), request.POST.get("reason", ""),
                        block=request.POST.get("block_user") == "yes")
            messages.success(request, "Your safety report is recorded. Check its status in My updates.")
        else:
            raise RequestError("Choose report or block.")
    except RequestError as error:
        messages.error(request, str(error))
        post.refresh_from_db()
        response = render(request, "plusone/post_detail.html", {
            "post": post, "return_to": _queue_return(request),
            "post_is_active": post.status == ActivityPost.Status.ACTIVE and not post.is_expired and post.activity_window_end > timezone.now(),
            "post_window_finished": post.activity_window_end <= timezone.now(),
            "matching_unavailable": post.user_id != request.user.pk and blocked_between(request.user.pk, post.user_id),
        }, status=error.status)
        if error.retry_after:
            response["Retry-After"] = str(error.retry_after)
        return response
    return redirect(_queue_return(request))


@require_GET
def service_worker(request):
    response = FileResponse((settings.BASE_DIR / "plusone/static/plusone/service-worker.js").open("rb"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def push_config(request):
    from .services.push_notifications import push_status
    state = push_status()
    return JsonResponse({"enabled": state["available"], "public_key": state.get("public_key", ""),
                         "subscribe_url": reverse("push_subscribe"), "unsubscribe_url": reverse("push_unsubscribe")})


def _push_body(request):
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise RequestError("Invalid subscription data.")
    if not isinstance(payload, dict) or payload.get("session_scope") != session_scope(request.user):
        raise RequestError("Your browser identity changed. Refresh before enabling alerts.", 409)
    return payload


@login_required
@require_POST
def push_subscribe(request):
    from .services.push_notifications import subscribe
    try:
        payload = _push_body(request)
        subscribe(request.user, payload.get("subscription"))
    except RequestError as error:
        return _json_request_error(error)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_unsubscribe(request):
    from .services.push_notifications import unsubscribe
    try:
        payload = _push_body(request)
        unsubscribe(request.user, payload.get("endpoint"))
    except RequestError as error:
        return _json_request_error(error)
    return JsonResponse({"ok": True})


@login_required
@require_GET
def meetup_calendar(request, match_id):
    from .services.meetups import _effective_times
    match = get_object_or_404(Match.objects.select_related("post", "post__location"), pk=match_id)
    if not match.is_participant(request.user):
        return HttpResponseForbidden("Only participants can download this plan.")
    with locked_match(match.pk) as match:
        expire_locked(match)
    if match.status != Match.Status.AGREED or match.meetup_cancelled_at:
        return HttpResponse("Only a confirmed, uncancelled plan can be added to your calendar.", status=409)
    start, end = _effective_times(match)
    def escaped(value):
        return str(value).replace("\\", "\\\\").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n").replace(";", "\\;").replace(",", "\\,")
    def instant(value):
        return value.astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    point = match.meeting_point or match.post.location.name
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Plus One//Campus meetup//EN", "BEGIN:VEVENT",
             f"UID:plusone-{match.pk}@plusone", f"SEQUENCE:{match.plan_revision}", f"DTSTAMP:{instant(timezone.now())}",
             f"DTSTART:{instant(start)}", f"DTEND:{instant(end)}", f"SUMMARY:{escaped(match.post.title)}",
             f"LOCATION:{escaped(point)}", "DESCRIPTION:Check Plus One for the latest changes or cancellation before travelling.",
             "END:VEVENT", "END:VCALENDAR", ""]
    # RFC 5545 folds long lines at 75 octets, without splitting UTF-8.
    folded = []
    for line in lines:
        segment = ""
        for character in line:
            if len((segment + character).encode("utf-8")) > 75:
                folded.append(segment)
                segment = " "
            segment += character
        folded.append(segment)
    response = HttpResponse("\r\n".join(folded), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="plusone-meetup-{match.pk}.ics"'
    response["Cache-Control"] = "private, no-store"
    return response


def _json_request_error(error):
    response = JsonResponse({"ok": False, "error": str(error), "retry_after": error.retry_after}, status=error.status)
    if error.retry_after:
        response["Retry-After"] = str(error.retry_after)
    return response


def discover(request):
    ensure_anonymous_session(request)
    refresh_expired_records()
    context = discover_context_for_user(request.user, request.GET, request.session.get("last_passed_post_id"))
    context["return_to"] = _queue_return(request)
    from .services.analytics import log_discovery_visit
    log_discovery_visit(request.user, context["posts"], request.GET)
    if request.session.get("last_passed_post_id") and not context["undo_pass_post"]:
        request.session.pop("last_passed_post_id", None)
    return render(request, "plusone/discover.html", context)


def about(request):
    ensure_anonymous_session(request)
    return render(request, "plusone/about.html")


def home(request):
    ensure_anonymous_session(request)
    return redirect("discover")


def start_anonymous_session(request):
    ensure_anonymous_session(request)
    return _safe_next_redirect(request, request.GET.get("next"), "discover")


def reset_anonymous_identity(request):
    ensure_anonymous_session(request)
    if request.method == "POST" and request.POST.get("confirm_reset") == "yes":
        if request.POST.get("session_scope") != session_scope(request.user):
            messages.warning(request, "Your identity changed. Review the current plans before starting fresh.")
        else:
            try:
                retired = reset_anonymous_identity_for_request(request)
            except RequestError as error:
                messages.error(request, str(error))
                return render(request, "plusone/profile_setup.html", {"profile": ensure_user_profile(request.user)}, status=error.status)
            messages.success(request, "A fresh identity is ready. Previous live cards, waiting chats and upcoming meetups were closed.")
            return redirect("session")
    from .services.meetups import _effective_times
    posts = list(ActivityPost.objects.filter(user=request.user, status__in=["active", "matched", "paused"]).select_related("location"))
    participant = Q(poster=request.user) | Q(swiper=request.user)
    matches = list(Match.objects.filter(participant, status__in=Match.LIVE_STATUSES).select_related("post"))
    meetups = [m for m in Match.objects.filter(participant, status=Match.Status.AGREED, meetup_cancelled_at__isnull=True).select_related("post", "post__location") if _effective_times(m)[1] > timezone.now()]
    return render(request, "plusone/identity_reset_confirm.html", {
        "reset_posts": posts, "reset_matches": matches, "reset_meetups": meetups,
        "reset_post_count": len(posts), "reset_match_count": len(matches), "reset_meetup_count": len(meetups),
    })


def profile_setup(request):
    ensure_anonymous_session(request)
    profile = ensure_user_profile(request.user)
    if request.method == "POST":
        messages.success(request, "Your temporary identity is active.")
        return _safe_next_redirect(request, request.GET.get("next"), "discover")
    return render(request, "plusone/profile_setup.html", {"profile": profile})


@login_required
def create_post(request):
    refresh_expired_records()
    assist_form = ActivityAssistForm()
    post_form = ActivityPostForm(require_end=True)
    draft_proposal = None
    reviewed = {}
    parsed = None
    time_options = []
    draft_check = None
    date_confirm_message = None
    raw_text = ""
    request_id = request.POST.get("request_id") or str(uuid4())
    response_status = 200
    retry_after = None
    review_token = request.POST.get("review_token") or request.GET.get("draft", "")
    draft_revision = _create_draft_revision(request.POST.get("draft_revision"))
    if request.method == "POST" and request.POST.get("action") == "discard_draft":
        key = _create_review_store_key(request.user)
        reviews = dict(request.session.get(key, {}))
        reviews.pop(review_token, None)
        request.session[key] = reviews
        messages.info(request, "Draft discarded. Fill in a new plan when you are ready.")
        return redirect("create_post")
    if request.method == "GET":
        from .services.analytics import log_create_started
        log_create_started(request.user)
        snapshot = request.session.get(_create_review_store_key(request.user), {}).get(review_token)
        if snapshot:
            reviewed = snapshot.get("reviewed", {})
            raw_text = reviewed.get("raw_text", "")
            post_form = ActivityPostForm(initial=reviewed, require_end=True)
            assist_form = ActivityAssistForm(initial={"raw_text": snapshot.get("assist_text", "")})
            if snapshot.get("assist_errors"):
                assist_form = ActivityAssistForm({"raw_text": snapshot.get("assist_text", "")})
                assist_form.is_valid()
            draft_proposal = snapshot.get("proposal")
            parsed = snapshot.get("parsed")
            draft_check = snapshot.get("draft_check")
            time_options = snapshot.get("time_options", [])
            draft_revision = _create_draft_revision(snapshot.get("draft_revision"))
        elif review_token:
            review_token = ""
            messages.info(request, "This saved review is no longer available. You can start a new plan here.")

    if request.method == "POST" and request.POST.get("action") in {"assist", "apply_draft", "keep_reviewed_draft"}:
        try:
            reviewed = _reviewed_fields(request.POST.get("reviewed_payload", ""))
            # Shared-form submissions carry the current manual edits even
            # with JavaScript disabled. They take precedence over snapshots.
            reviewed.update({key: request.POST[key] for key in set(ActivityPostForm().fields) | {"raw_text"} if key in request.POST})
            raw_text = reviewed.get("raw_text", "")
            post_form = ActivityPostForm(initial=reviewed, require_end=True)
            if request.POST.get("action") == "apply_draft":
                proposal = json.loads(request.POST.get("proposal_payload", "{}"))
                proposed = _reviewed_fields(json.dumps(proposal.get("fields", {})))
                reviewed.update(proposed)
                raw_text = reviewed.get("raw_text", "")
                post_form = ActivityPostForm(initial=reviewed, require_end=True)
                assist_form = ActivityAssistForm(initial={"raw_text": raw_text})
                messages.info(request, "Draft suggestions applied. Review all details before publishing.")
            elif request.POST.get("action") == "keep_reviewed_draft":
                assist_form = ActivityAssistForm(initial={"raw_text": raw_text})
                messages.info(request, "Your reviewed fields were kept.")
        except (RequestError, ValueError, AttributeError) as error:
            response_status = 400
            messages.error(request, str(error))

    if request.method == "POST" and request.POST.get("action") == "assist":
        # Assist is a draft-only path: unsafe input is blocked before parsing,
        # and successful AI output still requires manual review before publish.
        assist_form = ActivityAssistForm({"raw_text": request.POST.get("assist_text", request.POST.get("raw_text", ""))})
        if response_status == 200 and assist_form.is_valid():
            assist_text = assist_form.cleaned_data["raw_text"]
            try:
                consume_browser_limit(request, "ai")
                consume_limit(request.user, "ai")
                moderation = moderate_activity_text(request.user, assist_text)
            except RequestError as error:
                moderation = {"service_unavailable": True, "reason": str(error)}
                response_status, retry_after = error.status, error.retry_after
            if moderation.get("service_unavailable"):
                response_status = response_status if response_status != 200 else 503
                messages.error(request, moderation.get("reason", "Safety checking is temporarily unavailable. Your input has been kept."))
            elif moderation.get("flagged"):
                messages.error(request, f"Safety check flagged this request: {moderation.get('reason', 'Please revise it.')}")
            else:
                parsed = parse_activity_text(request.user, assist_text)
                draft_check = parsed.get("validation")
                draft_proposal = {"fields": {**post_initial_from_ai(parsed), "raw_text": assist_text},
                                  "warnings": draft_check.get("warnings", []) if draft_check else [],
                                  "field_sources": parsed.get("field_sources", {}), "location_label": parsed.get("location_name", "")}
                time_options = suggest_ambiguous_time_options(assist_text) if not parsed.get("start_time") else []
                if draft_check and (draft_check["missing_fields"] or draft_check["warnings"]):
                    messages.warning(request, "Draft ready, but it needs your attention before publishing.")
                else:
                    messages.success(request, "Suggestions ready. Apply them when you choose; your reviewed fields are kept.")

    if request.method == "POST" and response_status == 200 and request.POST.get("action") in {"assist", "apply_draft", "keep_reviewed_draft"}:
        if request.POST.get("action") in {"apply_draft", "keep_reviewed_draft"}:
            draft_revision += 1
        return _save_create_review(request, {
            "reviewed": reviewed, "proposal": draft_proposal, "parsed": parsed,
            "draft_check": draft_check, "time_options": time_options,
            "assist_text": assist_form.data.get("raw_text", "") if assist_form.is_bound else assist_form.initial.get("raw_text", ""),
            "assist_errors": bool(assist_form.errors), "draft_revision": draft_revision,
            "request_id": request_id,
        })

    if request.method == "POST" and request.POST.get("action") == "publish":
        # Publish uses the reviewed structured form, then moderates the final
        # title/description in case the user edited AI output before submitting.
        post_form = ActivityPostForm(request.POST, require_end=True)
        raw_text = request.POST.get("raw_text", "")
        assist_form = ActivityAssistForm(initial={"raw_text": raw_text})
        replay = None
        try:
            replay = published_replay(request.user, request.POST)
        except RequestError as error:
            response_status = error.status
            messages.error(request, str(error))
        if replay:
            return redirect("post_detail", post_id=replay.pk)
        if len(raw_text) > 2000:
            post_form.add_error(None, "Original input must be at most 2,000 characters.")
            response_status = 400
        if response_status == 200 and post_form.is_valid():
            try:
                consume_browser_limit(request, "publish")
                consume_limit(request.user, "publish")
                moderation = moderate_activity_form(request.user, post_form)
            except RequestError as error:
                moderation = {"service_unavailable": True, "reason": str(error)}
                response_status, retry_after = error.status, error.retry_after
            date_confirmed = request.POST.get("confirm_date") == "yes"
            date_confirm_message = None if date_confirmed else check_publish_date(
                raw_text, post_form.cleaned_data.get("start_time")
            )
            if moderation.get("service_unavailable"):
                response_status = response_status if response_status != 200 else 503
                messages.error(request, moderation.get("reason", "Safety checking is temporarily unavailable. Your input has been kept."))
            elif moderation.get("flagged"):
                messages.error(request, f"Safety check flagged this post: {moderation.get('reason', 'Please revise it.')}")
            elif date_confirm_message:
                # Guardrail: an explicit date in the original text conflicts
                # with the reviewed start_time. Block once and ask the user to
                # confirm instead of silently publishing a wrong date.
                messages.warning(request, "Please confirm the start date before publishing.")
            else:
                try:
                    post = save_activity_post_for_user(request.user, post_form, request_id=request.POST.get("request_id"), request_fingerprint=publish_fingerprint(request.POST))
                    messages.success(request, "Your Plus One card is live.")
                    return redirect("post_detail", post_id=post.id)
                except RequestError as error:
                    response_status = error.status
                    messages.error(request, str(error))

    response = render(
        request,
        "plusone/create_post.html",
        {
            "assist_form": assist_form,
            "post_form": post_form,
            "parsed": parsed,
            "post_preview": post_form_preview(post_form),
            "time_options": time_options,
            "draft_check": draft_check,
            "date_confirm_message": date_confirm_message,
            "raw_text": raw_text,
            "request_id": request_id,
            "draft_proposal": draft_proposal,
            "draft_proposal_json": json.dumps(draft_proposal or {}),
            "reviewed_payload_json": json.dumps(reviewed),
            "create_restore_draft": request.method == "GET",
            "create_review_token": review_token,
            "create_draft_revision": draft_revision,
        },
        status=response_status,
    )
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


@login_required
def post_detail(request, post_id):
    from .services.safety import blocked_between
    refresh_expired_records()
    post = get_object_or_404(ActivityPost.objects.select_related("user", "location"), id=post_id)
    existing_swipe = Swipe.objects.filter(user=request.user, post=post).first()
    post_window_finished = post.activity_window_end <= timezone.now()
    post_is_active = post.status == ActivityPost.Status.ACTIVE and not post.is_expired and not post_window_finished
    matching_unavailable = post.user_id != request.user.pk and blocked_between(request.user.pk, post.user_id)
    paused_match = None
    if post.user_id == request.user.id and post.status == ActivityPost.Status.PAUSED:
        paused_match = post.matches.filter(meetup_cancelled_at__isnull=False).order_by("-meetup_cancelled_at").first()
    current_match = post.matches.filter(Q(poster=request.user) | Q(swiper=request.user)).order_by("-created_at", "-pk").first()
    current_match_label = None
    waiting_retry = None
    if current_match:
        current_match_label = {"waiting": "Open waiting room", "chatting": "Continue chat", "agreed": "View meeting plan"}.get(current_match.status, "View match history")
        from .services.matching import waiting_retry_state
        waiting_retry = waiting_retry_state(current_match, request.user)
    return render(
        request,
        "plusone/post_detail.html",
        {"post": post, "existing_swipe": existing_swipe, "post_is_active": post_is_active,
         "matching_unavailable": matching_unavailable,
         "post_window_finished": post_window_finished, "paused_match": paused_match,
         "current_match": current_match, "current_match_label": current_match_label, "waiting_retry": waiting_retry,
         "return_to": _queue_return(request)},
    )


@login_required
def edit_post(request, post_id):
    refresh_expired_records()
    post = get_object_or_404(ActivityPost.objects.select_related("user", "location"), id=post_id)
    if post.user_id != request.user.id:
        return HttpResponseForbidden("Only the post owner can edit this Plus One.")
    if post.status != ActivityPost.Status.ACTIVE or post.is_expired:
        messages.error(request, "Only active, unexpired posts can be edited.")
        return redirect("dashboard")

    if request.method == "POST" and request.POST.get("action") == "cancel":
        try:
            cancel_activity_post(post, require_active=True)
        except RequestError as error:
            messages.warning(request, str(error))
        else:
            messages.info(request, "Your Plus One card was cancelled.")
        return redirect("dashboard")

    form = ActivityPostForm(initial=post_edit_initial(post), instance=post)
    response_status = 200
    retry_after = None
    if request.method == "POST" and request.POST.get("action") == "save":
        form = ActivityPostForm(request.POST, instance=post)
        if form.is_valid():
            try:
                consume_browser_limit(request, "publish")
                consume_limit(request.user, "publish")
                moderation = moderate_activity_form(request.user, form)
            except RequestError as error:
                moderation = {"service_unavailable": True, "reason": str(error)}
                response_status, retry_after = error.status, error.retry_after
            if moderation.get("service_unavailable"):
                response_status = response_status if response_status != 200 else 503
                messages.error(request, moderation.get("reason", "Safety checking is temporarily unavailable. Your edits have been kept."))
            elif moderation.get("flagged"):
                messages.error(request, f"Safety check flagged this update: {moderation.get('reason', 'Please revise it.')}")
            else:
                try:
                    save_activity_post_for_user(request.user, form)
                    messages.success(request, "Your Plus One card was updated.")
                    return redirect("post_detail", post_id=post.id)
                except RequestError as error:
                    response_status = error.status
                    form.add_error(None, str(error))

    response = render(
        request,
        "plusone/edit_post.html",
        {"post": post, "post_form": form, "post_preview": post_form_preview(form)},
        status=response_status,
    )
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


@login_required
def swipe_post(request, post_id):
    if request.method != "POST":
        return redirect("post_detail", post_id=post_id)
    refresh_expired_records()
    result = handle_swipe(request.user, post_id, request.POST.get("action"))
    if request.POST.get("action") == "interested":
        from .services.analytics import log_interested_result
        log_interested_result(request.user, get_object_or_404(ActivityPost, pk=post_id), result.outcome)
    return_to = _queue_return(request)
    if result.outcome == SwipeOutcome.OWN_POST:
        messages.error(request, "You cannot swipe on your own Plus One post.")
        return redirect("post_detail", post_id=result.post_id)
    if result.outcome == SwipeOutcome.INACTIVE_POST:
        messages.error(request, "This post is no longer active.")
        return redirect(return_to)
    if result.outcome == SwipeOutcome.FULL_POST:
        messages.info(request, "That Plus One just filled up. I kept you in Discover so you can pick another card.")
        return redirect(return_to)
    if result.outcome == SwipeOutcome.INVALID_ACTION:
        messages.error(request, "Unknown swipe action.")
        return redirect("post_detail", post_id=result.post_id)
    if result.outcome == SwipeOutcome.PASSED:
        request.session["last_passed_post_id"] = result.post_id
        messages.info(request, "Skipped. Undo is available while you keep browsing.")
        return redirect(return_to)
    if result.outcome == SwipeOutcome.MATCH_CREATED:
        messages.success(request, "It's a vibe. Open the chat; your five minutes begin when you are both there.")
        return redirect(f"{return_to}{'&' if '?' in return_to else '?'}matched={result.match_id}")
    if result.outcome == SwipeOutcome.TRY_AGAIN:
        messages.warning(request, "That card is busy right now. Please try again.")
        return redirect(return_to)
    messages.info(request, "You already matched on this post.")
    return redirect("chat", match_id=result.match_id)


@login_required
def undo_pass(request, post_id):
    if request.method != "POST":
        return redirect("discover")
    deleted, _ = Swipe.objects.filter(user=request.user, post_id=post_id, action=Swipe.Action.PASS).delete()
    if request.session.get("last_passed_post_id") == post_id:
        request.session.pop("last_passed_post_id", None)
    if deleted:
        messages.success(request, "Pass undone. The card is back in your queue.")
    else:
        messages.info(request, "There was no pass to undo.")
    return redirect(_queue_return(request))


@login_required
@require_POST
def retry_waiting(request, match_id):
    from .services.matching import retry_waiting_match
    target = get_object_or_404(Match, pk=match_id)
    if not target.is_participant(request.user):
        return HttpResponseForbidden("Only the two participants can invite each other again.")
    try:
        result = retry_waiting_match(request.user, match_id)
        if result.outcome == SwipeOutcome.MATCH_CREATED:
            messages.success(request, "Invitation sent again. This is a new waiting room; your earlier attempt stays in history.")
        else:
            messages.info(request, "This invitation was already sent. Its waiting deadline has not changed.")
        return redirect("chat", match_id=result.match_id)
    except RequestError as error:
        messages.warning(request, str(error))
        return redirect("chat", match_id=match_id)


@login_required
def dashboard(request):
    refresh_expired_records()
    return render(request, "plusone/dashboard.html", dashboard_context_for_user(request.user))


@login_required
def chat(request, match_id):
    match = get_object_or_404(Match.objects.select_related("post", "poster", "swiper", "post__location"), id=match_id)
    if not match.is_participant(request.user):
        return HttpResponseForbidden("Only matched users can access this chat.")
    match.mark_chat_expired_if_needed()
    form = ChatMessageForm()
    response_status = 200
    retry_after = None

    if request.method == "POST" and request.POST.get("action") == "send":
        form = ChatMessageForm(request.POST)
        if form.is_valid():
            text = form.cleaned_data["message"]
            try:
                message, moderation = _send_message(request, match, text)
            except RequestError as error:
                message, moderation = None, {"reason": str(error), "service_unavailable": True}
                response_status, retry_after = error.status, error.retry_after
            if moderation.get("flagged"):
                messages.error(request, f"Message blocked by safety check: {moderation.get('reason', 'Safety check triggered.')}")
            elif moderation.get("service_unavailable"):
                response_status = response_status if response_status != 200 else 503
                messages.error(request, moderation.get("reason", "Safety checking is temporarily unavailable. Your message has been kept."))
            elif moderation.get("unavailable"):
                response_status = 409
                messages.error(request, "This chat closed before the message could be sent.")
            elif message:
                return redirect("chat", match_id=match.id)

    if request.method == "POST" and request.POST.get("action") == "agree":
        try:
            agreement = record_agreement(match.id, request.user)
            if agreement.recorded:
                messages.success(request, "Your agreement was recorded.")
        except RequestError as error:
            messages.error(request, str(error))
        return redirect("chat", match_id=match.id)

    if request.method == "POST" and request.POST.get("action") in {"decline", "report"}:
        action = request.POST.get("action")
        try:
            if action == "report":
                consume_browser_limit(request, "report")
                report_match(match.pk, request.user, request.POST.get("category", "other"), request.POST.get("reason", ""),
                             block=request.POST.get("block_user") == "yes")
                messages.warning(request, "Safety report recorded. Any open match has ended or any confirmed meetup that has not ended has been cancelled. Completed plans remain in history.")
            else:
                closed = close_match(match.pk, request.user, Match.CloseReason.DECLINED)
                if closed.closed:
                    messages.info(request, "This match is closed.")
                else:
                    match.refresh_from_db()
                    if match.status == Match.Status.AGREED and not match.meetup_cancelled_at:
                        messages.warning(request, "Both people already confirmed a meetup. Review the plan and use Cancel meetup if you no longer want to meet.")
                    else:
                        messages.info(request, "This match was already closed.")
            return redirect("chat", match_id=match.id)
        except RequestError as error:
            response_status = error.status
            messages.error(request, str(error))

    if request.method == "POST" and request.POST.get("action") == "confirm_meetup":
        # This is a self-reported outcome, not independent proof of attendance.
        # Record at most one confirmation per participant and match.
        if confirm_meetup(match.pk, request.user):
            messages.success(request, "Thanks - your meetup confirmation was recorded.")
        return redirect("chat", match_id=match.id)

    opener_suggestions = []
    opener_batch_id = ""
    if request.method == "POST" and request.POST.get("action") == "suggest_openers":
        # Agent-assisted openers: AI drafts, the user picks and sends.
        # Suggestions are never auto-sent (same rule as post publishing).
        if match.status == Match.Status.CHATTING:
            try:
                consume_browser_limit(request, "ai")
                consume_limit(request.user, "ai")
                prior_log = LLMLog.objects.filter(user=request.user, task_type=LLMLog.TaskType.OPENING_ASSISTANT).order_by("-pk").first()
                opener_suggestions = generate_openers(request.user, match)
                current_log = LLMLog.objects.filter(user=request.user, task_type=LLMLog.TaskType.OPENING_ASSISTANT, pk__gt=prior_log.pk if prior_log else 0).order_by("-pk").first()
                from .services.analytics import log_opener_suggestions
                event = log_opener_suggestions(request.user, match, opener_suggestions, metadata={"model": current_log.model, "strategy": current_log.strategy} if current_log else {})
                opener_batch_id = event.properties.get("batch_id", "") if event else ""
            except RequestError as error:
                response_status, retry_after = error.status, error.retry_after
                messages.error(request, str(error))
        else:
            messages.error(request, "This chat is no longer active.")

    with locked_match(match.pk) as match:
        expire_locked(match)
        messages_list = list(match.messages.select_related("sender").order_by("id"))
        meetup_payload = phase_payload(match, request.user)
    response = render(
        request,
        "plusone/chat.html",
        {
            "match": match,
            "messages_list": messages_list,
            "last_message_id": messages_list[-1].id if messages_list else 0,
            "form": form,
            "viewer_agreed": meetup_payload["viewer_agreed"],
            "other_agreed": meetup_payload["other_agreed"],
            "meetup_payload": meetup_payload,
            "closure_notice": meetup_payload["closure_notice"],
            "plan": meetup_payload["plan"],
            "plan_request_id": str(uuid4()),
            "phase_deadline": match.phase_deadline,
            "server_time": timezone.now(),
            "request_id": request.POST.get("request_id") or str(uuid4()),
            "opener_suggestions": opener_suggestions,
            "opener_batch_id": opener_batch_id,
            # Reply mode: once real conversation exists, the assistant
            # suggests continuations instead of first messages.
            "has_user_messages": any(not m.is_system for m in messages_list),
            "meetup_confirmed": ProductEvent.objects.filter(
                name=ProductEvent.Name.MEETUP_CONFIRMED,
                match=match,
                user=request.user,
            ).exists(),
        },
        status=response_status,
    )
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


@login_required
@require_POST
def chat_plan(request, match_id):
    """Mutate only this participant's shared plan, with durable retry IDs."""
    match = get_object_or_404(Match, pk=match_id)
    if not match.is_participant(request.user):
        return HttpResponseForbidden("Only matched users can change this plan.")
    wants_json = "application/json" in request.headers.get("Accept", "")
    try:
        result = perform_meetup_action(
            match.pk,
            request.user,
            request.POST.get("action", ""),
            request.POST.get("request_id"),
            request.POST.get("revision"),
            meeting_point=request.POST.get("meeting_point"),
            meeting_at=request.POST.get("meeting_at"),
            expected_end_at=request.POST.get("expected_end_at"),
            delay_minutes=request.POST.get("delay_minutes"),
            coordination_signal=request.POST.get("coordination_signal"),
            outcome=request.POST.get("outcome"),
            outcome_reason=request.POST.get("outcome_reason"),
            before_moderation=lambda: consume_browser_limit(request, "ai"),
        )
    except RequestError as error:
        if wants_json:
            with locked_match(match.pk) as current:
                expire_locked(current)
                state = phase_payload(current, request.user)
            response = JsonResponse(
                {"ok": False, "error": str(error), "retry_after": error.retry_after, **state},
                status=error.status,
            )
            if error.retry_after:
                response["Retry-After"] = str(error.retry_after)
            return response
        messages.error(request, str(error))
    else:
        if wants_json:
            return JsonResponse({"ok": True, **result})
        messages.success(request, result.get("action_result", {}).get("message", "Your plan was updated."))
    return redirect("chat", match_id=match.pk)


@login_required
def opener_click(request, match_id):
    # Analytics-only endpoint: records that a suggestion was clicked into the
    # input. Best-effort - failures must never affect the chat experience.
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    match = get_object_or_404(Match.objects.select_related("poster", "swiper"), id=match_id)
    if not match.is_participant(request.user):
        return HttpResponseForbidden("Only matched users can access this chat.")
    try:
        index = int(request.POST.get("index", -1))
    except (TypeError, ValueError):
        index = -1
    from .services.analytics import log_opener_click
    log_opener_click(request.user, match, index, batch_id=request.POST.get("batch_id"))
    return JsonResponse({"ok": True})


@login_required
def chat_messages(request, match_id):
    # JSON endpoint used by the chat page for lightweight polling and async
    # sends. It expires only this match instead of sweeping all records.
    match = get_object_or_404(Match.objects.select_related("post", "poster", "swiper"), id=match_id)
    if not match.is_participant(request.user):
        return HttpResponseForbidden("Only matched users can access this chat.")
    match.mark_chat_expired_if_needed()

    if request.method == "POST":
        form = ChatMessageForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors}, status=400)
        try:
            message, moderation = _send_message(request, match, form.cleaned_data["message"])
        except RequestError as error:
            return _json_request_error(error)
        if moderation.get("service_unavailable"):
            return JsonResponse({"ok": False, "error": moderation.get("reason", "Safety checking is temporarily unavailable. Your input has been kept.")}, status=503)
        if moderation.get("flagged"):
            return JsonResponse(
                {
                    "ok": False,
                    "flagged": True,
                    "error": "Message blocked by safety check.",
                    "warning": moderation.get("reason", "Safety check triggered."),
                },
                status=400,
            )
        if moderation.get("unavailable") or message is None:
            return JsonResponse({"ok": False, "error": "This chat is no longer active."}, status=409)
        return JsonResponse(
            {
                "ok": True,
                "message": chat_message_payload(message, request.user),
                "flagged": bool(moderation.get("flagged")),
                "warning": moderation.get("reason", "") if moderation.get("flagged") else "",
            }
        )

    if request.method != "GET":
        return JsonResponse({"ok": False}, status=405)
    after_id = request.GET.get("after", "0")
    if not after_id.isdigit() or len(after_id) > 18:
        return JsonResponse({"ok": False, "error": "Invalid message cursor."}, status=400)
    after_id = int(after_id)
    with locked_match(match.pk) as current:
        expire_locked(current)
        payload = [chat_message_payload(message, request.user) for message in current.messages.select_related("sender").filter(pk__gt=after_id).order_by("id")]
        data = {"ok": True, "messages": payload, "next_cursor": max([after_id] + [item["id"] for item in payload]), **phase_payload(current, request.user)}
    return JsonResponse(data)


@login_required
@require_POST
def chat_presence(request, match_id):
    if request.POST.get("visible") not in {"true", "false"}:
        return JsonResponse({"ok": False, "error": "visible must be true or false"}, status=400)
    try:
        match = set_presence(match_id, request.user, request.POST["visible"] == "true",
                             tab_id=request.POST.get("tab_id"), sequence=request.POST.get("sequence"))
    except RequestError as error:
        return _json_request_error(error)
    if match is None:
        return HttpResponseForbidden("Only participants can update presence.")
    return JsonResponse({"ok": True, **phase_payload(match, request.user)})


@require_GET
def session_updates(request):
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False, "session_scope": "", "matches": [], "notifications": [], "unread_count": 0, "open_count": 0, "waiting_count": 0})
    matches = Match.objects.filter(Q(poster=request.user) | Q(swiper=request.user), status__in=Match.HOLDING_STATUSES).select_related("post").order_by("-created_at")[:50]
    notifications = _notifications_for(request)
    from .services.meetups import _effective_times
    rows = [{"id": match.pk, "url": reverse("chat", args=[match.pk]), "status": match.status, "title": match.post.title}
            for match in matches if match.status in Match.LIVE_STATUSES or (not match.meetup_cancelled_at and _effective_times(match)[1] > timezone.now())]
    return JsonResponse({"authenticated": True, "session_scope": session_scope(request.user), "matches": rows,
                         "notifications": notifications, "unread_count": sum(not row["is_read"] for row in notifications),
                         "open_count": sum(row["status"] in Match.LIVE_STATUSES for row in rows),
                         "waiting_count": sum(row["status"] == Match.Status.WAITING for row in rows), "server_time": timezone.now().isoformat()})


def _send_message(request, match, text):
    identifier = request_uuid(request.POST.get("request_id"))
    if not ChatMessage.objects.filter(match=match, sender=request.user, request_id=identifier).exists():
        consume_browser_limit(request, "message")
    return create_chat_message(match, request.user, text, str(identifier))


def _refresh_user_matches(user):
    ids = list(Match.objects.filter(Q(poster=user) | Q(swiper=user), status__in=Match.HOLDING_STATUSES).values_list("pk", flat=True)[:100])
    for match_id in ids:
        with locked_match(match_id) as match:
            expire_locked(match)
