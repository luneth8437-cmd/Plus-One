"""Tests for server-side product events and the funnel report."""

import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from plusone.forms import ActivityPostForm
from plusone.management.commands.funnel_report import build_report
from plusone.models import ActivityPost, CampusLocation, Match, ProductEvent
from plusone.services.analytics import classify_opener_usage, log_event
from plusone.services.chat import create_chat_message, record_agreement
from plusone.services.matching import SwipeOutcome, handle_swipe
from plusone.services.posts import save_activity_post_for_user


class ProductEventTests(TestCase):
    def setUp(self):
        self.llm_env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
        self.llm_env.start()
        self.addCleanup(self.llm_env.stop)
        User = get_user_model()
        self.poster = User.objects.create_user("evt_poster", password="x")
        self.swiper = User.objects.create_user("evt_swiper", password="x")
        self.location, _ = CampusLocation.objects.update_or_create(
            name="Campus Sports Hall",
            defaults={
                "location_type": CampusLocation.LocationType.SPORTS,
                "area": "Central Campus",
            },
        )

    def _publish_post(self):
        start = timezone.localtime() + timedelta(hours=3)
        form = ActivityPostForm(
            data={
                "title": "Badminton session",
                "description": "Casual",
                "activity_type": ActivityPost.ActivityType.SPORTS,
                "location": self.location.id,
                "start_time": start.strftime("%Y-%m-%dT%H:%M"),
                "expire_minutes": 60,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        return save_activity_post_for_user(self.poster, form)

    def test_publish_and_match_events(self):
        post = self._publish_post()
        self.assertEqual(
            ProductEvent.objects.filter(name=ProductEvent.Name.PUBLISH_CARD, post=post).count(), 1)

        result = handle_swipe(self.swiper, post.id, "interested")
        self.assertEqual(result.outcome, SwipeOutcome.MATCH_CREATED)
        event = ProductEvent.objects.get(name=ProductEvent.Name.MATCH_CREATED)
        self.assertEqual(event.match_id, result.match_id)

    def test_message_funnel_events_and_opener_attribution(self):
        post = self._publish_post()
        result = handle_swipe(self.swiper, post.id, "interested")
        match = Match.objects.get(id=result.match_id)

        suggested = "Nice, a fellow badminton fan - meet at the entrance?"
        log_event(
            ProductEvent.Name.OPENER_SUGGESTED,
            user=self.swiper, match=match,
            properties={"count": 3, "texts": [suggested, "Second option here."]},
        )

        # First message: verbatim use of a suggestion.
        create_chat_message(match, self.swiper, suggested)
        first = ProductEvent.objects.get(name=ProductEvent.Name.FIRST_MESSAGE_SENT)
        self.assertEqual(first.properties["opener_usage"], "verbatim")

        # Reply from the other side triggers first_reply_received, usage none.
        create_chat_message(match, self.poster, "Sure, see you there!")
        reply = ProductEvent.objects.get(name=ProductEvent.Name.FIRST_REPLY_RECEIVED)
        self.assertEqual(reply.user, self.poster)
        self.assertEqual(reply.properties["opener_usage"], "none")
        self.assertEqual(
            ProductEvent.objects.filter(name=ProductEvent.Name.MESSAGE_SENT).count(), 2)

    def test_edited_suggestion_is_classified_as_edited(self):
        post = self._publish_post()
        result = handle_swipe(self.swiper, post.id, "interested")
        match = Match.objects.get(id=result.match_id)
        log_event(
            ProductEvent.Name.OPENER_SUGGESTED,
            user=self.swiper, match=match,
            properties={"texts": ["Nice, a fellow badminton fan - meet at the entrance?"]},
        )
        usage = classify_opener_usage(
            match, self.swiper, "Nice, a fellow badminton fan! I can bring rackets.")
        self.assertEqual(usage, "edited")
        self.assertEqual(
            classify_opener_usage(match, self.swiper, "Totally unrelated message"), "none")

    def test_agree_event_and_funnel_report(self):
        post = self._publish_post()
        result = handle_swipe(self.swiper, post.id, "interested")
        match = Match.objects.get(id=result.match_id)
        create_chat_message(match, self.swiper, "Hey, still up for it?")
        create_chat_message(match, self.poster, "Yes!")
        record_agreement(match.id, self.swiper)
        record_agreement(match.id, self.poster)

        self.assertEqual(
            ProductEvent.objects.filter(name=ProductEvent.Name.AGREE_CLICKED).count(), 2)

        report = build_report()
        self.assertEqual(report["funnel"]["publish_card"], 1)
        self.assertEqual(report["funnel"]["match_created"], 1)
        self.assertEqual(report["funnel"]["matches_with_first_message"], 1)
        self.assertEqual(report["funnel"]["matches_with_first_reply"], 1)
        self.assertEqual(report["funnel"]["matches_both_agreed"], 1)
        self.assertEqual(report["conversion"]["match_to_first_message_pct"], 100.0)

    def test_no_message_text_is_stored_in_events(self):
        post = self._publish_post()
        result = handle_swipe(self.swiper, post.id, "interested")
        match = Match.objects.get(id=result.match_id)
        secret = "My private plan mentioning a secret keyword xyzzy"
        create_chat_message(match, self.swiper, secret)
        for event in ProductEvent.objects.all():
            self.assertNotIn("xyzzy", repr(event.properties))


class OpenerClickEventTests(TestCase):
    def setUp(self):
        self.llm_env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
        self.llm_env.start()
        self.addCleanup(self.llm_env.stop)
        User = get_user_model()
        self.poster = User.objects.create_user("clk_poster", password="x")
        self.swiper = User.objects.create_user("clk_swiper", password="x")
        location, _ = CampusLocation.objects.update_or_create(
            name="Campus Sports Hall",
            defaults={
                "location_type": CampusLocation.LocationType.SPORTS,
                "area": "Central Campus",
            },
        )
        post = ActivityPost.objects.create(
            user=self.poster, title="Game", description="x",
            activity_type=ActivityPost.ActivityType.SPORTS, location=location,
            start_time=timezone.now() + timedelta(hours=3),
            expire_time=timezone.now() + timedelta(hours=1),
        )
        self.match = Match.objects.get_or_create(
            post=post, swiper=self.swiper,
            defaults={"poster": self.poster,
                      "chat_expires_at": timezone.now() + timedelta(minutes=5)},
        )[0]

    def test_click_logs_event_for_participant(self):
        from django.urls import reverse
        self.client.force_login(self.swiper)
        response = self.client.post(
            reverse("opener_click", args=[self.match.id]), {"index": 1})
        self.assertEqual(response.status_code, 200)
        event = ProductEvent.objects.get(name=ProductEvent.Name.OPENER_CLICKED)
        self.assertEqual(event.properties["index"], 1)

    def test_click_forbidden_for_non_participant(self):
        from django.urls import reverse
        User = get_user_model()
        outsider = User.objects.create_user("clk_outsider", password="x")
        self.client.force_login(outsider)
        response = self.client.post(
            reverse("opener_click", args=[self.match.id]), {"index": 0})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ProductEvent.objects.count(), 0)
