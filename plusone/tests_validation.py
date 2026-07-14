"""Regression tests for the defensive AI draft guardrails.

These encode the failure modes observed in the 10-session usability test
(docs/product_validation/03_usability_test_report.md): explicit dates that
shifted by one day (U6-U10), drafts missing a required start time (U1-U5),
and out-of-range expiry values (U3).
"""

import os
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .ai_services.parsing import _finalize_draft, rule_parse_activity
from .ai_services.validation import (
    check_publish_date,
    clamp_expire_minutes,
    extract_explicit_date,
    validate_draft,
)
from .models import ActivityPost, CampusLocation


class ExplicitDateExtractionTests(TestCase):
    def setUp(self):
        self.now = timezone.localtime()
        self.today = self.now.date()

    def test_month_day(self):
        target = self.today + timedelta(days=2)
        text = f"badminton on {target.strftime('%B')} {target.day} at 19:00"
        self.assertEqual(extract_explicit_date(text, now=self.now), target)

    def test_day_before_month(self):
        target = self.today + timedelta(days=2)
        text = f"dinner on {target.day} {target.strftime('%B')} at 18:00"
        self.assertEqual(extract_explicit_date(text, now=self.now), target)

    def test_iso_date(self):
        target = self.today + timedelta(days=3)
        self.assertEqual(
            extract_explicit_date(f"meet {target.isoformat()} at 14:00", now=self.now),
            target,
        )

    def test_tomorrow_and_today(self):
        self.assertEqual(
            extract_explicit_date("lunch tomorrow at 12:00", now=self.now),
            self.today + timedelta(days=1),
        )
        self.assertEqual(
            extract_explicit_date("game tonight at 20:00", now=self.now),
            self.today,
        )

    def test_no_date(self):
        self.assertIsNone(extract_explicit_date("study sprint at 15:00", now=self.now))

    def test_past_month_rolls_to_next_year(self):
        past = self.today - timedelta(days=40)
        text = f"reunion on {past.strftime('%B')} {past.day} at 10:00"
        extracted = extract_explicit_date(text, now=self.now)
        self.assertIsNotNone(extracted)
        self.assertGreaterEqual(extracted, self.today - timedelta(days=1))


class ExpiryClampTests(TestCase):
    def test_u3_1440_minutes_is_capped(self):
        clamped, corrected, original = clamp_expire_minutes(1440)
        self.assertEqual(clamped, 180)
        self.assertTrue(corrected)
        self.assertEqual(original, 1440)

    def test_too_small_is_raised_to_minimum(self):
        clamped, corrected, _ = clamp_expire_minutes(1)
        self.assertEqual(clamped, 5)
        self.assertTrue(corrected)

    def test_valid_value_untouched(self):
        clamped, corrected, _ = clamp_expire_minutes(45)
        self.assertEqual(clamped, 45)
        self.assertFalse(corrected)

    def test_garbage_falls_back_to_default(self):
        clamped, corrected, _ = clamp_expire_minutes("soon")
        self.assertEqual(clamped, 45)
        self.assertTrue(corrected)


class RuleDraftRegressionTests(TestCase):
    """U6-U10: explicit dates must survive the deterministic pipeline."""

    def setUp(self):
        CampusLocation.objects.update_or_create(
            name="Campus Sports Hall",
            defaults={
                "location_type": CampusLocation.LocationType.SPORTS,
                "area": "Central Campus",
            },
        )

    def test_explicit_future_date_is_kept(self):
        target = timezone.localtime().date() + timedelta(days=2)
        text = f"Anyone up for badminton on {target.strftime('%B')} {target.day} at 19:00 at the sports hall?"
        draft = _finalize_draft(text, rule_parse_activity(text))
        start = timezone.datetime.fromisoformat(draft["start_time"])
        self.assertEqual(timezone.localtime(start).date(), target)
        self.assertEqual(timezone.localtime(start).strftime("%H:%M"), "19:00")

    def test_missing_time_is_flagged_not_invented(self):
        text = "Tonight around 7 I want to go to the basketball game at the sports hall."
        draft = _finalize_draft(text, rule_parse_activity(text))
        self.assertEqual(draft["start_time"], "")
        codes = [w["code"] for w in draft["validation"]["warnings"]]
        self.assertIn("missing_start_time", codes)

    def test_validate_draft_reports_date_mismatch(self):
        now = timezone.localtime()
        target = now.date() + timedelta(days=2)
        wrong = now.replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=1)
        parsed = {
            "start_time": wrong.isoformat(),
            "expire_minutes": 45,
        }
        text = f"badminton on {target.strftime('%B')} {target.day} at 19:00"
        result = validate_draft(text, parsed, now=now)
        self.assertTrue(result["date_mismatch"])
        codes = [w["code"] for w in result["warnings"]]
        self.assertIn("date_mismatch", codes)

    def test_finalize_aligns_date_to_text(self):
        now = timezone.localtime()
        target = now.date() + timedelta(days=2)
        wrong = now.replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=1)
        draft = {
            "title": "Badminton",
            "description": "x",
            "activity_type": "sports",
            "location_name": "Campus Sports Hall",
            "start_time": wrong.isoformat(),
            "expire_minutes": 1440,
        }
        text = f"badminton on {target.strftime('%B')} {target.day} at 19:00"
        finalized = _finalize_draft(text, draft)
        start = timezone.localtime(timezone.datetime.fromisoformat(finalized["start_time"]))
        self.assertEqual(start.date(), target)
        self.assertEqual(finalized["expire_minutes"], 180)


class PublishDateGuardrailTests(TestCase):
    def setUp(self):
        self.llm_env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
        self.llm_env.start()
        self.addCleanup(self.llm_env.stop)
        User = get_user_model()
        self.user = User.objects.create_user(username="guard", password="pass")
        self.location, _ = CampusLocation.objects.update_or_create(
            name="Campus Sports Hall",
            defaults={
                "location_type": CampusLocation.LocationType.SPORTS,
                "area": "Central Campus",
            },
        )
        self.client.force_login(self.user)

    def _publish_payload(self, start_time, raw_text, confirm=False):
        payload = {
            "action": "publish",
            "raw_text": raw_text,
            "title": "Badminton Session",
            "description": "Intermediate level.",
            "activity_type": ActivityPost.ActivityType.SPORTS,
            "location": self.location.id,
            "start_time": timezone.localtime(start_time).strftime("%Y-%m-%dT%H:%M"),
            "expire_minutes": 60,
        }
        if confirm:
            payload["confirm_date"] = "yes"
        return payload

    def test_publish_blocked_on_date_mismatch_until_confirmed(self):
        now = timezone.localtime()
        text_date = now.date() + timedelta(days=2)
        wrong_start = now.replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=1)
        raw_text = f"badminton on {text_date.strftime('%B')} {text_date.day} at 19:00"

        response = self.client.post(
            reverse("create_post"),
            self._publish_payload(wrong_start, raw_text),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ActivityPost.objects.count(), 0)
        self.assertContains(response, "confirm_date")

        response = self.client.post(
            reverse("create_post"),
            self._publish_payload(wrong_start, raw_text, confirm=True),
        )
        self.assertEqual(ActivityPost.objects.count(), 1)

    def test_publish_passes_when_date_matches_text(self):
        now = timezone.localtime()
        text_date = now.date() + timedelta(days=2)
        start = (now + timedelta(days=2)).replace(hour=19, minute=0, second=0, microsecond=0)
        raw_text = f"badminton on {text_date.strftime('%B')} {text_date.day} at 19:00"
        self.client.post(reverse("create_post"), self._publish_payload(start, raw_text))
        self.assertEqual(ActivityPost.objects.count(), 1)

    def test_check_publish_date_none_without_explicit_date(self):
        start = timezone.now() + timedelta(hours=3)
        self.assertIsNone(check_publish_date("badminton at 19:00", start))
