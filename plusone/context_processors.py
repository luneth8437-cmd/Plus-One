from django.db.models import Q

from plusone.models import Match


def open_chat_badge(request):
    if not request.user.is_authenticated:
        return {"open_chat_count": 0}
    count = Match.objects.filter(
        Q(poster=request.user) | Q(swiper=request.user),
        status=Match.Status.CHATTING,
    ).count()
    return {"open_chat_count": count}
