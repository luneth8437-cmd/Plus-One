"""Regressions for plans crossing identity, history, and legacy boundaries."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from plusone.models import ActivityPost, ChatMessage, Match, MeetupAction, ProductEvent, UserProfile
from plusone.selectors import dashboard_context_for_user
from plusone.services.analytics import log_event
from plusone.services.chat import confirm_meetup
from plusone.services.cleanup import cleanup_stale_records, stale_anonymous_users
from plusone.services.identity import retire_anonymous_identity
from plusone.services.matching import handle_swipe
from plusone.services.meetups import plan_payload
from plusone.tests_meetups import MeetupFixtures


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupIdentityLinkageTests(MeetupFixtures, TestCase):
    def test_guest_reset_cancels_upcoming_meetup_and_publisher_can_recruit_again(self):
        self.agree()
        retire_anonymous_identity(self.swiper)
        self.match.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertIsNotNone(self.match.meetup_cancelled_at)
        self.assertEqual(self.match.meetup_cancelled_by_id, self.swiper.pk)
        self.assertEqual(self.post.status, ActivityPost.Status.PAUSED)
        self.assertEqual(self.post.held_spots, 0)
        self.assertEqual(ProductEvent.objects.filter(name="meetup_cancelled", match=self.match).count(), 1)
        self.assertTrue(plan_payload(self.match, self.poster)["can_reopen_card"])
        self.action("reopen_card", self.poster)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.ACTIVE)

    def test_publisher_reset_cancels_upcoming_meetup_and_its_public_card(self):
        self.agree()
        retire_anonymous_identity(self.poster)
        self.match.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertIsNotNone(self.match.meetup_cancelled_at)
        self.assertEqual(self.match.meetup_cancelled_by_id, self.poster.pk)
        self.assertEqual(self.post.status, ActivityPost.Status.CANCELLED)
        self.assertFalse(plan_payload(self.match, self.swiper)["can_reopen_card"])
        self.assertEqual(ProductEvent.objects.filter(name="meetup_cancelled", match=self.match).count(), 1)

    def test_identity_reset_after_meetup_end_does_not_fabricate_cancellation(self):
        self.agree()
        event_count = ProductEvent.objects.count()
        message_count = self.match.messages.count()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at + timedelta(seconds=1)):
            retire_anonymous_identity(self.swiper)
            retire_anonymous_identity(self.poster)
        self.match.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertEqual(self.post.status, ActivityPost.Status.CANCELLED)
        self.assertIsNone(self.match.meetup_cancelled_at)
        self.assertIsNone(self.match.meetup_cancelled_by_id)
        self.assertEqual(ProductEvent.objects.count(), event_count)
        self.assertEqual(self.match.messages.count(), message_count)


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupHistoryLinkageTests(MeetupFixtures, TestCase):
    def test_finished_agreement_moves_to_dashboard_history_without_rewriting_match(self):
        self.agree()
        with patch("plusone.selectors.timezone.now", return_value=self.match.plan_expected_end_at + timedelta(seconds=1)):
            context = dashboard_context_for_user(self.poster)
        self.assertEqual(context["handoff_count"], 0)
        self.assertEqual(context["handoff_matches"], [])
        self.assertEqual([match.pk for match in context["finished_matches"]], [self.match.pk])
        closed = next(match for match in context["closed_matches"] if match.pk == self.match.pk)
        self.assertTrue(closed.meetup_finished)
        self.assertNotEqual(context["dashboard_state"]["eyebrow"], "Ready to meet")
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertIsNone(self.match.meetup_cancelled_at)

    def test_confirmed_snapshot_end_controls_payload_and_dashboard_after_card_changes(self):
        self.agree()
        snapshot_end = self.match.plan_expected_end_at
        cases = ((self.now + timedelta(minutes=40), self.now + timedelta(minutes=50), False),
                 (self.now + timedelta(minutes=120), snapshot_end, True))
        for original_end, instant, finished in cases:
            with self.subTest(original_end=original_end, finished=finished):
                ActivityPost.objects.filter(pk=self.post.pk).update(expected_end_time=original_end)
                self.match.refresh_from_db()
                with patch("plusone.services.meetups.timezone.now", return_value=instant):
                    payload = plan_payload(self.match, self.poster)
                    context = dashboard_context_for_user(self.poster)
                self.assertEqual(payload["window_finished"], finished)
                self.assertEqual(payload["meetup_status"], "finished" if finished else "confirmed")
                self.assertEqual(context["handoff_count"], 0 if finished else 1)
                self.assertEqual([match.pk for match in context["finished_matches"]], [self.match.pk] if finished else [])
                self.assertEqual(next(match for match in context["matches"] if match.pk == self.match.pk).meetup_finished, finished)
                self.assertEqual(self.match.plan_expected_end_at, snapshot_end)

    def legacy_confirmation(self):
        self.agree()
        event = log_event(ProductEvent.Name.MEETUP_CONFIRMED, user=self.poster, match=self.match,
                          properties={"historical": True})
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_meetup_outcome, "")
        return event

    def test_historical_confirmation_is_consistent_in_payload_page_and_poll(self):
        event = self.legacy_confirmation()
        count = ProductEvent.objects.count()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_meeting_at + timedelta(minutes=1)):
            poster = plan_payload(self.match, self.poster)
            swiper = plan_payload(self.match, self.swiper)
            self.assertEqual(poster["viewer_outcome"], "met")
            self.assertEqual(swiper["other_outcome"], "met")
            self.assertEqual(swiper["viewer_outcome"], "")
            self.assertFalse(poster["can_outcome"])
            self.client.force_login(self.poster)
            page = self.client.get(reverse("chat", args=[self.match.pk]))
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context["plan"]["viewer_outcome"], "met")
            poll = self.client.get(reverse("chat_messages", args=[self.match.pk]))
            self.assertEqual(poll.status_code, 200)
            self.assertEqual(poll.json()["plan"]["viewer_outcome"], "met")
            self.assertFalse(poll.json()["plan"]["can_outcome"])
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_meetup_outcome, "")
        self.assertEqual(self.match.swiper_meetup_outcome, "")
        self.assertEqual(ProductEvent.objects.count(), count)
        event.refresh_from_db()
        self.assertEqual(event.properties, {"historical": True})

    def test_historical_met_report_cannot_be_overwritten_by_new_negative_feedback(self):
        event = self.legacy_confirmation()
        event_count = ProductEvent.objects.count()
        action_count = MeetupAction.objects.count()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_meeting_at + timedelta(minutes=1)):
            self.assert_rejected("outcome", outcome="not_met", outcome_reason="no_show", status=409)
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_meetup_outcome, "")
        self.assertEqual(self.match.poster_outcome_reason, "")
        self.assertEqual(ProductEvent.objects.count(), event_count)
        self.assertEqual(MeetupAction.objects.count(), action_count)
        self.assertTrue(ProductEvent.objects.filter(pk=event.pk).exists())

    def test_old_confirmation_duplicate_preserves_historical_fields_and_event(self):
        event = self.legacy_confirmation()
        count = ProductEvent.objects.count()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_meeting_at + timedelta(minutes=1)):
            self.assertTrue(confirm_meetup(self.match.pk, self.poster))
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_meetup_outcome, "")
        self.assertEqual(ProductEvent.objects.count(), count)
        event.refresh_from_db()
        self.assertEqual(event.properties, {"historical": True})


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupWindowLinkageTests(MeetupFixtures, TestCase):
    def test_cancelled_feedback_window_protects_stale_participants_and_evidence(self):
        self.agree()
        self.action("cancel_meetup", self.swiper)
        self.match.refresh_from_db()
        deadline = self.match.plan_expected_end_at + timedelta(hours=24)
        old = self.now - timedelta(days=8)
        participant_ids = [self.poster.pk, self.swiper.pk]
        for user in (self.poster, self.swiper):
            get_user_model().objects.filter(pk=user.pk).update(date_joined=old, last_login=old)
            UserProfile.objects.create(user=user, display_name="Stale student", last_seen_at=old,
                                       retired_at=old if user.pk == self.swiper.pk else None)
        evidence = ChatMessage.objects.create(match=self.match, sender=self.poster, message="Existing meetup evidence")
        ChatMessage.objects.filter(pk=evidence.pk).update(created_at=self.now - timedelta(days=91))
        self.post.refresh_from_db()
        self.assertEqual(self.post.held_spots, 0)
        self.assertFalse(ActivityPost.objects.active().filter(pk=self.post.pk).exists())
        for instant in (deadline - timedelta(hours=1), deadline):
            with self.subTest(instant=instant), patch("plusone.services.meetups.timezone.now", return_value=instant):
                self.assertTrue(plan_payload(self.match, self.poster)["can_outcome"])
                counts = cleanup_stale_records(dry_run=False)
            self.assertEqual(counts["users"], 0)
            self.assertEqual(counts["report_messages"], 0)
            self.assertEqual(get_user_model().objects.filter(pk__in=participant_ids).count(), 2)
            self.assertTrue(Match.objects.filter(pk=self.match.pk).exists())
            self.assertTrue(ChatMessage.objects.filter(pk=evidence.pk).exists())
        with patch("plusone.services.meetups.timezone.now", return_value=deadline + timedelta(microseconds=1)):
            self.assertFalse(plan_payload(self.match, self.poster)["can_outcome"])
            counts = cleanup_stale_records(dry_run=False)
        self.assertEqual(counts["users"], 2)
        self.assertFalse(get_user_model().objects.filter(pk__in=participant_ids).exists())
        self.assertFalse(Match.objects.filter(pk=self.match.pk).exists())
        self.assertFalse(ChatMessage.objects.filter(pk=evidence.pk).exists())

    def test_ended_original_plan_cannot_be_confirmed_through_either_endpoint(self):
        deadline = self.match.chat_expires_at
        start, end = self.now - timedelta(hours=2), self.now - timedelta(hours=1)
        ActivityPost.objects.filter(pk=self.post.pk).update(start_time=start, expected_end_time=end)
        Match.objects.filter(pk=self.match.pk).update(plan_meeting_at=start, plan_expected_end_at=end)
        self.assert_rejected("confirm_plan", status=409)
        self.client.force_login(self.poster)
        self.client.post(reverse("chat", args=[self.match.pk]), {"action": "agree"})
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.CHATTING)
        self.assertFalse(self.match.poster_agreed)
        self.assertFalse(self.match.swiper_agreed)
        self.assertEqual(self.match.chat_expires_at, deadline)
        self.assertFalse(MeetupAction.objects.exists())

    def test_ended_card_is_not_discoverable_or_matchable_from_a_stale_page(self):
        Match.objects.filter(pk=self.match.pk).update(status=Match.Status.DECLINED)
        ActivityPost.objects.filter(pk=self.post.pk).update(
            status=ActivityPost.Status.ACTIVE, start_time=self.now - timedelta(hours=2),
            expected_end_time=self.now - timedelta(hours=1), expire_time=self.now + timedelta(minutes=30),
        )
        self.assertFalse(ActivityPost.objects.active().filter(pk=self.post.pk).exists())
        result = handle_swipe(self.outsider, self.post.pk, "interested")
        self.assertEqual(result.outcome, "inactive_post")
        self.assertFalse(Match.objects.filter(post=self.post, swiper=self.outsider).exists())
        ongoing = ActivityPost.objects.create(
            user=self.poster, title="An ongoing plan", activity_type="study", location=self.location,
            start_time=self.now - timedelta(minutes=5), expected_end_time=self.now + timedelta(minutes=20),
            expire_time=self.now + timedelta(minutes=30),
        )
        self.assertTrue(ActivityPost.objects.active().filter(pk=ongoing.pk).exists())

    def test_unknown_legacy_end_feedback_and_cleanup_share_one_boundary(self):
        self.agree()
        start = self.now - timedelta(hours=24, minutes=30)
        ActivityPost.objects.filter(pk=self.post.pk).update(
            start_time=start, expected_end_time=None, expire_time=start - timedelta(minutes=1),
        )
        Match.objects.filter(pk=self.match.pk).update(
            plan_meeting_at=start, plan_expected_end_at=None, plan_legacy=True,
        )
        old = self.now - timedelta(days=8)
        participants = [self.poster.pk, self.swiper.pk]
        for user in (self.poster, self.swiper):
            get_user_model().objects.filter(pk=user.pk).update(date_joined=old, last_login=old)
            UserProfile.objects.create(user=user, display_name="Historical student", last_seen_at=old)
        self.match.refresh_from_db()
        for instant in (self.now, start + timedelta(hours=25)):
            with self.subTest(instant=instant), patch("plusone.services.meetups.timezone.now", return_value=instant):
                self.assertTrue(plan_payload(self.match, self.poster)["can_outcome"])
                self.assertFalse(stale_anonymous_users(now=instant).filter(pk__in=participants).exists())
        expired = start + timedelta(hours=25, microseconds=1)
        with patch("plusone.services.meetups.timezone.now", return_value=expired):
            self.assertFalse(plan_payload(self.match, self.poster)["can_outcome"])
            self.assertEqual(stale_anonymous_users(now=expired).filter(pk__in=participants).count(), 2)
        self.match.refresh_from_db()
        self.post.refresh_from_db()
        self.assertIsNone(self.match.plan_expected_end_at)
        self.assertIsNone(self.post.expected_end_time)
