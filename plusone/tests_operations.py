import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from plusone.ai_services.client import DEFAULT_LLM_MAX_RETRIES, DEFAULT_LLM_TIMEOUT_SECONDS, llm_client
from plusone.forms import ActivityAssistForm
from plusone.models import ActivityPost, CampusLocation, ChatMessage, LLMLog, Match, ProductEvent
from plusone.services.chat import create_chat_message


class HealthAndInputLimitTests(TestCase):
    def test_healthz_is_database_and_session_free(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertEqual(len(queries), 0)
        self.assertNotIn("sessionid", response.cookies)
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_activity_assist_rejects_more_than_2000_characters(self):
        form = ActivityAssistForm({"raw_text": "x" * 2001})

        self.assertFalse(form.is_valid())
        self.assertIn("raw_text", form.errors)


class ChatConcurrencyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.poster = User.objects.create_user(username="chat_poster")
        self.swiper = User.objects.create_user(username="chat_swiper")
        self.outsider = User.objects.create_user(username="chat_outsider")
        location = CampusLocation.objects.create(
            name="Chat Test Hall",
            location_type=CampusLocation.LocationType.OTHER,
            area="Test Campus",
        )
        post = ActivityPost.objects.create(
            user=self.poster,
            title="Chat race test",
            activity_type=ActivityPost.ActivityType.OTHER,
            location=location,
            start_time=timezone.now() + timedelta(hours=1),
            expire_time=timezone.now() + timedelta(minutes=45),
        )
        self.match = Match.objects.create(
            post=post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_message_is_not_written_if_chat_closes_during_moderation(self):
        def close_during_moderation(user, text):
            Match.objects.filter(id=self.match.id).update(status=Match.Status.DECLINED)
            return {"flagged": False, "reason": ""}

        self.client.force_login(self.poster)
        with patch("plusone.services.chat.moderate_text", side_effect=close_during_moderation):
            response = self.client.post(
                reverse("chat_messages", args=[self.match.id]),
                {"message": "See you there"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(ChatMessage.objects.filter(match=self.match).exists())
        self.assertFalse(ProductEvent.objects.filter(match=self.match, name=ProductEvent.Name.MESSAGE_SENT).exists())

    def test_nonparticipant_is_rejected_before_moderation(self):
        with patch("plusone.services.chat.moderate_text") as moderate:
            message, result = create_chat_message(self.match, self.outsider, "hello")

        self.assertIsNone(message)
        self.assertTrue(result["unavailable"])
        moderate.assert_not_called()


class LLMClientConfigurationTests(TestCase):
    @patch("openai.OpenAI")
    def test_client_uses_bounded_timeout_and_retry_configuration(self, openai_client):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-key",
                "PLUSONE_LLM_TIMEOUT_SECONDS": "12.5",
                "PLUSONE_LLM_MAX_RETRIES": "2",
            },
            clear=False,
        ):
            llm_client()

        openai_client.assert_called_once()
        kwargs = openai_client.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 12.5)
        self.assertEqual(kwargs["max_retries"], 2)

    @patch("openai.OpenAI")
    def test_client_falls_back_for_invalid_timeout_and_retry_values(self, openai_client):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-key",
                "PLUSONE_LLM_TIMEOUT_SECONDS": "invalid",
                "PLUSONE_LLM_MAX_RETRIES": "-1",
            },
            clear=False,
        ):
            llm_client()

        kwargs = openai_client.call_args.kwargs
        self.assertEqual(kwargs["timeout"], DEFAULT_LLM_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["max_retries"], DEFAULT_LLM_MAX_RETRIES)


class DataRetentionTests(TestCase):
    def test_cleanup_uses_independent_retention_windows(self):
        User = get_user_model()
        user = User.objects.create_user(username="retention_user")
        old_log = LLMLog.objects.create(
            user=user,
            task_type=LLMLog.TaskType.MODERATION,
            input_text="old input",
        )
        fresh_log = LLMLog.objects.create(
            user=user,
            task_type=LLMLog.TaskType.MODERATION,
            input_text="fresh input",
        )
        old_event = ProductEvent.objects.create(name=ProductEvent.Name.PUBLISH_CARD, user=user)
        fresh_event = ProductEvent.objects.create(name=ProductEvent.Name.PUBLISH_CARD, user=user)
        LLMLog.objects.filter(id=old_log.id).update(created_at=timezone.now() - timedelta(days=31))
        ProductEvent.objects.filter(id=old_event.id).update(created_at=timezone.now() - timedelta(days=91))

        call_command("cleanup_anonymous_sessions", stdout=StringIO())
        self.assertTrue(LLMLog.objects.filter(id=old_log.id).exists())
        self.assertTrue(ProductEvent.objects.filter(id=old_event.id).exists())

        call_command("cleanup_anonymous_sessions", "--commit", stdout=StringIO())
        self.assertFalse(LLMLog.objects.filter(id=old_log.id).exists())
        self.assertFalse(ProductEvent.objects.filter(id=old_event.id).exists())
        self.assertTrue(LLMLog.objects.filter(id=fresh_log.id).exists())
        self.assertTrue(ProductEvent.objects.filter(id=fresh_event.id).exists())


class DemoSeedSafetyTests(TestCase):
    @override_settings(DEBUG=False)
    def test_reset_is_blocked_outside_debug_mode(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", "--reset", stdout=StringIO())

    @override_settings(DEBUG=True)
    def test_reset_preserves_non_demo_data_and_shared_locations(self):
        call_command("seed_demo", stdout=StringIO())
        User = get_user_model()
        real_user = User.objects.create_user(username="real_user")
        location = CampusLocation.objects.get(name="Main Library")
        real_post = ActivityPost.objects.create(
            user=real_user,
            title="Real study group",
            activity_type=ActivityPost.ActivityType.STUDY,
            location=location,
            start_time=timezone.now() + timedelta(hours=1),
            expire_time=timezone.now() + timedelta(minutes=45),
        )
        real_log = LLMLog.objects.create(
            user=real_user,
            task_type=LLMLog.TaskType.MODERATION,
            input_text="real input",
        )
        demo_user = User.objects.get(username="demo_alex")
        demo_log = LLMLog.objects.create(
            user=demo_user,
            task_type=LLMLog.TaskType.MODERATION,
            input_text="demo input",
        )
        demo_event = ProductEvent.objects.create(
            name=ProductEvent.Name.PUBLISH_CARD,
            user=demo_user,
            post=demo_user.activity_posts.first(),
        )

        call_command("seed_demo", "--reset", stdout=StringIO())

        self.assertTrue(User.objects.filter(id=real_user.id).exists())
        self.assertTrue(ActivityPost.objects.filter(id=real_post.id).exists())
        self.assertTrue(LLMLog.objects.filter(id=real_log.id).exists())
        self.assertTrue(CampusLocation.objects.filter(id=location.id).exists())
        self.assertFalse(LLMLog.objects.filter(id=demo_log.id).exists())
        self.assertFalse(ProductEvent.objects.filter(id=demo_event.id).exists())
        self.assertEqual(User.objects.filter(username__in=("demo_alex", "demo_blair")).count(), 2)
