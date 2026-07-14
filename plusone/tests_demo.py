"""Tests for demo mode: single-visitor full-loop experience."""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ActivityPost, Match, ProductEvent
from .services.demo import DEMO_REPLY_CAP, maybe_seed_demo_cards
from .services.chat import create_chat_message, record_agreement
from .services.matching import SwipeOutcome, handle_swipe


class DemoModeTests(TestCase):
    def setUp(self):
        self.llm_env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
        self.llm_env.start()
        self.addCleanup(self.llm_env.stop)
        User = get_user_model()
        self.visitor = User.objects.create_user("visitor", password="x")

    @override_settings(PLUSONE_DEMO_MODE=True)
    def test_demo_cards_seeded_when_enabled(self):
        maybe_seed_demo_cards()
        demo_posts = ActivityPost.objects.filter(user__username__startswith="demo_partner_")
        self.assertEqual(demo_posts.count(), 3)
        self.assertTrue(all(p.is_demo_card for p in demo_posts))
        # Seeding again does not duplicate.
        maybe_seed_demo_cards()
        self.assertEqual(demo_posts.count(), 3)
        # Demo supply never fires analytics events.
        self.assertEqual(ProductEvent.objects.filter(name=ProductEvent.Name.PUBLISH_CARD).count(), 0)

    @override_settings(PLUSONE_DEMO_MODE=False)
    def test_no_seeding_when_disabled(self):
        maybe_seed_demo_cards()
        self.assertEqual(
            ActivityPost.objects.filter(user__username__startswith="demo_partner_").count(), 0)

    @override_settings(PLUSONE_DEMO_MODE=True)
    def test_single_visitor_reaches_handoff(self):
        maybe_seed_demo_cards()
        post = ActivityPost.objects.filter(user__username__startswith="demo_partner_").first()

        result = handle_swipe(self.visitor, post.id, "interested")
        self.assertEqual(result.outcome, SwipeOutcome.MATCH_CREATED)
        match = Match.objects.get(id=result.match_id)

        # Demo partner greeted and already agreed.
        demo_messages = match.messages.filter(sender=post.user)
        self.assertEqual(demo_messages.count(), 1)
        self.assertIn("demo", demo_messages.first().message.lower())
        match.refresh_from_db()
        self.assertTrue(match.poster_agreed)
        # Demo auto-agree must not log an agree event.
        self.assertEqual(ProductEvent.objects.filter(name=ProductEvent.Name.AGREE_CLICKED).count(), 0)

        # Visitor chats -> demo replies; visitor agrees -> both agreed.
        create_chat_message(match, self.visitor, "Cool, how does this work?")
        self.assertEqual(match.messages.filter(sender=post.user).count(), 2)
        record_agreement(match.id, self.visitor)
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.AGREED)

    @override_settings(PLUSONE_DEMO_MODE=True)
    def test_demo_replies_are_capped(self):
        maybe_seed_demo_cards()
        post = ActivityPost.objects.filter(user__username__startswith="demo_partner_").first()
        result = handle_swipe(self.visitor, post.id, "interested")
        match = Match.objects.get(id=result.match_id)
        for i in range(DEMO_REPLY_CAP + 2):
            create_chat_message(match, self.visitor, f"message {i}")
        demo_count = match.messages.filter(sender=post.user).count()
        # Greeting + at most DEMO_REPLY_CAP replies.
        self.assertLessEqual(demo_count, 1 + DEMO_REPLY_CAP)

    @override_settings(PLUSONE_DEMO_MODE=True)
    def test_discover_view_shows_demo_badge(self):
        self.client.force_login(self.visitor)
        response = self.client.get(reverse("discover"))
        self.assertContains(response, "demo-chip")
