"""A drafted card must preserve written facts and expose unresolved details."""

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .ai_services.parsing import _finalize_draft, parse_activity_text, rule_parse_activity, suggest_ambiguous_time_options
from .models import CampusLocation
from .presenters import post_initial_from_ai
from .management.commands.evaluate_ai import evaluate_parsing


@override_settings(TIME_ZONE="America/Los_Angeles")
class PublishingAccuracyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="accuracy_review")
        self.now = datetime(2026, 10, 3, 17, 0, tzinfo=dt_timezone.utc)
        clock = patch("django.utils.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        for name, location_type in (
            ("Main Library", CampusLocation.LocationType.STUDY),
            ("North Dining Hall", CampusLocation.LocationType.DINING),
            ("Campus Sports Hall", CampusLocation.LocationType.SPORTS),
        ):
            CampusLocation.objects.update_or_create(
                name=name, defaults={"location_type": location_type, "area": "Test campus"},
            )

    def draft(self, text):
        return _finalize_draft(text, rule_parse_activity(text))

    def times(self, draft):
        return tuple(
            timezone.localtime(datetime.fromisoformat(draft[field]))
            for field in ("start_time", "expected_end_time")
        )

    def test_activity_keywords_do_not_invent_a_location(self):
        for activity in ("study", "basketball", "dinner", "mensa", "club", "walk"):
            with self.subTest(activity=activity):
                draft = self.draft(f"Tomorrow at 7pm, {activity} together")
                self.assertEqual(draft["location_name"], "")
                self.assertIsNone(post_initial_from_ai(draft)["location"])
                self.assertIn("location", draft["validation"]["missing_fields"])
                self.assertIn("missing_location", [item["code"] for item in draft["validation"]["warnings"]])

    def test_generic_building_word_used_as_activity_detail_does_not_choose_place(self):
        self.assertEqual(self.draft("Tomorrow at 7pm, read a library book together")["location_name"], "")

    def test_explicit_catalog_place_and_unambiguous_alias_are_preserved(self):
        for place, expected in (("Main Library", "Main Library"), ("sports hall", "Campus Sports Hall")):
            draft = self.draft(f"Tomorrow at 7pm at {place}")
            self.assertEqual(draft["location_name"], expected)
            self.assertEqual(draft["field_sources"]["location"], "user_text")

    def test_shared_alias_and_multiple_named_places_stay_unresolved(self):
        CampusLocation.objects.create(name="South Library", location_type="study", area="Other test area")
        for places in ("library", "Main Library or North Dining Hall"):
            draft = self.draft(f"Tomorrow at 7pm, meet at {places}")
            self.assertEqual(draft["location_name"], "")

    def test_presenter_does_not_guess_a_partial_or_unknown_place(self):
        for location in ("", "Library", "Unknown courtyard"):
            self.assertIsNone(post_initial_from_ai({"location_name": location})["location"])

    def test_explicit_end_is_preserved_in_parser_and_review_form(self):
        draft = self.draft("Tomorrow study at Main Library from 19:00 until 22:00")
        start, end = self.times(draft)
        self.assertEqual((start.hour, end.hour), (19, 22))
        self.assertEqual(draft["field_sources"]["expected_end_time"], "user_text")
        self.assertTrue(post_initial_from_ai(draft)["expected_end_time"].endswith("22:00"))

    def test_mixed_clock_notation_does_not_use_end_as_start(self):
        draft = self.draft("Tomorrow at Main Library, start at 19:00 and end at 10pm")
        start, end = self.times(draft)
        self.assertEqual((start.hour, end.hour), (19, 22))

    def test_clear_clock_range_uses_shared_period(self):
        for request in ("Tomorrow at Main Library, 7–9pm", "Tomorrow at Main Library, 7pm to 9"):
            with self.subTest(request=request):
                start, end = self.times(self.draft(request))
                self.assertEqual((start.hour, end.hour), (19, 21))

    def test_duration_becomes_user_written_end(self):
        draft = self.draft("Tomorrow at 7pm, study at Main Library for 90 minutes")
        start, end = self.times(draft)
        self.assertEqual(end - start, timedelta(minutes=90))
        self.assertEqual(draft["field_sources"]["expected_end_time"], "user_text")

    def test_chinese_clock_end_and_relative_date_are_preserved(self):
        draft = self.draft("明晚在 Main Library 学习，晚上7点到22点结束")
        start, end = self.times(draft)
        self.assertEqual(start.date(), timezone.localtime(self.now).date() + timedelta(days=1))
        self.assertEqual((start.hour, end.hour), (19, 22))
        shared_period = self.draft("明晚在 Main Library 学习，晚上7点到9点")
        self.assertEqual(self.times(shared_period)[1].hour, 21)

    def test_clear_overnight_end_keeps_next_day(self):
        draft = self.draft("Tomorrow at Main Library, 10pm until 1am")
        start, end = self.times(draft)
        self.assertEqual(end - start, timedelta(hours=3))
        self.assertEqual(end.date(), start.date() + timedelta(days=1))

    def test_unresolved_or_invalid_written_end_is_not_replaced_by_one_hour(self):
        for ending in ("until late", "ends at 6pm", "for 0 hours", "for 999999999999999999 hours"):
            draft = self.draft(f"Tomorrow at Main Library at 7pm {ending}")
            self.assertEqual(draft["expected_end_time"], "")
            self.assertEqual(post_initial_from_ai(draft)["expected_end_time"], "")
            self.assertIn("expected_end_time", draft["validation"]["missing_fields"])
            self.assertTrue(draft["end_time_warning"])

    def test_absent_end_is_explicitly_a_suggestion(self):
        draft = self.draft("Tomorrow at Main Library at 7pm")
        start, end = self.times(draft)
        self.assertEqual(end - start, timedelta(hours=1))
        self.assertEqual(draft["field_sources"]["expected_end_time"], "suggested")
        self.assertIn("suggested_end_time", [warning["code"] for warning in draft["validation"]["warnings"]])

    def test_end_only_or_ambiguous_start_does_not_become_a_start(self):
        for request in ("Tomorrow at Main Library until 10pm", "Tomorrow at Main Library at 7 until 10pm"):
            self.assertEqual(self.draft(request)["start_time"], "")

    def test_ambiguous_start_choices_keep_explicit_day_even_with_clear_end(self):
        options = suggest_ambiguous_time_options("October 5 at 7 until 10pm at Main Library")
        self.assertEqual([item["value"] for item in options], ["2026-10-05T07:00", "2026-10-05T19:00"])

    def test_written_calendar_year_and_chinese_calendar_date_are_preserved(self):
        for request in ("October 5, 2027 at 7pm at Main Library until 9pm", "2027年10月5日在 Main Library 晚上7点到9点"):
            start, end = self.times(self.draft(request))
            self.assertEqual(start.date().isoformat(), "2027-10-05")
            self.assertEqual(end.date(), start.date())

    def test_date_alignment_preserves_the_written_activity_duration(self):
        wrong_start = timezone.localtime(self.now).replace(hour=19)
        draft = {
            "start_time": wrong_start.isoformat(),
            "expected_end_time": (wrong_start + timedelta(hours=3)).isoformat(),
            "location_name": "Main Library", "expire_minutes": 45,
        }
        aligned = _finalize_draft("October 5 at 7pm until 10pm at Main Library", draft)
        start, end = self.times(aligned)
        self.assertEqual(start.date().isoformat(), "2026-10-05")
        self.assertEqual(end - start, timedelta(hours=3))

    def test_llm_cannot_invent_place_or_replace_explicit_start_and_end(self):
        observed = {}

        def model_response(client, config, **kwargs):
            observed.update(kwargs)
            payload = {
                "title": "Study together", "activity_type": "study", "location_name": "Main Library",
                "start_time": "2026-10-04T18:00:00-07:00", "expected_end_time": "2026-10-04T20:00:00-07:00",
                "expire_minutes": 60, "field_sources": {"location": "user_text"},
            }
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

        draft = parse_activity_text(
            self.user, "Tomorrow study from 19:00 until 22:00", llm_client=lambda: (object(), {"model": "test", "strategy": "test"}),
            chat_completion=model_response,
        )
        start, end = self.times(draft)
        self.assertEqual((start.hour, end.hour), (19, 22))
        self.assertEqual(draft["location_name"], "")
        self.assertEqual(draft["field_sources"]["location"], "missing")
        self.assertEqual(draft["expire_minutes"], 60)
        self.assertIn("expected_end_time", observed["messages"][0]["content"])

    def test_configured_local_timezone_is_used(self):
        draft = self.draft("Tomorrow at 7pm at Main Library until 9pm")
        start, end = self.times(draft)
        self.assertEqual(start.utcoffset(), timedelta(hours=-7))
        self.assertEqual(end.utcoffset(), timedelta(hours=-7))
        self.assertEqual(start.date().isoformat(), "2026-10-04")

    def test_current_deterministic_benchmark_covers_preserved_end_and_provenance(self):
        report = evaluate_parsing(self.draft)
        self.assertEqual(report["suite_version"], "2026-10-publishing-accuracy")
        self.assertEqual(report["failed_case_count"], 0, report["cases"])
        self.assertGreater(report["fields"]["end_duration"]["total"], 0)
        self.assertGreater(report["fields"]["source_expected_end_time"]["total"], 0)

    def test_benchmark_detects_wrong_end_and_false_suggestion_provenance(self):
        def broken_parser(text):
            draft = self.draft(text)
            if draft["start_time"]:
                draft["expected_end_time"] = (
                    datetime.fromisoformat(draft["start_time"]) + timedelta(hours=1)
                ).isoformat()
                draft["field_sources"]["expected_end_time"] = "suggested"
            return draft

        report = evaluate_parsing(broken_parser)
        self.assertGreater(report["failed_case_count"], 0)
        self.assertLess(report["fields"]["end_duration"]["correct"], report["fields"]["end_duration"]["total"])
        self.assertLess(report["fields"]["source_expected_end_time"]["correct"],
                        report["fields"]["source_expected_end_time"]["total"])
