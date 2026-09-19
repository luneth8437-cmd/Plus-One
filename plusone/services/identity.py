import secrets

from django.contrib.auth import get_user_model, login, logout
from django.db.models import Q
from django.db import transaction
from django.utils import timezone

from plusone.models import ActivityPost, Match, UserProfile


ANONYMOUS_SESSION_USERNAME_KEY = "plusone_anonymous_username"


def anonymous_profile_defaults(username):
    code = username.removeprefix("anon_")[:4].upper()
    return {
        "display_name": f"Campus Guest {code}",
        "avatar_initial": code[:2] or "CG",
        "major": "",
        "year": "",
        "campus_area": "Campus",
        "interests": "",
    }


def create_anonymous_user():
    User = get_user_model()
    for _ in range(10):
        username = f"anon_{secrets.token_hex(4)}"
        if not User.objects.filter(username=username).exists():
            user = User(username=username)
            user.set_unusable_password()
            user.save()
            UserProfile.objects.create(user=user, **anonymous_profile_defaults(username))
            return user
    raise RuntimeError("Could not allocate an anonymous Plus One identity.")


def ensure_user_profile(user):
    if user.username.startswith("anon_"):
        defaults = anonymous_profile_defaults(user.username)
    else:
        defaults = {
            "display_name": user.get_full_name() or user.username,
            "avatar_initial": (user.username[:1] or "S").upper(),
        }
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults=defaults)
    if not profile.avatar_initial:
        profile.avatar_initial = (profile.display_name[:1] or user.username[:1] or "S").upper()
        profile.save(update_fields=["avatar_initial"])
    return profile


def ensure_anonymous_session(request):
    # Most pages are usable without signup. Anonymous users are real Django
    # users so posts, swipes, and chats can keep normal foreign-key ownership.
    if request.user.is_authenticated:
        profile = ensure_user_profile(request.user)
        if not profile.retired_at:
            return request.user
        logout(request)

    username = request.session.get(ANONYMOUS_SESSION_USERNAME_KEY)
    User = get_user_model()
    user = User.objects.filter(username=username).first() if username else None
    if user is None:
        user = create_anonymous_user()
        request.session[ANONYMOUS_SESSION_USERNAME_KEY] = user.username

    login(request, user)
    return user


def retire_anonymous_identity(user):
    if not getattr(user, "is_authenticated", False) or not user.username.startswith("anon_"):
        return {"posts": 0, "matches": 0}

    # Resetting an identity must also close live state from the old identity;
    # otherwise stale anonymous users could keep appearing in Discover/chat.
    from plusone.services.lifecycle import end_locked, locked_match, lock_users
    from plusone.services.posts import cancel_activity_post
    # Fence in-flight requests before enumerating state. Every create/activate
    # path rechecks this flag under the same user lock after external work.
    with transaction.atomic():
        lock_users([user.pk])
        profile = ensure_user_profile(user)
        if not profile.retired_at:
            profile.retired_at = timezone.now()
            profile.save(update_fields=["retired_at"])
    posts = 0
    matches = Match.objects.filter(Q(poster=user) | Q(swiper=user), status__in=Match.LIVE_STATUSES).count()
    for post in ActivityPost.objects.filter(user=user, status__in=[ActivityPost.Status.ACTIVE, ActivityPost.Status.MATCHED]).exclude(matches__status=Match.Status.AGREED):
        cancel_activity_post(post)
        posts += 1
    ids = list(Match.objects.filter(Q(poster=user) | Q(swiper=user), status__in=Match.LIVE_STATUSES).values_list("pk", flat=True))
    for match_id in ids:
        with locked_match(match_id) as match:
            end_locked(match, reason=Match.CloseReason.RESET, user=user)
    return {"posts": posts, "matches": matches}


def reset_anonymous_identity_for_request(request):
    retired = retire_anonymous_identity(request.user)
    logout(request)
    user = create_anonymous_user()
    request.session[ANONYMOUS_SESSION_USERNAME_KEY] = user.username
    login(request, user)
    return retired
