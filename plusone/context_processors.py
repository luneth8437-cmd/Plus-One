from django.db.models import Q
from django.conf import settings
from django.utils.crypto import salted_hmac

from plusone.models import Match


def session_scope(user):
    if not getattr(user, "is_authenticated", False):
        return ""
    return salted_hmac("plusone-browser-state", str(user.pk), algorithm="sha256").hexdigest()[:32]


def product_context(request):
    return {
        "session_scope": session_scope(request.user),
        "campus_name": settings.PLUSONE_CAMPUS_NAME or "Campus",
        "campus_configured": bool(settings.PLUSONE_CAMPUS_NAME),
        "campus_timezone": settings.TIME_ZONE,
        "support_email": settings.PLUSONE_SUPPORT_EMAIL,
        "new_matches_enabled": settings.PLUSONE_NEW_MATCHES_ENABLED,
        "session_lifetime_days": settings.SESSION_COOKIE_AGE // 86400,
    }


def open_chat_badge(request):
    if not request.user.is_authenticated:
        return {"open_chat_count": 0}
    count = Match.objects.filter(
        Q(poster=request.user) | Q(swiper=request.user),
        status__in=Match.LIVE_STATUSES,
    ).count()
    return {"open_chat_count": count}
