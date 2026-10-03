"""Cross-user, tab-ordering and calendar rules for real matching sessions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from plusone.models import ActivityPost, Match, MeetupAction, PresenceLease, ProductEvent, Swipe, UserBlock
from plusone.selectors import dashboard_context_for_user, discover_context_for_user
from plusone.services.chat import close_match, report_match
from plusone.services.lifecycle import locked_match, phase_payload, set_presence
from plusone.services.matching import handle_swipe
from plusone.services.meetups import initialize_plan_locked, perform_meetup_action, plan_payload
from plusone.services.requests import RequestError
from plusone.tests_meetups import MeetupFixtures


class AdditionalPlanFixtures(MeetupFixtures):
    def second_plan(self, *, status=Match.Status.CHATTING, start=None, end=None):
        post = ActivityPost.objects.create(
            user=self.outsider, title="Another public study", activity_type="study", location=self.location,
            start_time=start or self.post.start_time, expected_end_time=end or self.post.expected_end_time,
            expire_time=self.post.expire_time, status=ActivityPost.Status.MATCHED,
        )
        match = Match.objects.create(
            post=post, poster=self.outsider, swiper=self.swiper, status=status,
            chat_expires_at=self.now + timedelta(minutes=5) if status == Match.Status.CHATTING else None,
            waiting_expires_at=self.now + timedelta(minutes=10) if status == Match.Status.WAITING else None,
        )
        with locked_match(match.pk) as current:
            initialize_plan_locked(current)
        match.refresh_from_db()
        return match


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class PresenceLeaseTests(AdditionalPlanFixtures, TestCase):
    def wait(self):
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.Status.WAITING, chat_started_at=None, chat_expires_at=None,
            waiting_expires_at=self.now + timedelta(minutes=10),
        )
        self.match.refresh_from_db()

    def test_hiding_one_tab_keeps_other_visible_tab_present(self):
        self.wait()
        first, second = uuid4(), uuid4()
        set_presence(self.match.pk, self.poster, True, tab_id=first, sequence=1)
        set_presence(self.match.pk, self.poster, True, tab_id=second, sequence=1)
        set_presence(self.match.pk, self.poster, False, tab_id=second, sequence=2)
        current = set_presence(self.match.pk, self.swiper, True, tab_id=uuid4(), sequence=1)
        self.assertEqual(current.status, Match.Status.CHATTING)
        self.assertTrue(phase_payload(current, self.swiper)["other_present"])

    def test_late_visible_request_after_hide_cannot_start_clock(self):
        self.wait()
        tab = uuid4()
        set_presence(self.match.pk, self.poster, False, tab_id=tab, sequence=2)
        set_presence(self.match.pk, self.poster, True, tab_id=tab, sequence=1)
        current = set_presence(self.match.pk, self.swiper, True, tab_id=uuid4(), sequence=1)
        self.assertEqual(current.status, Match.Status.WAITING)
        self.assertIsNone(current.chat_expires_at)
        self.assertFalse(phase_payload(current, self.swiper)["other_present"])

    def test_duplicate_does_not_refresh_a_lease_or_hide_a_later_signal(self):
        self.wait()
        tab = uuid4()
        set_presence(self.match.pk, self.poster, True, tab_id=tab, sequence=2)
        lease = PresenceLease.objects.get(match=self.match, user=self.poster, tab_id=tab)
        original = (lease.sequence, lease.last_visible_at, lease.expires_at)
        with patch("plusone.services.lifecycle.timezone.now", return_value=self.now + timedelta(seconds=10)):
            set_presence(self.match.pk, self.poster, False, tab_id=tab, sequence=2)
        lease.refresh_from_db()
        self.assertTrue(lease.visible)
        self.assertEqual((lease.sequence, lease.last_visible_at, lease.expires_at), original)

    def test_expired_lease_does_not_count_as_presence_and_legacy_client_still_works(self):
        self.wait()
        set_presence(self.match.pk, self.poster, True, tab_id=uuid4(), sequence=1)
        with patch("plusone.services.lifecycle.timezone.now", return_value=self.now + timedelta(seconds=16)):
            current = set_presence(self.match.pk, self.swiper, True)
            self.assertEqual(current.status, Match.Status.WAITING)
            self.assertFalse(phase_payload(current, self.swiper)["other_present"])
            current = set_presence(self.match.pk, self.poster, True)
            self.assertEqual(current.status, Match.Status.CHATTING)

    def test_invalid_modern_presence_input_is_rejected_without_a_lease(self):
        self.wait()
        for tab, sequence in (("bad", 1), (uuid4(), 0), (uuid4(), None), (None, 1), (uuid4(), True)):
            with self.subTest(tab=tab, sequence=sequence), self.assertRaises(RequestError):
                set_presence(self.match.pk, self.poster, True, tab_id=tab, sequence=sequence)
        self.assertFalse(PresenceLease.objects.exists())

    def test_busy_wait_keeps_original_deadline_then_starts_when_first_chat_closes(self):
        waiting = self.second_plan(status=Match.Status.WAITING)
        deadline = waiting.waiting_expires_at
        guest_tab, publisher_tab = uuid4(), uuid4()
        set_presence(waiting.pk, self.swiper, True, tab_id=guest_tab, sequence=1)
        current = set_presence(waiting.pk, self.outsider, True, tab_id=publisher_tab, sequence=1)
        self.assertEqual(current.status, Match.Status.WAITING)
        self.assertEqual(current.waiting_expires_at, deadline)
        self.assertIsNone(current.chat_expires_at)
        publisher = phase_payload(current, self.outsider)
        self.assertTrue(publisher["other_busy"])
        self.assertEqual(publisher["waiting_reason"], "other_busy")
        close_match(self.match.pk, self.swiper, Match.CloseReason.DECLINED)
        current = set_presence(waiting.pk, self.outsider, True, tab_id=publisher_tab, sequence=2)
        self.assertEqual(current.status, Match.Status.CHATTING)
        self.assertEqual(current.waiting_expires_at, deadline)
        self.assertEqual(current.chat_expires_at - current.chat_started_at, timedelta(minutes=5))
        self.assertEqual(Match.objects.filter(swiper=self.swiper, status=Match.Status.CHATTING).count(), 1)

    def test_busy_wait_still_expires_without_extending_deadline(self):
        waiting = self.second_plan(status=Match.Status.WAITING)
        original = waiting.waiting_expires_at
        with patch("plusone.services.lifecycle.timezone.now", return_value=original):
            current = set_presence(waiting.pk, self.swiper, True, tab_id=uuid4(), sequence=1)
        self.assertEqual(current.status, Match.Status.EXPIRED)
        self.assertEqual(current.waiting_expires_at, original)
        self.assertIsNone(current.chat_started_at)
        event = ProductEvent.objects.get(name=ProductEvent.Name.WAIT_CLOSED, match=waiting)
        self.assertEqual(event.properties["reason"], "timeout")


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupScheduleAndOutcomeTests(AdditionalPlanFixtures, TestCase):
    def test_failed_same_uuid_moderation_retries_are_bounded_by_actual_attempts(self):
        key, values = uuid4(), self.plan_values()
        with patch("plusone.services.requests.timezone.now", return_value=self.now), patch(
            "plusone.services.meetups.moderate_text", return_value={"service_unavailable": True},
        ) as moderate:
            for _ in range(6):
                self.assert_rejected("update_plan", request_id=key, status=503, **values)
            self.assert_rejected("update_plan", request_id=key, status=429, **values)
        self.assertEqual(moderate.call_count, 6)
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_revision, 1)
        self.assertFalse(MeetupAction.objects.exists())

    def test_browser_budget_callback_follows_prechecks_and_accepted_replay_is_free(self):
        callback, key, values = Mock(), uuid4(), self.plan_values()
        with patch("plusone.services.meetups.moderate_text", return_value={"flagged": False}) as moderate:
            accepted = self.action("update_plan", request_id=key, before_moderation=callback, **values)
            replayed = self.action("update_plan", request_id=key, revision=1, before_moderation=callback, **values)
            self.assertFalse(accepted["replayed"])
            self.assertTrue(replayed["replayed"])
            self.assert_rejected("update_plan", revision=1, before_moderation=callback, status=409, **values)
        callback.assert_called_once_with()
        moderate.assert_called_once()

    def test_budget_callback_denial_prevents_moderation_and_plan_mutation(self):
        callback = Mock(side_effect=RequestError("Browser budget reached.", 429, 30))
        with patch("plusone.services.meetups.moderate_text") as moderate:
            self.assert_rejected("update_plan", before_moderation=callback, status=429, **self.plan_values())
        moderate.assert_not_called()
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_revision, 1)
        self.assertFalse(MeetupAction.objects.exists())

    def test_overlapping_confirmed_meetup_blocks_consent_in_either_role(self):
        self.agree()
        second = self.second_plan()
        for actor in (self.outsider, self.swiper):
            payload = plan_payload(second, actor)
            self.assertTrue(payload["schedule_conflict"])
            self.assertFalse(payload["can_confirm"])
            with self.assertRaises(RequestError) as error:
                perform_meetup_action(second.pk, actor, "confirm_plan", uuid4(), 1)
            self.assertEqual(error.exception.status, 409)
        second.refresh_from_db()
        self.assertFalse(second.poster_agreed)
        self.assertFalse(second.swiper_agreed)
        self.assertFalse(MeetupAction.objects.filter(match=second).exists())
        # Also cover a shared user switching from guest to publisher.
        ActivityPost.objects.filter(pk=second.post_id).update(user=self.swiper)
        Match.objects.filter(pk=second.pk).update(poster=self.swiper, swiper=self.outsider)
        second.refresh_from_db()
        self.assertTrue(plan_payload(second, self.swiper)["viewer_schedule_conflict"])

    def test_back_to_back_and_cancelled_meetups_do_not_block_confirmation(self):
        self.agree()
        second = self.second_plan(start=self.match.plan_expected_end_at,
                                  end=self.match.plan_expected_end_at + timedelta(hours=1))
        self.assertFalse(plan_payload(second, self.swiper)["schedule_conflict"])
        perform_meetup_action(second.pk, self.swiper, "confirm_plan", uuid4(), 1)
        third = self.second_plan()
        self.action("cancel_meetup")
        self.assertFalse(plan_payload(third, self.swiper)["schedule_conflict"])
        perform_meetup_action(third.pk, self.swiper, "confirm_plan", uuid4(), 1)

    def test_negative_feedback_waits_for_end_but_positive_feedback_can_start_at_meeting(self):
        self.agree()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_meeting_at):
            payload = plan_payload(self.match, self.poster)
            self.assertTrue(payload["can_met"])
            self.assertFalse(payload["can_not_met"])
            self.assert_rejected("outcome", outcome="not_met", outcome_reason="no_show", status=409)
            self.action("outcome", outcome="met")
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at):
            self.assertTrue(plan_payload(self.match, self.swiper)["can_not_met"])
            self.action("outcome", self.swiper, outcome="not_met", outcome_reason="no_show")

    def test_cancelled_future_meetup_allows_negative_feedback_immediately_only(self):
        self.agree()
        self.action("cancel_meetup")
        self.match.refresh_from_db()
        payload = plan_payload(self.match, self.poster)
        self.assertTrue(payload["can_not_met"])
        self.assertFalse(payload["can_met"])
        self.assert_rejected("outcome", outcome="met", status=409)
        self.action("outcome", outcome="not_met", outcome_reason="cancelled")

    def test_ended_report_preserves_history_and_does_not_fabricate_cancellation(self):
        self.agree()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at):
            report_match(self.match.pk, self.poster, category="harassment", reason="Concern after meeting")
            self.match.refresh_from_db()
            self.assertEqual(plan_payload(self.match, self.poster)["meetup_status"], "finished")
            self.assertEqual(len(dashboard_context_for_user(self.poster)["finished_matches"]), 1)
        self.assertIsNone(self.match.meetup_cancelled_at)
        self.assertFalse(ProductEvent.objects.filter(name=ProductEvent.Name.MEETUP_CANCELLED, match=self.match).exists())
        self.assertTrue(UserBlock.objects.filter(blocker=self.poster, target=self.swiper).exists())


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class BlockAndMatchingTests(AdditionalPlanFixtures, TestCase):
    def public_second(self):
        second = self.second_plan(status=Match.Status.DECLINED)
        ActivityPost.objects.filter(pk=second.post_id).update(status=ActivityPost.Status.ACTIVE)
        return second.post

    @override_settings(PLUSONE_NEW_MATCHES_ENABLED=False)
    def test_disabled_matching_leaves_discover_card_available_for_retry(self):
        post = self.public_second()
        result = handle_swipe(self.poster, post.pk, "interested")
        self.assertEqual(result.outcome, "try_again")
        self.assertFalse(Swipe.objects.filter(post=post, user=self.poster).exists())
        self.assertTrue(discover_context_for_user(self.poster, {})["posts"].filter(pk=post.pk).exists())

    def test_either_direction_block_hides_card_and_refuses_stale_interest(self):
        post = self.public_second()
        for blocker, target in ((self.poster, self.outsider), (self.outsider, self.poster)):
            with self.subTest(blocker=blocker.pk):
                UserBlock.objects.all().delete()
                UserBlock.objects.create(blocker=blocker, target=target)
                self.assertFalse(discover_context_for_user(self.poster, {})["posts"].filter(pk=post.pk).exists())
                result = handle_swipe(self.poster, post.pk, "interested")
                self.assertEqual(result.outcome, "inactive_post")
                self.assertFalse(Match.objects.filter(post=post, swiper=self.poster).exists())
                self.assertFalse(Swipe.objects.filter(post=post, user=self.poster).exists())

    def test_manual_block_closes_live_pair_when_domain_state_is_refreshed(self):
        UserBlock.objects.create(blocker=self.poster, target=self.swiper)
        current = set_presence(self.match.pk, self.swiper, True, tab_id=uuid4(), sequence=1)
        self.assertEqual(current.status, Match.Status.EXPIRED)
        self.assertEqual(current.close_reason, Match.CloseReason.BLOCKED)


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupScheduleConcurrencyTests(AdditionalPlanFixtures, TransactionTestCase):
    @skipUnless(connection.features.has_select_for_update, "Requires PostgreSQL user locks")
    def test_competing_waiting_rooms_cannot_start_two_chats_for_the_same_user(self):
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.Status.WAITING, chat_started_at=None, chat_expires_at=None,
            waiting_expires_at=self.now + timedelta(minutes=10),
        )
        second = self.second_plan(status=Match.Status.WAITING)
        set_presence(self.match.pk, self.swiper, True, tab_id=uuid4(), sequence=1)
        set_presence(second.pk, self.swiper, True, tab_id=uuid4(), sequence=1)
        barrier = Barrier(2)

        def activate(values):
            match_id, user_id = values
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user_id)
                barrier.wait(timeout=10)
                return set_presence(match_id, actor, True, tab_id=uuid4(), sequence=1).status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(activate, [(self.match.pk, self.poster.pk), (second.pk, self.outsider.pk)]))
        self.assertCountEqual(results, [Match.Status.CHATTING, Match.Status.WAITING])
        self.assertEqual(Match.objects.filter(swiper=self.swiper, status=Match.Status.CHATTING).count(), 1)

    @skipUnless(connection.features.has_select_for_update, "Requires PostgreSQL user locks")
    def test_two_overlapping_final_confirmations_can_accept_only_one_plan(self):
        second = self.second_plan()
        for match in (self.match, second):
            Match.objects.filter(pk=match.pk).update(poster_agreed=True, poster_agreed_revision=1)
        barrier = Barrier(2)

        def confirm(match_id):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.swiper.pk)
                barrier.wait(timeout=10)
                try:
                    perform_meetup_action(match_id, actor, "confirm_plan", uuid4(), 1)
                    return "accepted"
                except RequestError as error:
                    return error.status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, [self.match.pk, second.pk]))
        self.assertCountEqual(results, ["accepted", 409])
        self.assertEqual(Match.objects.filter(pk__in=[self.match.pk, second.pk], status=Match.Status.AGREED).count(), 1)
