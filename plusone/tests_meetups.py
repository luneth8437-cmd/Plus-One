"""Domain regressions for revisioned plans and post-agreement coordination."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from plusone.models import ActivityPost, CampusLocation, Match, MeetupAction, RateLimitBucket, UserProfile
from plusone.services.capacity import sync_post_status_for_capacity
from plusone.services.chat import record_agreement
from plusone.services.cleanup import stale_anonymous_users
from plusone.services.lifecycle import locked_match
from plusone.services.meetups import initialize_plan_locked, perform_meetup_action, plan_payload
from plusone.services.requests import RequestError


class MeetupFixtures:
    def setUp(self):
        super().setUp()
        self.now = timezone.now().replace(microsecond=0)
        User = get_user_model()
        self.poster = User.objects.create_user("anon_plan_poster")
        self.swiper = User.objects.create_user("anon_plan_swiper")
        self.outsider = User.objects.create_user("anon_plan_outsider")
        self.location = CampusLocation.objects.create(
            name="Plan test library", location_type="study", area="Campus",
        )
        self.post = ActivityPost.objects.create(
            user=self.poster, title="Quiet study", description="Study in public",
            activity_type="study", location=self.location,
            start_time=self.now + timedelta(minutes=20),
            expected_end_time=self.now + timedelta(minutes=80),
            expire_time=self.now + timedelta(hours=2), status=ActivityPost.Status.MATCHED,
        )
        self.match = Match.objects.create(
            post=self.post, poster=self.poster, swiper=self.swiper,
            status=Match.Status.CHATTING, chat_started_at=self.now,
            chat_expires_at=self.now + timedelta(minutes=5),
        )
        with locked_match(self.match.pk) as current:
            initialize_plan_locked(current)
        self.match.refresh_from_db()

    def action(self, action, user=None, *, revision=None, request_id=None, **values):
        self.match.refresh_from_db()
        return perform_meetup_action(
            self.match.pk, user or self.poster, action,
            request_id or uuid4(), self.match.plan_revision if revision is None else revision,
            **values,
        )

    def plan_values(self, *, point="Library main entrance", minutes=30):
        return {
            "meeting_point": point,
            "meeting_at": self.now + timedelta(minutes=minutes),
            "expected_end_at": self.now + timedelta(minutes=75),
        }

    def agree(self):
        self.action("confirm_plan", self.poster)
        self.action("confirm_plan", self.swiper)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        return self.match

    def assert_rejected(self, action, *, status=None, user=None, **values):
        with self.assertRaises(RequestError) as error:
            self.action(action, user, **values)
        if status is not None:
            self.assertEqual(error.exception.status, status)
        return error.exception


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupPlanTests(MeetupFixtures, TestCase):
    def test_initial_plan_is_a_snapshot_and_is_not_overwritten_by_card_edits(self):
        point, start, end = (
            self.match.meeting_point, self.match.plan_meeting_at,
            self.match.plan_expected_end_at,
        )
        self.assertTrue(point)
        self.assertEqual(start, self.post.start_time)
        self.assertEqual(end, self.post.expected_end_time)
        ActivityPost.objects.filter(pk=self.post.pk).update(
            start_time=self.now + timedelta(hours=3),
            expected_end_time=self.now + timedelta(hours=4),
        )
        with locked_match(self.match.pk) as current:
            initialize_plan_locked(current)
        self.match.refresh_from_db()
        self.assertEqual((self.match.meeting_point, self.match.plan_meeting_at,
                          self.match.plan_expected_end_at), (point, start, end))

    def test_plan_update_resets_both_confirmations_without_extending_chat(self):
        self.action("confirm_plan", self.poster)
        deadline = self.match.chat_expires_at
        self.action("update_plan", self.swiper, **self.plan_values())
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_revision, 2)
        self.assertEqual(self.match.meeting_point, "Library main entrance")
        self.assertFalse(self.match.poster_agreed)
        self.assertFalse(self.match.swiper_agreed)
        self.assertIsNone(self.match.poster_agreed_revision)
        self.assertIsNone(self.match.swiper_agreed_revision)
        self.assertEqual(self.match.chat_expires_at, deadline)
        self.assertEqual(self.match.status, Match.Status.CHATTING)

    def test_stale_confirmation_does_not_accept_new_terms(self):
        self.action("confirm_plan", self.poster)
        self.action("update_plan", self.swiper, **self.plan_values())
        before = MeetupAction.objects.count()
        self.assert_rejected("confirm_plan", user=self.poster, revision=1, status=409)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.CHATTING)
        self.assertFalse(self.match.poster_agreed)
        self.assertEqual(MeetupAction.objects.count(), before)

    def test_both_people_must_confirm_the_same_current_revision(self):
        self.action("update_plan", **self.plan_values())
        self.action("confirm_plan", self.poster)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.CHATTING)
        self.assertEqual(self.match.poster_agreed_revision, 2)
        self.action("confirm_plan", self.swiper)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertEqual(self.match.swiper_agreed_revision, 2)
        self.assertIsNotNone(self.match.plan_confirmed_at)

    def test_confirmed_plan_cannot_be_edited(self):
        self.agree()
        point = self.match.meeting_point
        self.assert_rejected("update_plan", **self.plan_values())
        self.match.refresh_from_db()
        self.assertEqual(self.match.meeting_point, point)
        self.assertEqual(self.match.plan_revision, 1)

    def test_nonparticipant_cannot_read_or_modify_plan(self):
        for action in ("update_plan", "confirm_plan", "arrived", "cancel_meetup"):
            with self.subTest(action=action):
                values = self.plan_values() if action == "update_plan" else {}
                self.assert_rejected(action, user=self.outsider, status=403, **values)
        with self.assertRaises(RequestError) as error:
            plan_payload(self.match, self.outsider)
        self.assertEqual(error.exception.status, 403)
        self.assertFalse(MeetupAction.objects.exists())

    def test_plan_changes_require_live_chat(self):
        for state in (Match.Status.WAITING, Match.Status.DECLINED, Match.Status.EXPIRED):
            with self.subTest(state=state):
                Match.objects.filter(pk=self.match.pk).update(status=state)
                self.assert_rejected("update_plan", **self.plan_values())
                self.assert_rejected("confirm_plan")
        self.assertFalse(MeetupAction.objects.exists())

    def test_expired_chat_cannot_confirm_or_extend_plan(self):
        Match.objects.filter(pk=self.match.pk).update(
            chat_expires_at=self.now - timedelta(seconds=1),
        )
        self.assert_rejected("confirm_plan")
        self.assert_rejected("update_plan", **self.plan_values())
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.EXPIRED)
        self.assertFalse(self.match.poster_agreed)

    def test_plan_rejects_empty_point_and_inverted_or_past_time_range(self):
        invalid = (
            {**self.plan_values(), "meeting_point": "   "},
            {**self.plan_values(), "meeting_at": self.now - timedelta(minutes=1)},
            {**self.plan_values(), "expected_end_at": self.now + timedelta(minutes=30)},
        )
        for values in invalid:
            with self.subTest(values=values):
                self.assert_rejected("update_plan", **values)
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_revision, 1)
        self.assertFalse(MeetupAction.objects.exists())

    def test_plan_cannot_move_outside_original_activity_window(self):
        for values in (
            {**self.plan_values(), "meeting_at": self.post.start_time - timedelta(minutes=16)},
            {**self.plan_values(), "expected_end_at": self.post.expected_end_time + timedelta(seconds=1)},
        ):
            with self.subTest(values=values):
                self.assert_rejected("update_plan", **values)
        self.assertFalse(MeetupAction.objects.exists())

    def test_unavailable_or_flagged_moderation_keeps_original_plan(self):
        for decision, status in (({"service_unavailable": True}, 503), ({"flagged": True}, 400)):
            with self.subTest(decision=decision):
                with patch("plusone.services.meetups.moderate_text", return_value=decision):
                    self.assert_rejected("update_plan", status=status, **self.plan_values())
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_revision, 1)
        self.assertFalse(MeetupAction.objects.exists())

    def test_competing_edit_during_moderation_cannot_overwrite_new_revision(self):
        def competing_edit(*args, **kwargs):
            with patch("plusone.services.meetups.moderate_text", return_value={"flagged": False}):
                self.action("update_plan", self.swiper, **self.plan_values(point="Other public entrance"))
            return {"flagged": False}

        with patch("plusone.services.meetups.moderate_text", side_effect=competing_edit):
            self.assert_rejected("update_plan", revision=1, status=409, **self.plan_values())
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_revision, 2)
        self.assertEqual(self.match.meeting_point, "Other public entrance")
        self.assertEqual(MeetupAction.objects.count(), 1)

    def test_final_confirmation_during_moderation_freezes_plan(self):
        def confirm_while_moderating(*args, **kwargs):
            self.agree()
            return {"flagged": False}

        original_point = self.match.meeting_point
        with patch("plusone.services.meetups.moderate_text", side_effect=confirm_while_moderating):
            self.assert_rejected("update_plan", **self.plan_values())
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertEqual(self.match.meeting_point, original_point)
        self.assertEqual(self.match.plan_revision, 1)

    def test_accepted_update_replays_after_agreement_without_moderation(self):
        key, values = uuid4(), self.plan_values()
        self.action("update_plan", revision=1, request_id=key, **values)
        self.agree()
        count = MeetupAction.objects.count()
        with patch("plusone.services.meetups.moderate_text") as moderate:
            replay = self.action("update_plan", revision=1, request_id=key, **values)
        moderate.assert_not_called()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["action_result"]["revision"], 2)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertEqual(self.match.plan_revision, 2)
        self.assertEqual(MeetupAction.objects.count(), count)
        self.assert_rejected("update_plan", revision=1, request_id=key, status=409,
                             **self.plan_values(point="Changed entrance"))

    def test_request_key_is_scoped_to_actor_and_rejects_action_conflicts(self):
        key = uuid4()
        self.action("confirm_plan", self.poster, request_id=key)
        self.action("confirm_plan", self.swiper, request_id=key)
        self.assertEqual(MeetupAction.objects.filter(match=self.match, request_id=key).count(), 2)
        self.assert_rejected("arrived", request_id=key, status=409)

    def test_old_accepted_confirmation_replay_cannot_confirm_an_edited_plan(self):
        key = uuid4()
        self.action("confirm_plan", self.poster, revision=1, request_id=key)
        self.action("update_plan", self.swiper, **self.plan_values())
        replay = self.action("confirm_plan", self.poster, revision=1, request_id=key)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["action_result"]["revision"], 1)
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_revision, 2)
        self.assertEqual(self.match.status, Match.Status.CHATTING)
        self.assertFalse(self.match.poster_agreed)
        self.assertIsNone(self.match.poster_agreed_revision)

    def test_unversioned_agreement_only_accepts_untouched_original_revision(self):
        self.assertTrue(record_agreement(self.match.pk, self.poster).recorded)
        self.action("update_plan", self.swiper, **self.plan_values())
        self.assertFalse(record_agreement(self.match.pk, self.poster).recorded)
        self.match.refresh_from_db()
        self.assertFalse(self.match.poster_agreed)
        self.assertEqual(self.match.status, Match.Status.CHATTING)

    def test_point_only_edit_preserves_original_time_if_it_has_just_passed(self):
        original = self.now - timedelta(minutes=1)
        ActivityPost.objects.filter(pk=self.post.pk).update(start_time=original)
        Match.objects.filter(pk=self.match.pk).update(plan_meeting_at=original)
        self.match.refresh_from_db()
        self.action("update_plan", meeting_point="Library public reception",
                    meeting_at=original, expected_end_at=self.match.plan_expected_end_at)
        self.match.refresh_from_db()
        self.assertEqual(self.match.plan_meeting_at, original)
        self.assertEqual(self.match.meeting_point, "Library public reception")
        self.assertEqual(self.match.plan_revision, 2)
        self.assert_rejected("update_plan", meeting_point="Another entrance",
                             meeting_at=original - timedelta(minutes=1),
                             expected_end_at=self.match.plan_expected_end_at)

    def test_point_only_frontend_submission_preserves_original_seconds_after_start(self):
        original_start = self.now.replace(second=20, microsecond=123456)
        original_end = (self.now + timedelta(minutes=80)).replace(second=37, microsecond=654321)
        ActivityPost.objects.filter(pk=self.post.pk).update(
            start_time=original_start, expected_end_time=original_end,
        )
        Match.objects.filter(pk=self.match.pk).update(
            plan_meeting_at=original_start, plan_expected_end_at=original_end,
        )
        self.match.refresh_from_db()
        with patch("plusone.services.meetups.timezone.now", return_value=self.now.replace(second=45)):
            frontend = plan_payload(self.match, self.poster)
            self.action("update_plan", meeting_point="Library public reception",
                        meeting_at=frontend["meeting_at_input"],
                        expected_end_at=frontend["expected_end_at_input"])
        self.match.refresh_from_db()
        self.assertEqual(self.match.meeting_point, "Library public reception")
        self.assertEqual(self.match.plan_meeting_at, original_start)
        self.assertEqual(self.match.plan_expected_end_at, original_end)
        self.assertEqual(self.match.plan_revision, 2)

    def test_plan_moderation_uses_shared_ai_quota_and_accepted_replay_is_free(self):
        first_key, values = uuid4(), self.plan_values()
        with patch("plusone.services.requests.timezone.now", return_value=self.now):
            with patch("plusone.services.meetups.moderate_text", return_value={"flagged": False}) as moderate:
                self.action("update_plan", revision=1, request_id=first_key, **values)
                for _ in range(5):
                    self.action("update_plan", **values)
                self.assertEqual(moderate.call_count, 6)
                self.assert_rejected("update_plan", status=429, **values)
                self.assertEqual(moderate.call_count, 6)
                replay = self.action("update_plan", revision=1, request_id=first_key, **values)
                self.assertTrue(replay["replayed"])
                self.assertEqual(moderate.call_count, 6)
        bucket = RateLimitBucket.objects.get(user=self.poster, scope="ai")
        self.assertEqual(bucket.count, 6)
        self.assertEqual(MeetupAction.objects.count(), 6)


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupCoordinationTests(MeetupFixtures, TestCase):
    def test_post_agreement_actions_are_unavailable_during_chat(self):
        for action, values in (("arrived", {}), ("delayed", {"delay_minutes": 5}),
                               ("cancel_meetup", {}), ("outcome", {"outcome": "met"})):
            with self.subTest(action=action):
                self.assert_rejected(action, **values)
        self.assertFalse(MeetupAction.objects.exists())

    def test_arrival_and_delay_are_independent_per_participant(self):
        self.agree()
        self.action("arrived", self.poster)
        self.action("delayed", self.swiper, delay_minutes=10)
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_arrival_status, "arrived")
        self.assertEqual(self.match.swiper_arrival_status, "delayed")
        self.assertEqual(self.match.poster_delay_minutes, 0)
        self.assertEqual(self.match.swiper_delay_minutes, 10)
        self.action("delayed", self.swiper, delay_minutes=0)
        self.match.refresh_from_db()
        self.assertEqual(self.match.swiper_delay_minutes, 0)

    def test_payload_shows_the_correct_participant_perspective(self):
        self.agree()
        self.action("arrived", self.poster)
        self.action("delayed", self.swiper, delay_minutes=5)
        self.match.refresh_from_db()
        poster = plan_payload(self.match, self.poster)
        swiper = plan_payload(self.match, self.swiper)
        self.assertEqual(poster["viewer_status"], "arrived")
        self.assertEqual(poster["other_status"], "delayed")
        self.assertEqual(swiper["viewer_status"], "delayed")
        self.assertEqual(swiper["other_status"], "arrived")
        self.assertEqual(poster["other_delay_minutes"], 5)
        self.assertEqual(swiper["viewer_delay_minutes"], 5)
        self.assertTrue(poster["viewer_confirmed"])
        self.assertTrue(swiper["other_confirmed"])

    def test_arrival_window_is_thirty_minutes_before_start_through_end(self):
        self.agree()
        start, end = self.match.plan_meeting_at, self.match.plan_expected_end_at
        for instant in (start - timedelta(minutes=30, seconds=1), end + timedelta(seconds=1)):
            with self.subTest(instant=instant), patch("plusone.services.meetups.timezone.now", return_value=instant):
                self.assert_rejected("arrived")
                self.assert_rejected("delayed", delay_minutes=5)
        with patch("plusone.services.meetups.timezone.now", return_value=start - timedelta(minutes=30)):
            self.action("arrived")

    def test_delay_rejects_arbitrary_minutes(self):
        self.agree()
        for minutes in (-1, 1, 15, 100):
            with self.subTest(minutes=minutes):
                self.assert_rejected("delayed", delay_minutes=minutes)
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_delay_minutes, 0)

    def test_cancellation_preserves_agreement_and_never_automatically_relists(self):
        self.agree()
        self.action("cancel_meetup", self.swiper)
        self.match.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertIsNotNone(self.match.meetup_cancelled_at)
        self.assertEqual(self.match.meetup_cancelled_by_id, self.swiper.pk)
        self.assertEqual(self.post.status, ActivityPost.Status.PAUSED)
        self.assertEqual(self.post.held_spots, 0)
        sync_post_status_for_capacity(self.post)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.PAUSED)
        self.assertFalse(ActivityPost.objects.active().filter(pk=self.post.pk).exists())
        self.assert_rejected("arrived")
        self.assert_rejected("delayed", delay_minutes=5)

    def test_cancel_action_replays_after_expired_action_window(self):
        self.agree()
        key = uuid4()
        self.action("cancel_meetup", request_id=key)
        count = MeetupAction.objects.count()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at + timedelta(days=2)):
            self.action("cancel_meetup", request_id=key)
        self.assertEqual(MeetupAction.objects.count(), count)

    def test_new_cancellation_is_not_accepted_after_meetup_end(self):
        self.agree()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at + timedelta(seconds=1)):
            self.assert_rejected("cancel_meetup")
        self.match.refresh_from_db()
        self.assertIsNone(self.match.meetup_cancelled_at)

    def test_only_publisher_can_explicitly_reopen_cancelled_card(self):
        self.agree()
        self.assert_rejected("reopen_card")
        self.action("cancel_meetup", self.swiper)
        self.assert_rejected("reopen_card", user=self.swiper, status=403)
        self.action("reopen_card", self.poster)
        self.post.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.ACTIVE)
        self.assertIsNotNone(self.match.meetup_cancelled_at)
        self.assertEqual(self.match.status, Match.Status.AGREED)

    def test_former_guest_identity_reset_does_not_block_publisher_reopening(self):
        self.agree()
        self.action("cancel_meetup", self.swiper)
        UserProfile.objects.create(user=self.swiper, display_name="Former guest", retired_at=self.now)
        self.match.refresh_from_db()
        self.assertTrue(plan_payload(self.match, self.poster)["can_reopen_card"])
        self.action("reopen_card", self.poster)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.ACTIVE)
        self.assertEqual(self.post.held_spots, 0)

    def test_reopening_cannot_revive_expired_or_started_card(self):
        self.agree()
        self.action("cancel_meetup")
        for values in ({"expire_time": self.now - timedelta(seconds=1)},
                       {"expire_time": self.now + timedelta(hours=2), "start_time": self.now - timedelta(seconds=1)}):
            with self.subTest(values=values):
                ActivityPost.objects.filter(pk=self.post.pk).update(**values)
                self.assert_rejected("reopen_card")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.PAUSED)

    def test_reopening_cannot_override_another_active_hold(self):
        self.agree()
        self.action("cancel_meetup")
        Match.objects.create(
            post=self.post, poster=self.poster, swiper=self.outsider,
            status=Match.Status.WAITING, waiting_expires_at=self.now + timedelta(minutes=5),
        )
        self.assert_rejected("reopen_card")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.PAUSED)

    def test_outcomes_are_independent_with_positive_at_start_and_negative_at_end(self):
        self.agree()
        self.assert_rejected("outcome", outcome="met")
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at + timedelta(hours=24, seconds=1)):
            self.assert_rejected("outcome", outcome="met")
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_meeting_at):
            self.action("outcome", self.poster, outcome="met")
            self.assert_rejected("outcome", user=self.swiper, outcome="not_met", outcome_reason="no_show")
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at):
            self.action("outcome", self.swiper, outcome="not_met", outcome_reason="no_show")
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_meetup_outcome, "met")
        self.assertEqual(self.match.swiper_meetup_outcome, "not_met")

    def test_cancelled_meetup_still_allows_individual_did_not_meet_feedback(self):
        self.agree()
        self.action("cancel_meetup", self.swiper)
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_meeting_at):
            self.action("outcome", self.poster, outcome="not_met", outcome_reason="cancelled")
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_meetup_outcome, "not_met")
        self.assertEqual(self.match.poster_outcome_reason, "cancelled")

    def test_did_not_meet_requires_a_supported_reason(self):
        self.agree()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at):
            self.assert_rejected("outcome", outcome="not_met")
            self.assert_rejected("outcome", outcome="not_met", outcome_reason="unsupported")
            self.action("outcome", outcome="not_met", outcome_reason="time_conflict")
        self.match.refresh_from_db()
        payload = plan_payload(self.match, self.poster)
        self.assertEqual(payload["viewer_outcome_reason"], "time_conflict")
        self.assertEqual(payload["other_outcome_reason"], "")

    def test_retention_uses_confirmed_plan_end_even_if_original_card_has_ended(self):
        self.agree()
        old = self.now - timedelta(days=8)
        for user in (self.poster, self.swiper):
            get_user_model().objects.filter(pk=user.pk).update(date_joined=old, last_login=old)
            UserProfile.objects.create(user=user, display_name="Anonymous", last_seen_at=old)
        ActivityPost.objects.filter(pk=self.post.pk).update(
            start_time=self.now - timedelta(days=3),
            expected_end_time=self.now - timedelta(days=2),
            expire_time=self.now - timedelta(days=2),
        )
        Match.objects.filter(pk=self.match.pk).update(plan_expected_end_at=self.now - timedelta(hours=23))
        participants = [self.poster.pk, self.swiper.pk]
        self.assertFalse(stale_anonymous_users(now=self.now).filter(pk__in=participants).exists())
        Match.objects.filter(pk=self.match.pk).update(plan_expected_end_at=self.now - timedelta(hours=25))
        self.assertEqual(stale_anonymous_users(now=self.now).filter(pk__in=participants).count(), 2)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupConcurrencyTests(MeetupFixtures, TransactionTestCase):
    def test_competing_edit_and_final_confirmation_never_agree_to_mixed_revisions(self):
        self.action("confirm_plan", self.poster)
        barrier = Barrier(2)

        def worker(action, actor, values):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try:
                    return perform_meetup_action(self.match.pk, actor, action, uuid4(), 1, **values)
                except RequestError as error:
                    return error.status
            finally:
                connections.close_all()

        with patch("plusone.services.meetups.moderate_text", return_value={"flagged": False}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                edit = pool.submit(worker, "update_plan", self.poster, self.plan_values())
                confirmation = pool.submit(worker, "confirm_plan", self.swiper, {})
                edit.result(timeout=10)
                confirmation.result(timeout=10)
        self.match.refresh_from_db()
        if self.match.status == Match.Status.AGREED:
            self.assertEqual(self.match.plan_revision, 1)
            self.assertEqual(self.match.poster_agreed_revision, 1)
            self.assertEqual(self.match.swiper_agreed_revision, 1)
        else:
            self.assertEqual(self.match.status, Match.Status.CHATTING)
            self.assertEqual(self.match.plan_revision, 2)
            self.assertFalse(self.match.poster_agreed)
            self.assertFalse(self.match.swiper_agreed)
            self.assertIsNone(self.match.poster_agreed_revision)
            self.assertIsNone(self.match.swiper_agreed_revision)


class MeetupPlanMigrationCompatibilityTests(TransactionTestCase):
    migrate_from = [("plusone", "0014_activitypost_expected_end_time")]
    migrate_to = [("plusone", "0015_versioned_meetup_plan")]

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_latest_schema)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._create_historical_arrangements(old_apps)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def _restore_latest_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _create_historical_arrangements(self, apps):
        app_label, model_name = settings.AUTH_USER_MODEL.split(".")
        User = apps.get_model(app_label, model_name)
        CampusLocation = apps.get_model("plusone", "CampusLocation")
        ActivityPost = apps.get_model("plusone", "ActivityPost")
        Match = apps.get_model("plusone", "Match")
        ChatMessage = apps.get_model("plusone", "ChatMessage")
        ProductEvent = apps.get_model("plusone", "ProductEvent")
        poster = User.objects.create(username="historical_plan_poster")
        swiper = User.objects.create(username="historical_plan_swiper")
        location = CampusLocation.objects.create(
            name="Historical public meeting point", location_type="study", area="Campus",
        )
        now = timezone.now()
        self.arrangements = []
        states = (("agreed", True, True), ("waiting", False, False), ("chatting", True, False))
        for index, (status, poster_agreed, swiper_agreed) in enumerate(states):
            start = now + timedelta(hours=index + 2)
            expiry = now + timedelta(minutes=37 + index)
            post = ActivityPost.objects.create(
                user=poster, title=f"Historical {status} plan", description="Original arrangement",
                activity_type="study", location=location, start_time=start,
                expected_end_time=None, expire_time=expiry, status="matched",
            )
            waiting_deadline = now + timedelta(minutes=7) if status == "waiting" else None
            chat_deadline = now + timedelta(minutes=3, seconds=index) if status != "waiting" else None
            started = now - timedelta(minutes=2) if status != "waiting" else None
            match = Match.objects.create(
                post=post, poster=poster, swiper=swiper, status=status,
                poster_agreed=poster_agreed, swiper_agreed=swiper_agreed,
                waiting_expires_at=waiting_deadline, chat_expires_at=chat_deadline,
                chat_started_at=started,
            )
            self.arrangements.append({
                "post_id": post.pk, "match_id": match.pk, "status": status,
                "point": location.name, "start": start, "expiry": expiry,
                "poster_agreed": poster_agreed, "swiper_agreed": swiper_agreed,
                "waiting_deadline": waiting_deadline, "chat_deadline": chat_deadline,
                "started": started,
            })

        original = self.arrangements[0]
        message = ChatMessage.objects.create(
            match_id=original["match_id"], sender=swiper, message="Historical arrival discussion",
        )
        event = ProductEvent.objects.create(
            name="publish_card", user=poster, post_id=original["post_id"],
            properties={"historical": True},
        )
        self.message_id = message.pk
        self.event_id = event.pk

    def test_upgrade_preserves_original_arrangement_and_does_not_invent_evidence(self):
        Match = self.apps.get_model("plusone", "Match")
        ActivityPost = self.apps.get_model("plusone", "ActivityPost")
        ProductEvent = self.apps.get_model("plusone", "ProductEvent")
        MeetupAction = self.apps.get_model("plusone", "MeetupAction")
        ChatMessage = self.apps.get_model("plusone", "ChatMessage")

        for original in self.arrangements:
            with self.subTest(status=original["status"]):
                match = Match.objects.get(pk=original["match_id"])
                post = ActivityPost.objects.get(pk=original["post_id"])
                self.assertEqual(match.status, original["status"])
                self.assertEqual(match.meeting_point, original["point"])
                self.assertEqual(match.plan_meeting_at, original["start"])
                self.assertIsNone(match.plan_expected_end_at)
                self.assertIsNone(post.expected_end_time)
                self.assertTrue(match.plan_legacy)
                self.assertEqual(match.plan_revision, 1)
                self.assertEqual(match.poster_agreed, original["poster_agreed"])
                self.assertEqual(match.swiper_agreed, original["swiper_agreed"])
                self.assertEqual(match.poster_agreed_revision, 1 if original["poster_agreed"] else None)
                self.assertEqual(match.swiper_agreed_revision, 1 if original["swiper_agreed"] else None)
                self.assertIsNone(match.plan_confirmed_at)
                self.assertIsNone(match.meetup_cancelled_at)
                self.assertEqual(match.waiting_expires_at, original["waiting_deadline"])
                self.assertEqual(match.chat_expires_at, original["chat_deadline"])
                self.assertEqual(match.chat_started_at, original["started"])
                self.assertEqual(post.start_time, original["start"])
                self.assertEqual(post.expire_time, original["expiry"])

        self.assertEqual(list(ProductEvent.objects.values_list("pk", flat=True)), [self.event_id])
        self.assertEqual(ProductEvent.objects.get(pk=self.event_id).properties, {"historical": True})
        self.assertFalse(MeetupAction.objects.exists())
        self.assertEqual(list(ChatMessage.objects.values_list("pk", flat=True)), [self.message_id])
        self.assertEqual(ChatMessage.objects.get(pk=self.message_id).message, "Historical arrival discussion")
        self.assertFalse(ChatMessage.objects.filter(is_system=True).exists())
