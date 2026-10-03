from uuid import uuid4

from django.contrib import messages
from django.db import connection, DatabaseError
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .ai import generate_openers, parse_activity_text, suggest_ambiguous_time_options
from .forms import ActivityAssistForm, ActivityPostForm, ChatMessageForm
from .models import ActivityPost, Match, ProductEvent, Swipe
from .services.analytics import log_event
from .presenters import chat_message_payload, post_edit_initial, post_form_preview, post_initial_from_ai
from .selectors import dashboard_context_for_user, discover_context_for_user
from .services.chat import close_match, create_chat_message, record_agreement, report_match, confirm_meetup
from .services.meetups import perform_meetup_action
from .services.lifecycle import locked_match, expire_locked, phase_payload, set_presence
from .services.requests import RequestError, consume_limit
from .services.expiration import refresh_expired_records
from .services.identity import ensure_anonymous_session, ensure_user_profile, reset_anonymous_identity_for_request
from .services.matching import SwipeOutcome, handle_swipe
from .services.posts import cancel_activity_post, moderate_activity_form, moderate_activity_text, save_activity_post_for_user, published_replay, publish_fingerprint
from .ai_services.validation import check_publish_date


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


def _json_request_error(error):
    response = JsonResponse({"ok": False, "error": str(error), "retry_after": error.retry_after}, status=error.status)
    if error.retry_after:
        response["Retry-After"] = str(error.retry_after)
    return response


def discover(request):
    ensure_anonymous_session(request)
    refresh_expired_records()
    context = discover_context_for_user(request.user, request.GET, request.session.get("last_passed_post_id"))
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
    if request.method != "POST":
        return redirect("session")
    retired = reset_anonymous_identity_for_request(request)
    if retired["posts"] or retired["matches"]:
        messages.success(request, "A fresh temporary identity is ready. Previous live cards and open chats were closed.")
    else:
        messages.success(request, "A fresh temporary identity is ready.")
    return redirect("session")


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
    post_form = ActivityPostForm()
    parsed = None
    time_options = []
    draft_check = None
    date_confirm_message = None
    raw_text = ""
    request_id = request.POST.get("request_id") or str(uuid4())
    response_status = 200
    retry_after = None

    if request.method == "POST" and request.POST.get("action") == "assist":
        # Assist is a draft-only path: unsafe input is blocked before parsing,
        # and successful AI output still requires manual review before publish.
        assist_form = ActivityAssistForm(request.POST)
        if assist_form.is_valid():
            raw_text = assist_form.cleaned_data["raw_text"]
            try:
                consume_limit(request.user, "ai")
                moderation = moderate_activity_text(request.user, raw_text)
            except RequestError as error:
                moderation = {"service_unavailable": True, "reason": str(error)}
                response_status, retry_after = error.status, error.retry_after
            if moderation.get("service_unavailable"):
                response_status = response_status if response_status != 200 else 503
                messages.error(request, moderation.get("reason", "Safety checking is temporarily unavailable. Your input has been kept."))
            elif moderation.get("flagged"):
                messages.error(request, f"Safety check flagged this request: {moderation.get('reason', 'Please revise it.')}")
            else:
                parsed = parse_activity_text(request.user, raw_text)
                draft_check = parsed.get("validation")
                post_form = ActivityPostForm(initial=post_initial_from_ai(parsed))
                time_options = suggest_ambiguous_time_options(raw_text) if not parsed.get("start_time") else []
                if draft_check and (draft_check["missing_fields"] or draft_check["warnings"]):
                    messages.warning(request, "Draft ready, but it needs your attention before publishing.")
                else:
                    messages.success(request, "Draft ready. Review the details before publishing.")

    if request.method == "POST" and request.POST.get("action") == "publish":
        # Publish uses the reviewed structured form, then moderates the final
        # title/description in case the user edited AI output before submitting.
        post_form = ActivityPostForm(request.POST)
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
                consume_limit(request.user, "publish", request_id=request.POST.get("request_id"), digest=publish_fingerprint(request.POST))
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
        },
        status=response_status,
    )
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


@login_required
def post_detail(request, post_id):
    refresh_expired_records()
    post = get_object_or_404(ActivityPost.objects.select_related("user", "location"), id=post_id)
    existing_swipe = Swipe.objects.filter(user=request.user, post=post).first()
    post_window_finished = post.activity_window_end <= timezone.now()
    post_is_active = post.status == ActivityPost.Status.ACTIVE and not post.is_expired and not post_window_finished
    paused_match = None
    if post.user_id == request.user.id and post.status == ActivityPost.Status.PAUSED:
        paused_match = post.matches.filter(meetup_cancelled_at__isnull=False).order_by("-meetup_cancelled_at").first()
    return render(
        request,
        "plusone/post_detail.html",
        {"post": post, "existing_swipe": existing_swipe, "post_is_active": post_is_active,
         "post_window_finished": post_window_finished, "paused_match": paused_match},
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
    if result.outcome == SwipeOutcome.OWN_POST:
        messages.error(request, "You cannot swipe on your own Plus One post.")
        return redirect("post_detail", post_id=result.post_id)
    if result.outcome == SwipeOutcome.INACTIVE_POST:
        messages.error(request, "This post is no longer active.")
        return redirect("discover")
    if result.outcome == SwipeOutcome.FULL_POST:
        messages.info(request, "That Plus One just filled up. I kept you in Discover so you can pick another card.")
        return redirect("discover")
    if result.outcome == SwipeOutcome.INVALID_ACTION:
        messages.error(request, "Unknown swipe action.")
        return redirect("post_detail", post_id=result.post_id)
    if result.outcome == SwipeOutcome.PASSED:
        request.session["last_passed_post_id"] = result.post_id
        messages.info(request, "Skipped. Undo is available while you keep browsing.")
        return redirect("discover")
    if result.outcome == SwipeOutcome.MATCH_CREATED:
        messages.success(request, "It's a vibe. Open the chat; your five minutes begin when you are both there.")
        return redirect(f"{reverse('discover')}?matched={result.match_id}")
    if result.outcome == SwipeOutcome.TRY_AGAIN:
        messages.warning(request, "That card is busy right now. Please try again.")
        return redirect("discover")
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
    return redirect("discover")


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
                message, moderation = create_chat_message(match, request.user, text, request.POST.get("request_id"))
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
                report_match(match.pk, request.user, request.POST.get("category", "other"), request.POST.get("reason", ""))
                messages.warning(request, "Safety report recorded. Any open conversation has been closed.")
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
    if request.method == "POST" and request.POST.get("action") == "suggest_openers":
        # Agent-assisted openers: AI drafts, the user picks and sends.
        # Suggestions are never auto-sent (same rule as post publishing).
        if match.status == Match.Status.CHATTING:
            try:
                consume_limit(request.user, "ai")
                opener_suggestions = generate_openers(request.user, match)
                log_event(ProductEvent.Name.OPENER_SUGGESTED, user=request.user, match=match, properties={"count": len(opener_suggestions), "texts": [opener["text"] for opener in opener_suggestions]})
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
            "plan": meetup_payload["plan"],
            "plan_request_id": str(uuid4()),
            "phase_deadline": match.phase_deadline,
            "server_time": timezone.now(),
            "request_id": request.POST.get("request_id") or str(uuid4()),
            "opener_suggestions": opener_suggestions,
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
            outcome=request.POST.get("outcome"),
            outcome_reason=request.POST.get("outcome_reason"),
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
    log_event(
        ProductEvent.Name.OPENER_CLICKED,
        user=request.user,
        match=match,
        properties={"index": index},
    )
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
            message, moderation = create_chat_message(match, request.user, form.cleaned_data["message"], request.POST.get("request_id"))
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
    match = set_presence(match_id, request.user, request.POST["visible"] == "true")
    if match is None:
        return HttpResponseForbidden("Only participants can update presence.")
    return JsonResponse({"ok": True, **phase_payload(match, request.user)})


@require_GET
def session_updates(request):
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False, "matches": [], "open_count": 0, "waiting_count": 0})
    ids = list(Match.objects.filter(Q(poster=request.user) | Q(swiper=request.user), status__in=Match.LIVE_STATUSES).values_list("pk", flat=True))
    for match_id in ids:
        with locked_match(match_id) as match:
            expire_locked(match)
    matches = Match.objects.filter(Q(poster=request.user) | Q(swiper=request.user), status__in=Match.HOLDING_STATUSES).select_related("post").order_by("-created_at")[:50]
    rows = [{"id": match.pk, "url": reverse("chat", args=[match.pk]), "status": match.status, "title": match.post.title} for match in matches]
    return JsonResponse({"authenticated": True, "matches": rows, "open_count": sum(row["status"] in Match.LIVE_STATUSES for row in rows), "waiting_count": sum(row["status"] == Match.Status.WAITING for row in rows), "server_time": timezone.now().isoformat()})
