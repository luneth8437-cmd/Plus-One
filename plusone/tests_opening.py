"""Tests for the opening assistant (agent-shaped opener suggestions)."""

import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from plusone.ai_services.opening_assistant import (
    gather_context,
    generate_openers,
    rule_generate_openers,
    validate_openers,
)
from plusone.models import ActivityPost, CampusLocation, LLMLog, Match, UserProfile


class _FakeResponse:
    def __init__(self, content):
        message = type("M", (), {"content": content})
        self.choices = [type("C", (), {"message": message})]


def _fake_llm(content):
    def llm_client():
        return object(), {"model": "fake-model", "strategy": "deepseek"}

    def chat_completion(client, llm_config, **kwargs):
        return _FakeResponse(content)

    return llm_client, chat_completion


class OpeningAssistantTests(TestCase):
    def setUp(self):
        # Keep tests offline even when a local .env provides real API keys.
        self.llm_env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
        self.llm_env.start()
        self.addCleanup(self.llm_env.stop)
        User = get_user_model()
        self.poster = User.objects.create_user("poster_real_name", password="x")
        self.swiper = User.objects.create_user("swiper_real_name", password="x")
        UserProfile.objects.create(
            user=self.poster, display_name="Alex Chen",
            major="CS", interests="basketball, board games, coffee")
        UserProfile.objects.create(
            user=self.swiper, display_name="Blair Wu",
            major="Design", interests="coffee, hiking, Basketball")
        self.location, _ = CampusLocation.objects.update_or_create(
            name="Campus Sports Hall",
            defaults={
                "location_type": CampusLocation.LocationType.SPORTS,
                "area": "Central Campus",
            },
        )
        self.post = ActivityPost.objects.create(
            user=self.poster,
            title="Basketball game tonight",
            description="Casual game",
            activity_type=ActivityPost.ActivityType.SPORTS,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=3),
            expire_time=timezone.now() + timedelta(hours=1),
        )
        self.match = Match.objects.create(
            post=self.post, poster=self.poster, swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5))

    # -- Step 1: context assembly ------------------------------------

    def test_context_contains_both_cards_and_shared_interests(self):
        context = gather_context(self.match, self.swiper)
        self.assertEqual(context["viewer_role"], "swiper")
        self.assertEqual(context["post"]["location"], "Campus Sports Hall")
        self.assertEqual(context["shared_interests"], ["basketball", "coffee"])
        self.assertEqual(context["partner"]["major"], "CS")

    def test_context_never_leaks_identity(self):
        context = gather_context(self.match, self.poster)
        flat = repr(context).lower()
        for leaked in ("alex", "blair", "poster_real_name", "swiper_real_name"):
            self.assertNotIn(leaked, flat)

    # -- Step 3: validation guardrails --------------------------------

    def test_validation_drops_unsafe_long_probing_and_duplicate_openers(self):
        candidates = [
            {"text": "Hey! Ready for the game at the hall?", "reason": "ok"},
            {"text": "hey! ready for the game at the hall?", "reason": "duplicate"},
            {"text": "What is your real name and phone number?", "reason": "probes identity"},
            {"text": "x" * 300, "reason": "too long"},
            {"text": "Bring a weapon to the game", "reason": "unsafe"},
            "not-a-dict",
        ]
        valid = validate_openers(candidates)
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["text"], "Hey! Ready for the game at the hall?")

    # -- Step 4: deterministic fallback --------------------------------

    def test_rule_openers_are_personalized_from_shared_context(self):
        context = gather_context(self.match, self.swiper)
        openers = rule_generate_openers(context)
        self.assertGreaterEqual(len(openers), 2)
        self.assertLessEqual(len(openers), 3)
        joined = " ".join(o["text"] for o in openers)
        self.assertIn("basketball", joined.lower())  # shared interest used
        self.assertIn("Campus Sports Hall", joined)  # location used
        self.assertTrue(all(o["reason"] for o in openers))

    # -- Steps 1-4 end to end -----------------------------------------

    def test_llm_output_is_validated_and_topped_up(self):
        # LLM returns one valid and one probing opener: probing one must be
        # dropped and the list topped up from the rule fallback.
        llm_client, chat_completion = _fake_llm(
            '{"openers": ['
            '{"text": "Hey, still up for basketball tonight?", "reason": "references the plan"},'
            '{"text": "Tell me your real name first!", "reason": "bad"}'
            "]}"
        )
        openers = generate_openers(self.swiper, self.match,
                                   llm_client=llm_client, chat_completion=chat_completion)
        self.assertGreaterEqual(len(openers), 2)
        texts = [o["text"] for o in openers]
        self.assertIn("Hey, still up for basketball tonight?", texts)
        self.assertTrue(all("real name" not in t.lower() for t in texts))
        log = LLMLog.objects.filter(task_type=LLMLog.TaskType.OPENING_ASSISTANT).latest("id")
        self.assertIn("topped_up_rule", log.strategy)

    def test_llm_failure_falls_back_to_rules_and_logs(self):
        def llm_client():
            return object(), {"model": "fake-model", "strategy": "deepseek"}

        def chat_completion(client, llm_config, **kwargs):
            raise RuntimeError("provider down")

        openers = generate_openers(self.swiper, self.match,
                                   llm_client=llm_client, chat_completion=chat_completion)
        self.assertGreaterEqual(len(openers), 2)
        log = LLMLog.objects.filter(task_type=LLMLog.TaskType.OPENING_ASSISTANT).latest("id")
        self.assertFalse(log.success)
        self.assertIn("failed_rule_fallback", log.strategy)

    # -- View integration ----------------------------------------------

    def test_chat_view_renders_suggestions_on_demand_and_does_not_send(self):
        self.client.force_login(self.swiper)
        url = reverse("chat", args=[self.match.id])
        before = self.match.messages.count()

        response = self.client.post(url, {"action": "suggest_openers"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "opener-suggestion")
        self.assertContains(response, "data-quick-reply")
        # Crucial: suggesting openers must not create chat messages.
        self.assertEqual(self.match.messages.count(), before)

    def test_suggest_openers_rejected_when_chat_closed(self):
        self.match.status = Match.Status.DECLINED
        self.match.save(update_fields=["status"])
        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[self.match.id]),
                                    {"action": "suggest_openers"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "opener-suggestion")

    def test_non_participant_cannot_request_openers(self):
        User = get_user_model()
        outsider = User.objects.create_user("outsider", password="x")
        self.client.force_login(outsider)
        response = self.client.post(reverse("chat", args=[self.match.id]),
                                    {"action": "suggest_openers"})
        self.assertEqual(response.status_code, 403)
