"""Demo mode: let a single visitor experience the full loop.

Enabled only when the PLUSONE_DEMO_MODE=1 environment variable is set (the
production default stays clean, per the product decision log). When on:

- Discover lazily tops up a small pool of clearly-labeled demo cards, so the
  queue is never empty for a first-time visitor (no cron required).
- When a visitor matches a demo card, the demo partner greets immediately and
  agrees to meet, so one person can reach chat -> opening assistant ->
  mutual agreement -> handoff in one session.
- The demo partner sends a short canned reply after each visitor message
  (capped), so the five-minute chat feels alive.

Demo writes deliberately bypass the analytics logging paths (direct ORM
creates) so scripted demo behavior does not inflate the product funnel; the
visitor's own events still record with a demo flag on the match.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from plusone.models import ActivityPost, CampusLocation, ChatMessage, Match, UserProfile

DEMO_USERNAME_PREFIX = "demo_partner_"
DEMO_CARD_TARGET = 3
DEMO_REPLY_CAP = 3

DEMO_CARDS = [
    {
        "username": "demo_partner_sam",
        "display_name": "Demo Sam",
        "interests": "badminton, coffee, board games",
        "activity_type": ActivityPost.ActivityType.SPORTS,
        "location": "Campus Sports Hall",
        "title": "Badminton rally (demo card)",
        "description": "Demo card: match me to preview the full Plus One flow.",
    },
    {
        "username": "demo_partner_ria",
        "display_name": "Demo Ria",
        "interests": "coffee, hiking, movies",
        "activity_type": ActivityPost.ActivityType.FOOD,
        "location": "North Dining Hall",
        "title": "Lunch company (demo card)",
        "description": "Demo card: swipe interested to see matching and chat.",
    },
    {
        "username": "demo_partner_leo",
        "display_name": "Demo Leo",
        "interests": "chess, board games, coffee",
        "activity_type": ActivityPost.ActivityType.STUDY,
        "location": "Main Library",
        "title": "Study sprint (demo card)",
        "description": "Demo card: the partner replies automatically in chat.",
    },
]

DEMO_GREETING = (
    "Hi! I'm a demo Plus One partner (auto-reply). Chat away, try the "
    "'Suggest openers' button, and press Agree to see the meet handoff."
)
DEMO_REPLIES = [
    "Sounds good! I'm flexible on time - whatever works for you. (auto-reply)",
    "Nice! When you're ready, hit Agree to meet and I'll do the same. (auto-reply)",
    "This is the last auto-reply - the handoff appears once you agree too.",
]


def demo_mode_enabled():
    return getattr(settings, "PLUSONE_DEMO_MODE", False)


def is_demo_user(user):
    return bool(user) and user.username.startswith(DEMO_USERNAME_PREFIX)


def _demo_user(spec):
    User = get_user_model()
    user, created = User.objects.get_or_create(username=spec["username"])
    if created:
        user.set_unusable_password()
        user.save()
    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            "display_name": spec["display_name"],
            "campus_area": "Campus",
            "interests": spec["interests"],
        },
    )
    return user


def maybe_seed_demo_cards():
    """Top up demo supply. Called from Discover; cheap when pool is full."""
    if not demo_mode_enabled():
        return
    now = timezone.now()
    active_demo = ActivityPost.objects.filter(
        user__username__startswith=DEMO_USERNAME_PREFIX,
        status=ActivityPost.Status.ACTIVE,
        expire_time__gt=now,
    ).count()
    if active_demo >= DEMO_CARD_TARGET:
        return
    for spec in DEMO_CARDS:
        user = _demo_user(spec)
        has_active = ActivityPost.objects.filter(
            user=user, status=ActivityPost.Status.ACTIVE, expire_time__gt=now
        ).exists()
        if has_active:
            continue
        location = (
            CampusLocation.objects.filter(name=spec["location"]).first()
            or CampusLocation.objects.first()
        )
        if not location:
            return
        # Direct create on purpose: demo supply must not fire publish_card
        # analytics events.
        ActivityPost.objects.create(
            user=user,
            title=spec["title"],
            description=spec["description"],
            activity_type=spec["activity_type"],
            location=location,
            start_time=now + timedelta(hours=2),
            expire_time=now + timedelta(hours=6),
            capacity=1,
            status=ActivityPost.Status.ACTIVE,
        )


def maybe_demo_partner_react(match):
    """Greet + agree right after a visitor matches a demo card."""
    if not demo_mode_enabled() or not is_demo_user(match.post.user):
        return
    demo_user = match.post.user
    ChatMessage.objects.create(match=match, sender=demo_user, message=DEMO_GREETING)
    # mark_agreed directly: demo behavior must not log agree_clicked events.
    match.mark_agreed(demo_user)


def maybe_demo_reply(match, human_sender):
    """Canned reply after a visitor message in a demo chat (capped)."""
    if not demo_mode_enabled():
        return
    demo_user = match.post.user
    if not is_demo_user(demo_user) or is_demo_user(human_sender):
        return
    if match.status != Match.Status.CHATTING:
        return
    sent = match.messages.filter(sender=demo_user).count()
    # First demo message is the greeting; replies come after it.
    reply_index = max(0, sent - 1)
    if reply_index >= DEMO_REPLY_CAP:
        return
    ChatMessage.objects.create(
        match=match, sender=demo_user,
        message=DEMO_REPLIES[min(reply_index, len(DEMO_REPLIES) - 1)],
    )
