"""Seed scripted demo traffic through the real service layer.

    python manage.py seed_funnel_demo

Purpose: validate the ProductEvent instrumentation end to end and produce a
first funnel snapshot. Every event is fired by the same production code paths
a real user would hit (form publish -> handle_swipe -> generate_openers ->
create_chat_message -> record_agreement); nothing writes ProductEvent rows
directly except the opener_suggested log, which mirrors the chat view.

This is *scripted* traffic and must be reported as such. Session mix:

    8 full loop   (openers suggested; 3 sent verbatim, 3 edited, 2 ignored;
                   partner replies; both agree)
    5 chat only   (messages both ways in 3 of them, one-sided in 2; no agree)
    4 silent match (match created, nobody messages)
    3 unmatched   (card published, nobody swipes)

Run with API keys unset for a fast, free run (deterministic fallbacks fire).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from plusone.ai import generate_openers
from plusone.forms import ActivityPostForm
from plusone.models import CampusLocation, Match, ProductEvent, UserProfile
from plusone.services.analytics import log_event
from plusone.services.chat import create_chat_message, record_agreement
from plusone.services.matching import SwipeOutcome, handle_swipe
from plusone.services.posts import save_activity_post_for_user

ACTIVITIES = [
    ("sports", "Campus Sports Hall", "Badminton doubles", "basketball, badminton, coffee"),
    ("food", "North Dining Hall", "Lunch buddy", "coffee, hiking, movies"),
    ("study", "Main Library", "Study sprint", "board games, coffee, chess"),
    ("club", "Student Center", "Club fair tour", "anime, chess, running"),
    ("explore", "Campus Quad", "Campus walk", "photography, hiking, coffee"),
]


class Command(BaseCommand):
    help = "Seed scripted demo sessions through real service paths to populate the funnel."

    def _pair(self, index, interests):
        User = get_user_model()
        stamp = timezone.now().strftime("%m%d%H%M%S%f")[:14]
        poster = User.objects.create_user(f"funnel_p{index}_{stamp}", password="x")
        swiper = User.objects.create_user(f"funnel_s{index}_{stamp}", password="x")
        UserProfile.objects.create(user=poster, display_name=f"Poster {index}", interests=interests)
        UserProfile.objects.create(user=swiper, display_name=f"Swiper {index}", interests=interests)
        return poster, swiper

    def _publish(self, poster, index):
        activity_type, location_name, title, _ = ACTIVITIES[index % len(ACTIVITIES)]
        location = CampusLocation.objects.filter(name=location_name).first() or CampusLocation.objects.first()
        start = timezone.localtime() + timedelta(hours=2)
        form = ActivityPostForm(data={
            "title": f"{title} #{index}",
            "description": "Scripted demo session for funnel validation.",
            "activity_type": activity_type,
            "location": location.id,
            "start_time": start.strftime("%Y-%m-%dT%H:%M"),
            "expire_minutes": 60,
        })
        if not form.is_valid():
            raise SystemExit(f"Demo post form invalid: {form.errors}")
        return save_activity_post_for_user(poster, form)

    def _match(self, swiper, post):
        result = handle_swipe(swiper, post.id, "interested")
        if result.outcome != SwipeOutcome.MATCH_CREATED:
            raise SystemExit(f"Expected match, got {result.outcome}")
        return Match.objects.get(id=result.match_id)

    def _suggest(self, user, match):
        openers = generate_openers(user, match)
        log_event(  # mirrors the chat view's suggest_openers action
            ProductEvent.Name.OPENER_SUGGESTED,
            user=user, match=match,
            properties={"count": len(openers), "texts": [o["text"] for o in openers]},
        )
        return openers

    def handle(self, *args, **options):
        if not CampusLocation.objects.exists():
            self.stdout.write(self.style.WARNING("No campus locations. Run migrate first."))
            return

        session = 0

        # 8 full-loop sessions with varied opener adoption.
        for usage in ["verbatim", "verbatim", "verbatim", "edited", "edited", "edited", "none", "none"]:
            poster, swiper = self._pair(session, ACTIVITIES[session % len(ACTIVITIES)][3])
            post = self._publish(poster, session)
            match = self._match(swiper, post)
            openers = self._suggest(swiper, match)
            if usage == "verbatim" and openers:
                first = openers[0]["text"]
            elif usage == "edited" and openers:
                first = openers[0]["text"] + " I can bring snacks too."
            else:
                first = "Hey! Still up for this? I can head over soon."
            create_chat_message(match, swiper, first)
            create_chat_message(match, poster, "Yes! See you at the entrance in ten?")
            record_agreement(match.id, swiper)
            record_agreement(match.id, poster)
            session += 1

        # 5 chat-only sessions (no agreement); 2 of them get no reply.
        for replied in [True, True, True, False, False]:
            poster, swiper = self._pair(session, ACTIVITIES[session % len(ACTIVITIES)][3])
            post = self._publish(poster, session)
            match = self._match(swiper, post)
            if session % 2 == 0:
                self._suggest(swiper, match)  # suggested but ignored
            create_chat_message(match, swiper, "Hi! What level are you playing at these days?")
            if replied:
                create_chat_message(match, poster, "Pretty casual honestly, that ok?")
            session += 1

        # 4 silent matches.
        for _ in range(4):
            poster, swiper = self._pair(session, ACTIVITIES[session % len(ACTIVITIES)][3])
            post = self._publish(poster, session)
            self._match(swiper, post)
            session += 1

        # 3 cards nobody swipes on.
        for _ in range(3):
            poster, _swiper = self._pair(session, ACTIVITIES[session % len(ACTIVITIES)][3])
            self._publish(poster, session)
            session += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {session} scripted sessions. Run: python manage.py funnel_report"))
