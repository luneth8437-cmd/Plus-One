from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .ai import parse_activity_text, suggest_ambiguous_time_options
from .forms import ActivityAssistForm, ActivityPostForm, ChatMessageForm
from .models import ActivityPost, Match, Swipe
from .presenters import chat_message_payload, post_edit_initial, post_form_preview, post_initial_from_ai
from .selectors import dashboard_context_for_user, discover_context_for_user
from .services.chat import close_match, create_chat_message, record_agreement
from .services.expiration import refresh_expired_records
from .services.identity import ensure_anonymous_session, ensure_user_profile, reset_anonymous_identity_for_request
from .services.matching import SwipeOutcome, handle_swipe
from .services.posts import cancel_activity_post, moderate_activity_form, moderate_activity_text, save_activity_post_for_user


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

    if request.method == "POST" and request.POST.get("action") == "assist":
        # Assist is a draft-only path: unsafe input is blocked before parsing,
        # and successful AI output still requires manual review before publish.
        assist_form = ActivityAssistForm(request.POST)
        if assist_form.is_valid():
            raw_text = assist_form.cleaned_data["raw_text"]
            moderation = moderate_activity_text(request.user, raw_text)
            if moderation.get("flagged"):
                messages.error(request, f"Safety check flagged this request: {moderation.get('reason', 'Please revise it.')}")
            else:
                parsed = parse_activity_text(request.user, raw_text)
                post_form = ActivityPostForm(initial=post_initial_from_ai(parsed))
                time_options = suggest_ambiguous_time_options(raw_text) if not parsed.get("start_time") else []
                messages.success(request, "Draft ready. Review the details before publishing.")

    if request.method == "POST" and request.POST.get("action") == "publish":
        # Publish uses the reviewed structured form, then moderates the final
        # title/description in case the user edited AI output before submitting.
        post_form = ActivityPostForm(request.POST)
        assist_form = ActivityAssistForm(initial={"raw_text": request.POST.get("raw_text", "")})
        if post_form.is_valid():
            moderation = moderate_activity_form(request.user, post_form)
            if moderation.get("flagged"):
                messages.error(request, f"Safety check flagged this post: {moderation.get('reason', 'Please revise it.')}")
            else:
                post = save_activity_post_for_user(request.user, post_form)
                messages.success(request, "Your Plus One card is live.")
                return redirect("post_detail", post_id=post.id)

    return render(
        request,
        "plusone/create_post.html",
        {
            "assist_form": assist_form,
            "post_form": post_form,
            "parsed": parsed,
            "post_preview": post_form_preview(post_form),
            "time_options": time_options,
        },
    )


@login_required
def post_detail(request, post_id):
    refresh_expired_records()
    post = get_object_or_404(ActivityPost.objects.select_related("user", "location"), id=post_id)
    existing_swipe = Swipe.objects.filter(user=request.user, post=post).first()
    post_is_active = post.status == ActivityPost.Status.ACTIVE and not post.is_expired
    return render(
        request,
        "plusone/post_detail.html",
        {"post": post, "existing_swipe": existing_swipe, "post_is_active": post_is_active},
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
        cancel_activity_post(post)
        messages.info(request, "Your Plus One card was cancelled.")
        return redirect("dashboard")

    form = ActivityPostForm(initial=post_edit_initial(post), instance=post)
    if request.method == "POST" and request.POST.get("action") == "save":
        form = ActivityPostForm(request.POST, instance=post)
        if form.is_valid():
            moderation = moderate_activity_form(request.user, form)
            if moderation.get("flagged"):
                messages.error(request, f"Safety check flagged this update: {moderation.get('reason', 'Please revise it.')}")
            else:
                save_activity_post_for_user(request.user, form)
                messages.success(request, "Your Plus One card was updated.")
                return redirect("post_detail", post_id=post.id)

    return render(
        request,
        "plusone/edit_post.html",
        {"post": post, "post_form": form, "post_preview": post_form_preview(form)},
    )


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
        messages.success(request, "It's a vibe. You have five minutes to chat.")
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

    if request.method == "POST" and request.POST.get("action") == "send":
        form = ChatMessageForm(request.POST)
        if match.status != Match.Status.CHATTING:
            messages.error(request, "This chat is no longer active.")
        elif form.is_valid():
            text = form.cleaned_data["message"]
            message, moderation = create_chat_message(match, request.user, text)
            if moderation.get("flagged"):
                messages.error(request, f"Message blocked by safety check: {moderation.get('reason', 'Safety check triggered.')}")
            elif message:
                return redirect("chat", match_id=match.id)
            return redirect("chat", match_id=match.id)

    if request.method == "POST" and request.POST.get("action") == "agree":
        agreement = record_agreement(match.id, request.user)
        if agreement.recorded:
            messages.success(request, "Your agreement was recorded.")
        return redirect("chat", match_id=match.id)

    if request.method == "POST" and request.POST.get("action") in {"decline", "report"}:
        action = request.POST.get("action")
        reason = Match.CloseReason.REPORTED if action == "report" else Match.CloseReason.DECLINED
        result = close_match(match.id, request.user, reason)
        if result.closed and reason == Match.CloseReason.REPORTED:
            messages.warning(request, "Safety report submitted. This chat was closed.")
        elif result.closed:
            messages.info(request, "Chat declined. The Plus One can continue without this match.")
        else:
            messages.info(request, "This chat is already closed.")
        return redirect("chat", match_id=match.id)

    viewer_agreed = match.poster_agreed if request.user.id == match.poster_id else match.swiper_agreed
    other_agreed = match.swiper_agreed if request.user.id == match.poster_id else match.poster_agreed
    messages_list = list(match.messages.select_related("sender").order_by("id"))
    return render(
        request,
        "plusone/chat.html",
        {
            "match": match,
            "messages_list": messages_list,
            "last_message_id": messages_list[-1].id if messages_list else 0,
            "form": form,
            "viewer_agreed": viewer_agreed,
            "other_agreed": other_agreed,
        },
    )


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
        if match.status != Match.Status.CHATTING:
            return JsonResponse({"ok": False, "error": "This chat is no longer active."}, status=409)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors}, status=400)
        message, moderation = create_chat_message(match, request.user, form.cleaned_data["message"])
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
        return JsonResponse(
            {
                "ok": True,
                "message": chat_message_payload(message, request.user),
                "flagged": bool(moderation.get("flagged")),
                "warning": moderation.get("reason", "") if moderation.get("flagged") else "",
            }
        )

    after_id = request.GET.get("after")
    message_qs = match.messages.select_related("sender").order_by("id")
    if after_id and after_id.isdigit():
        message_qs = message_qs.filter(id__gt=int(after_id))
    payload = [chat_message_payload(message, request.user) for message in message_qs]
    return JsonResponse(
        {
            "ok": True,
            "messages": payload,
            "chat_status": match.status,
            "chat_active": match.status == Match.Status.CHATTING,
        }
    )
