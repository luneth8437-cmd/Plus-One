from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from plusone.management.commands.funnel_report import build_report
from plusone.models import ActivityPost, CampusLocation, Match, ProductEvent
from plusone.services.analytics import (
    classify_opener_usage, log_discovery_visit, log_event,
    log_interested_result, log_opener_click, log_opener_suggestions,
)


class ProductMetricsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.poster = User.objects.create_user("metrics_publisher")
        self.guest = User.objects.create_user("metrics_guest")
        self.location = CampusLocation.objects.first()

    def post(self, *, old=False):
        start = timezone.now() + timedelta(hours=-50 if old else 2)
        return ActivityPost.objects.create(user=self.poster, title="Metrics acceptance plan", description="",
            activity_type=ActivityPost.ActivityType.STUDY, location=self.location, start_time=start,
            expected_end_time=start + timedelta(hours=1), expire_time=timezone.now() + timedelta(hours=1))

    def match(self, *, old=False, publisher="", guest="", cancelled=False):
        post = self.post(old=old)
        return Match.objects.create(post=post, poster=self.poster, swiper=self.guest,
            status=Match.Status.AGREED, poster_agreed=True, swiper_agreed=True,
            plan_meeting_at=post.start_time, plan_expected_end_at=post.expected_end_time,
            poster_meetup_outcome=publisher, swiper_meetup_outcome=guest,
            meetup_cancelled_at=timezone.now() if cancelled else None)

    def test_discovery_metrics_are_rendered_supply_and_do_not_copy_query_text(self):
        post = self.post()
        log_discovery_visit(self.guest, [post], {"activity_type": "private@example.com", "time_window": "unknown secret"})
        log_discovery_visit(self.poster, [post])
        log_discovery_visit(self.guest, [], {"time_window": "today"})
        report = build_report()["journey"]
        self.assertEqual(report["discover_visits"], 3)
        self.assertEqual(report["empty_visits"], 1)
        self.assertEqual(report["rendered_card_impressions"], 1)
        self.assertIn("server-rendered", report["window_basis"])
        for props in ProductEvent.objects.values_list("properties", flat=True):
            self.assertNotIn("private@example.com", str(props))
            self.assertNotIn("unknown secret", str(props))

    def test_interested_results_preserve_failure_categories_without_arbitrary_text(self):
        post = self.post()
        log_interested_result(self.guest, post, "try_again")
        log_interested_result(self.guest, post, "an untrusted request")
        self.assertEqual(build_report()["journey"]["interested_results"], {"try_again": 1, "unknown": 1})

    def test_repeated_clicks_do_not_inflate_unique_batch_selection_rate(self):
        match = self.match()
        batch = log_opener_suggestions(self.guest, match, [{"text": "Shall we meet at the library entrance?"}],
            metadata={"prompt_version": "acceptance_v1", "model": "rules", "generation": "rule_fallback"})
        batch_id = batch.properties["batch_id"]
        for _ in range(3):
            self.assertIsNotNone(log_opener_click(self.guest, match, 0, batch_id=batch_id))
        self.assertIsNone(log_opener_click(self.poster, match, 0, batch_id=batch_id))
        self.assertIsNone(log_opener_click(self.guest, match, 7, batch_id=batch_id))
        self.assertIsNone(log_opener_click(self.guest, match, 0, batch_id=str(uuid4())))
        report = build_report()["opening_assistant"]
        self.assertEqual(report["suggestion_clicks"], 3)
        self.assertEqual(report["selected_batches"], 1)
        self.assertEqual(report["click_through_pct"], 100.0)
        self.assertEqual(report["by_version"][0]["prompt_version"], "acceptance_v1")

    def test_short_common_prefix_is_not_classified_as_ai_adoption(self):
        match = self.match()
        text = "Hello, a fellow library visitor - meet at the entrance?"
        log_opener_suggestions(self.guest, match, [{"text": text}])
        self.assertEqual(classify_opener_usage(match, self.guest, "Hello"), "none")
        self.assertEqual(classify_opener_usage(match, self.guest, text), "verbatim")
        self.assertEqual(classify_opener_usage(match, self.guest, "Hello, a fellow library visitor! How long are you studying?"), "edited")

    def test_mature_feedback_separates_conflicts_missing_answers_and_cancellation(self):
        self.match(old=True, publisher="met", guest="met")
        self.match(old=True, publisher="not_met", guest="not_met")
        self.match(old=True, publisher="met", guest="not_met")
        self.match(old=True, publisher="met")
        self.match(old=True)
        self.match(old=True, cancelled=True)
        self.match(publisher="met", guest="met")
        report = build_report()["meetup_feedback"]
        self.assertEqual(report["mature_agreements"], 6)
        self.assertEqual(report["future_or_feedback_open"], 1)
        for key in ("both_met", "both_not_met", "conflicting_feedback", "one_sided_feedback", "no_feedback", "cancelled"):
            self.assertEqual(report[key], 1, key)
        self.assertEqual(report["both_met_pct"], 16.7)

    def test_retained_feedback_evidence_survives_business_and_actor_cleanup(self):
        match = self.match(old=True)
        log_event(ProductEvent.Name.BOTH_AGREED, match=match)
        for user in (self.poster, self.guest):
            log_event(ProductEvent.Name.MEETUP_OUTCOME, user=user, match=match, properties={"outcome": "met"})
        match.post.delete()
        self.poster.delete()
        self.guest.delete()
        report = build_report()["meetup_feedback"]
        self.assertEqual(report["mature_agreements"], 1)
        self.assertEqual(report["both_met"], 1)
        self.assertFalse(ProductEvent.objects.filter(user__isnull=False).exists())

    def test_legacy_single_confirmation_does_not_become_a_bilateral_success(self):
        match = self.match(old=True)
        ProductEvent.objects.create(name=ProductEvent.Name.MEETUP_CONFIRMED, match=match, post=match.post,
            user=self.guest, match_reference=match.pk, post_reference=match.post_id, post_created_at=match.post.created_at)
        report = build_report()
        self.assertEqual(report["funnel"]["matches_with_meetup_confirmed"], 1)
        self.assertEqual(report["meetup_feedback"]["one_sided_feedback"], 1)
        self.assertEqual(report["meetup_feedback"]["both_met"], 0)

    def test_unknown_historical_timing_stays_unknown(self):
        ProductEvent.objects.create(name=ProductEvent.Name.BOTH_AGREED, match_reference=987654, post_reference=123456)
        report = build_report()["meetup_feedback"]
        self.assertEqual(report["maturity_unknown"], 1)
        self.assertEqual(report["mature_agreements"], 0)
        self.assertIsNone(report["both_met_pct"])

    def test_wait_closed_event_is_once_per_match(self):
        match = self.match()
        log_event(ProductEvent.Name.WAIT_CLOSED, match=match, properties={"reason": "timeout"})
        log_event(ProductEvent.Name.WAIT_CLOSED, match=match, properties={"reason": "timeout"})
        self.assertEqual(build_report()["journey"]["waiting_closed_reasons"], {"timeout": 1})
