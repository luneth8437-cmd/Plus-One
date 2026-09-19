import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from plusone.ai_services.client import (
    INTERACTIVE_TIMEOUT_SECONDS,
    MODERATION_TIMEOUT_SECONDS,
    llm_client,
    request_timeout_seconds,
)
from plusone.ai_services.moderation import moderate_text, rule_moderate_text
from plusone.ai_services.opening_assistant import generate_openers_from_context
from plusone.ai_services.parsing import parse_activity_text
from plusone.models import LLMLog


def _provider_response(payload):
    message = SimpleNamespace(content=json.dumps(payload))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class RuleModerationTests(TestCase):
    def test_keywords_match_words_not_substrings(self):
        self.assertFalse(rule_moderate_text("Whatever time works for you.")["flagged"])
        self.assertTrue(rule_moderate_text("I will bring a weapon.")["flagged"])

    def test_english_private_contact_request_is_flagged(self):
        for text in (
            "Give me your phone number and home address first.",
            "Could you share your WhatsApp before we meet?",
            "Call me at +1 (415) 555-0123.",
        ):
            with self.subTest(text=text):
                result = rule_moderate_text(text)
                self.assertTrue(result["flagged"])
                self.assertIn("private contact", result["categories"])

    def test_chinese_private_contact_request_is_flagged(self):
        for text in (
            "先给我你的手机号，再把微信号发给我。",
            "你的微信号是什么？",
            "我的手机号是13812345678。",
        ):
            with self.subTest(text=text):
                result = rule_moderate_text(text)
                self.assertTrue(result["flagged"])
                self.assertIn("private contact", result["categories"])

    def test_benign_english_and_chinese_sentences_are_not_flagged(self):
        for text in (
            "Whatever time works; let's meet at the library entrance.",
            "I'll address the scheduling question after class.",
            "我们在图书馆门口见，到了以后再确认座位。",
            "可以带一副备用球拍，我们在公共区域碰面。",
        ):
            with self.subTest(text=text):
                self.assertFalse(rule_moderate_text(text)["flagged"])


class ModerationModeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("moderation_test_user")

    @override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
    def test_rules_mode_is_allowed_only_for_debug(self):
        provider = Mock(side_effect=AssertionError("provider must not be called"))

        result = moderate_text(self.user, "Give me your phone number.", llm_client=provider)

        self.assertTrue(result["flagged"])
        self.assertFalse(result["service_unavailable"])
        provider.assert_not_called()

    @override_settings(DEBUG=False, PLUSONE_MODERATION_MODE="rules")
    def test_rules_mode_in_production_is_unavailable(self):
        provider = Mock(side_effect=AssertionError("provider must not be called"))

        result = moderate_text(self.user, "hello", llm_client=provider)

        self.assertFalse(result["flagged"])
        self.assertTrue(result["service_unavailable"])
        self.assertIn("temporarily unavailable", result["reason"].lower())
        provider.assert_not_called()

    @override_settings(DEBUG=False, PLUSONE_MODERATION_MODE="external")
    def test_missing_provider_key_is_explicitly_unavailable(self):
        result = moderate_text(self.user, "hello", llm_client=lambda: None)

        self.assertEqual(
            result,
            {
                "flagged": False,
                "categories": [],
                "reason": "Safety checking is temporarily unavailable. Please try again.",
                "service_unavailable": True,
            },
        )
        log = LLMLog.objects.latest("id")
        self.assertFalse(log.success)
        self.assertEqual(log.strategy, "provider_unavailable")

    @override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="RULES")
    def test_unknown_mode_is_unavailable_instead_of_silently_using_rules(self):
        provider = Mock(side_effect=AssertionError("provider must not be called"))

        result = moderate_text(self.user, "hello", llm_client=provider)

        self.assertTrue(result["service_unavailable"])
        self.assertFalse(result["flagged"])
        provider.assert_not_called()

    @override_settings(DEBUG=False, PLUSONE_MODERATION_MODE="external")
    def test_provider_timeout_returns_unavailable_and_uses_five_second_budget(self):
        observed = {}

        def timeout_provider(client, config, **kwargs):
            observed.update(kwargs)
            raise TimeoutError("provider timed out")

        result = moderate_text(
            self.user,
            "hello",
            llm_client=lambda: (object(), {"model": "mock", "strategy": "openai"}),
            chat_completion=timeout_provider,
        )

        self.assertEqual(observed["timeout"], MODERATION_TIMEOUT_SECONDS)
        self.assertFalse(result["flagged"])
        self.assertTrue(result["service_unavailable"])
        log = LLMLog.objects.latest("id")
        self.assertFalse(log.success)
        self.assertEqual(log.strategy, "openai_unavailable")

    @override_settings(DEBUG=False, PLUSONE_MODERATION_MODE="external")
    def test_provider_result_is_normalized_without_running_debug_rules(self):
        response = _provider_response(
            {"flagged": True, "categories": ["personal_information"], "reason": "Asks for contact details."}
        )
        completion = Mock(return_value=response)
        with patch(
            "plusone.ai_services.moderation.rule_moderate_text",
            side_effect=AssertionError("rules must not run in provider mode"),
        ):
            result = moderate_text(
                self.user,
                "share contact details",
                llm_client=lambda: (object(), {"model": "mock", "strategy": "openai"}),
                chat_completion=completion,
            )

        self.assertTrue(result["flagged"])
        self.assertFalse(result["service_unavailable"])
        self.assertEqual(completion.call_args.kwargs["timeout"], MODERATION_TIMEOUT_SECONDS)


class ProviderBudgetTests(TestCase):
    @patch("openai.AsyncOpenAI")
    def test_openai_client_disables_sdk_retries(self, openai_client):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "",
                "OPENAI_API_KEY": "test-key",
                "PLUSONE_LLM_MAX_RETRIES": "5",
            },
            clear=False,
        ):
            llm_client()

        self.assertEqual(openai_client.call_args.kwargs["max_retries"], 0)

    def test_task_timeouts_are_configurable_but_capped(self):
        with patch.dict(
            os.environ,
            {
                "PLUSONE_MODERATION_TIMEOUT_SECONDS": "99",
                "PLUSONE_PARSING_TIMEOUT_SECONDS": "99",
                "PLUSONE_OPENERS_TIMEOUT_SECONDS": "99",
            },
            clear=False,
        ):
            self.assertEqual(request_timeout_seconds("moderation"), MODERATION_TIMEOUT_SECONDS)
            self.assertEqual(request_timeout_seconds("parsing"), INTERACTIVE_TIMEOUT_SECONDS)
            self.assertEqual(request_timeout_seconds("opening"), INTERACTIVE_TIMEOUT_SECONDS)

    def test_parser_timeout_is_cancelled_by_sdk_budget_then_falls_back(self):
        observed = {}
        fallback = {
            "title": "Study sprint",
            "description": "Study at 7pm",
            "activity_type": "study",
            "location_name": "Main Library",
            "start_time": "",
            "expire_minutes": 45,
        }

        def timeout_provider(client, config, **kwargs):
            observed.update(kwargs)
            raise TimeoutError("provider timed out")

        user = get_user_model().objects.create_user("parser_budget_user")
        with patch("plusone.ai_services.parsing.rule_parse_activity", return_value=fallback.copy()):
            result = parse_activity_text(
                user,
                "Study at 7pm",
                llm_client=lambda: (object(), {"model": "mock", "strategy": "openai"}),
                chat_completion=timeout_provider,
            )

        self.assertEqual(observed["timeout"], INTERACTIVE_TIMEOUT_SECONDS)
        self.assertEqual(result["title"], "Study sprint")

    def test_opener_timeout_is_cancelled_by_sdk_budget_then_falls_back(self):
        observed = {}
        context = {
            "post": {
                "title": "Study session",
                "activity_type": "study",
                "location": "Main Library",
                "start_time": "",
            },
            "viewer_role": "swiper",
            "viewer": {"major": "", "year": "", "campus_area": "", "interests": ""},
            "partner": {"major": "", "year": "", "campus_area": "", "interests": ""},
            "shared_interests": [],
            "recent_messages": [],
        }

        def timeout_provider(client, config, **kwargs):
            observed.update(kwargs)
            raise TimeoutError("provider timed out")

        openers, strategy = generate_openers_from_context(
            context,
            llm_client=lambda: (object(), {"model": "mock", "strategy": "openai"}),
            chat_completion=timeout_provider,
        )

        self.assertEqual(observed["timeout"], INTERACTIVE_TIMEOUT_SECONDS)
        self.assertGreaterEqual(len(openers), 2)
        self.assertEqual(strategy, "rule_fallback_after_error")
