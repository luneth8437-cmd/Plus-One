"""Waiting recovery and recruiting boundaries, without extending old attempts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from plusone.models import ActivityPost, CampusLocation, Match, PresenceLease, ProductEvent, Swipe, UserBlock, UserProfile
from plusone.selectors import discover_context_for_user
from plusone.services.expiration import refresh_expired_records
from plusone.services.lifecycle import phase_payload, refresh_match, set_presence
from plusone.services.matching import SwipeOutcome, handle_swipe, retry_waiting_match, waiting_retry_state
from plusone.services.requests import RequestError


class WaitingFixtures:
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        User = get_user_model()
        self.poster = User.objects.create_user("waiting_publisher")
        self.swiper = User.objects.create_user("waiting_guest")
        self.outsider = User.objects.create_user("waiting_outsider")
        self.location = CampusLocation.objects.create(name="Waiting test library", location_type="study", area="Test campus")
        self.post = ActivityPost.objects.create(user=self.poster, title="Real study plan", activity_type="study",
            location=self.location, start_time=self.now + timedelta(minutes=15),
            expected_end_time=self.now + timedelta(minutes=60), expire_time=self.now + timedelta(minutes=45))

    def timed_out(self):
        result = handle_swipe(self.swiper, self.post.pk, Swipe.Action.INTERESTED)
        self.assertEqual(result.outcome, SwipeOutcome.MATCH_CREATED)
        Match.objects.filter(pk=result.match_id).update(waiting_expires_at=self.now - timedelta(seconds=1))
        match = refresh_match(result.match_id)
        self.post.refresh_from_db()
        self.assertEqual(match.status, Match.Status.EXPIRED)
        return match


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules", PLUSONE_NEW_MATCHES_ENABLED=True)
class WaitingRecoveryTests(WaitingFixtures, TestCase):
    def test_real_recruiting_deadline_is_earlier_of_expiry_and_activity_end(self):
        self.assertEqual(self.post.matching_deadline, self.post.expire_time)
        self.post.expected_end_time = self.now + timedelta(minutes=3)
        self.assertEqual(self.post.matching_deadline, self.post.expected_end_time)
        self.post.expected_end_time = None
        self.post.start_time = self.now - timedelta(minutes=59)
        self.assertEqual(self.post.matching_deadline, self.post.start_time + timedelta(hours=1))

    def test_new_wait_is_bounded_by_real_activity_end_and_repeated_interest_keeps_clock(self):
        self.post.expected_end_time = self.now + timedelta(minutes=3)
        self.post.save(update_fields=["expected_end_time"])
        result = handle_swipe(self.swiper, self.post.pk, Swipe.Action.INTERESTED)
        match = Match.objects.get(pk=result.match_id)
        self.assertEqual(match.waiting_expires_at, self.post.expected_end_time)
        self.assertEqual(match.phase_deadline, self.post.expected_end_time)
        repeated = handle_swipe(self.swiper, self.post.pk, Swipe.Action.INTERESTED)
        match.refresh_from_db()
        self.assertEqual(repeated.match_id, match.pk)
        self.assertEqual(match.waiting_expires_at, self.post.expected_end_time)

    def test_explicit_retry_preserves_history_and_resets_only_the_new_room(self):
        old = self.timed_out()
        old_deadline = old.waiting_expires_at
        Match.objects.filter(pk=old.pk).update(poster_agreed=True, poster_last_present_at=self.now)
        result = retry_waiting_match(self.poster, old.pk)
        child = Match.objects.get(pk=result.match_id)
        old.refresh_from_db()
        self.assertEqual(result.outcome, SwipeOutcome.MATCH_CREATED)
        self.assertEqual(child.retry_of_id, old.pk)
        self.assertEqual(child.status, Match.Status.WAITING)
        self.assertEqual(old.status, Match.Status.EXPIRED)
        self.assertEqual(old.waiting_expires_at, old_deadline)
        self.assertTrue(old.poster_agreed)
        self.assertFalse(child.poster_agreed)
        self.assertFalse(child.swiper_agreed)
        self.assertIsNone(child.chat_started_at)
        self.assertIsNone(child.poster_last_present_at)
        self.assertFalse(PresenceLease.objects.filter(match=child).exists())
        self.assertGreater(child.waiting_expires_at, self.now)
        self.assertLessEqual(child.waiting_expires_at, self.post.matching_deadline)
        self.assertEqual(ProductEvent.objects.filter(name=ProductEvent.Name.MATCH_CREATED).count(), 2)

    def test_replayed_retry_is_one_child_even_after_that_child_times_out(self):
        old = self.timed_out()
        first = retry_waiting_match(self.swiper, old.pk)
        child = Match.objects.get(pk=first.match_id)
        deadline = child.waiting_expires_at
        repeated = retry_waiting_match(self.poster, old.pk)
        self.assertEqual(first.match_id, repeated.match_id)
        child.refresh_from_db()
        self.assertEqual(child.waiting_expires_at, deadline)
        Match.objects.filter(pk=child.pk).update(waiting_expires_at=self.now - timedelta(seconds=1))
        child = refresh_match(child.pk)
        late_replay = retry_waiting_match(self.swiper, old.pk)
        self.assertEqual(late_replay.match_id, child.pk)
        self.assertEqual(Match.objects.filter(retry_of=old).count(), 1)
        new_explicit = retry_waiting_match(self.swiper, child.pk)
        self.assertNotEqual(new_explicit.match_id, child.pk)
        self.assertEqual(Match.objects.get(pk=new_explicit.match_id).retry_of_id, child.pk)

    def test_ordinary_interest_after_timeout_does_not_implicitly_retry(self):
        old = self.timed_out()
        repeated = handle_swipe(self.swiper, self.post.pk, Swipe.Action.INTERESTED)
        self.assertEqual(repeated.match_id, old.pk)
        self.assertEqual(repeated.outcome, SwipeOutcome.MATCH_EXISTS)
        self.assertFalse(Match.objects.filter(retry_of=old).exists())

    def test_active_wait_and_chat_timeout_cannot_retry(self):
        first = handle_swipe(self.swiper, self.post.pk, Swipe.Action.INTERESTED)
        with self.assertRaises(RequestError):
            retry_waiting_match(self.swiper, first.match_id)
        Match.objects.filter(pk=first.match_id).update(status=Match.Status.EXPIRED, close_reason=Match.CloseReason.TIMEOUT,
            chat_started_at=self.now, chat_expires_at=self.now - timedelta(seconds=1))
        ActivityPost.objects.filter(pk=self.post.pk).update(status=ActivityPost.Status.ACTIVE)
        with self.assertRaises(RequestError):
            retry_waiting_match(self.swiper, first.match_id)

    def test_decline_report_reset_and_block_are_not_retryable(self):
        old = self.timed_out()
        for reason in (Match.CloseReason.DECLINED, Match.CloseReason.REPORTED, Match.CloseReason.RESET, Match.CloseReason.BLOCKED):
            with self.subTest(reason=reason):
                Match.objects.filter(pk=old.pk).update(close_reason=reason)
                with self.assertRaises(RequestError):
                    retry_waiting_match(self.swiper, old.pk)
        self.assertFalse(Match.objects.filter(retry_of=old).exists())

    def test_retired_or_blocked_actors_cannot_create_or_replay_retry(self):
        old = self.timed_out()
        UserBlock.objects.create(blocker=self.poster, target=self.swiper)
        with self.assertRaises(RequestError):
            retry_waiting_match(self.swiper, old.pk)
        UserBlock.objects.all().delete()
        child_id = retry_waiting_match(self.swiper, old.pk).match_id
        profile, _ = UserProfile.objects.get_or_create(user=self.poster)
        profile.retired_at = self.now
        profile.save(update_fields=["retired_at"])
        with self.assertRaises(RequestError):
            retry_waiting_match(self.swiper, old.pk)
        self.assertEqual(Match.objects.filter(retry_of=old).count(), 1)
        self.assertEqual(Match.objects.get(pk=child_id).status, Match.Status.WAITING)

    def test_card_end_pause_full_and_global_pause_block_new_retry(self):
        old = self.timed_out()
        for status in (ActivityPost.Status.CANCELLED, ActivityPost.Status.PAUSED, ActivityPost.Status.EXPIRED):
            with self.subTest(status=status):
                ActivityPost.objects.filter(pk=self.post.pk).update(status=status)
                with self.assertRaises(RequestError):
                    retry_waiting_match(self.swiper, old.pk)
        ActivityPost.objects.filter(pk=self.post.pk).update(status=ActivityPost.Status.ACTIVE,
            expected_end_time=self.now - timedelta(seconds=1))
        with self.assertRaises(RequestError):
            retry_waiting_match(self.swiper, old.pk)
        ActivityPost.objects.filter(pk=self.post.pk).update(expected_end_time=self.now + timedelta(hours=1))
        with override_settings(PLUSONE_NEW_MATCHES_ENABLED=False), self.assertRaises(RequestError):
            retry_waiting_match(self.swiper, old.pk)
        competing = handle_swipe(self.outsider, self.post.pk, Swipe.Action.INTERESTED)
        self.assertEqual(competing.outcome, SwipeOutcome.MATCH_CREATED)
        with self.assertRaises(RequestError):
            retry_waiting_match(self.swiper, old.pk)

    def test_foreign_actor_is_rejected(self):
        old = self.timed_out()
        with self.assertRaises(RequestError) as raised:
            retry_waiting_match(self.outsider, old.pk)
        self.assertEqual(raised.exception.status, 403)

    def test_busy_participant_does_not_gain_second_live_chat_on_retry(self):
        old = self.timed_out()
        other_post = ActivityPost.objects.create(user=self.outsider, title="Other study", activity_type="study",
            location=self.location, start_time=self.post.start_time, expected_end_time=self.post.expected_end_time,
            expire_time=self.post.expire_time, status=ActivityPost.Status.MATCHED)
        Match.objects.create(post=other_post, poster=self.outsider, swiper=self.swiper, status=Match.Status.CHATTING,
            chat_started_at=self.now, chat_expires_at=self.now + timedelta(minutes=5))
        child_id = retry_waiting_match(self.swiper, old.pk).match_id
        set_presence(child_id, self.poster, True, tab_id=uuid4(), sequence=1)
        child = set_presence(child_id, self.swiper, True, tab_id=uuid4(), sequence=1)
        self.assertEqual(child.status, Match.Status.WAITING)
        self.assertIsNone(child.chat_expires_at)
        self.assertEqual(phase_payload(child, self.swiper)["waiting_reason"], "viewer_busy")

    def test_discovery_explicit_retry_respects_filters_pass_and_latest_attempt(self):
        old = self.timed_out()
        query = {"activity_type": "study", "location": str(self.location.pk), "time_window": "now"}
        posts = list(discover_context_for_user(self.swiper, query)["posts"])
        self.assertEqual([post.pk for post in posts], [self.post.pk])
        self.assertEqual(posts[0].retry_match_id, old.pk)
        self.client.force_login(self.swiper)
        response = self.client.get(reverse("discover"), query)
        self.assertContains(response, "Invite again")
        self.assertContains(response, reverse("retry_waiting", args=[old.pk]))
        handle_swipe(self.swiper, self.post.pk, Swipe.Action.PASS)
        self.assertFalse(discover_context_for_user(self.swiper, query)["posts"].exists())
        Swipe.objects.filter(user=self.swiper, post=self.post).update(action=Swipe.Action.INTERESTED)
        child = retry_waiting_match(self.swiper, old.pk)
        self.assertFalse(discover_context_for_user(self.swiper, query)["posts"].exists())
        Match.objects.filter(pk=child.match_id).update(waiting_expires_at=self.now - timedelta(seconds=1))
        refresh_match(child.match_id)
        posts = list(discover_context_for_user(self.swiper, query)["posts"])
        self.assertEqual(posts[0].retry_match_id, child.match_id)

    def test_detail_uses_latest_attempt_and_success_page_has_actual_next_steps(self):
        self.client.force_login(self.poster)
        response = self.client.get(reverse("post_detail", args=[self.post.pk]))
        self.assertContains(response, timezone.localtime(self.post.matching_deadline).isoformat())
        self.assertContains(response, "What happens next")
        self.assertContains(response, reverse("dashboard"))
        self.assertContains(response, reverse("notifications"))
        self.assertNotContains(response, "configured delivery service")
        old = self.timed_out()
        child_id = retry_waiting_match(self.poster, old.pk).match_id
        response = self.client.get(reverse("post_detail", args=[self.post.pk]))
        self.assertEqual(response.context["current_match"].pk, child_id)
        self.assertContains(response, reverse("chat", args=[child_id]))

    def test_retry_view_only_posts_and_replay_returns_same_child(self):
        old = self.timed_out()
        self.client.force_login(self.swiper)
        url = reverse("retry_waiting", args=[old.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        first = self.client.post(url)
        child = Match.objects.get(retry_of=old)
        self.assertRedirects(first, reverse("chat", args=[child.pk]), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(url), first.url, fetch_redirect_response=False)
        self.assertEqual(Match.objects.filter(retry_of=old).count(), 1)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_closed_payload_and_room_offer_retry_or_existing_child(self):
        old = self.timed_out()
        self.assertTrue(phase_payload(old, self.swiper)["waiting_retry"]["can_retry"])
        self.client.force_login(self.swiper)
        response = self.client.get(reverse("chat", args=[old.pk]))
        self.assertContains(response, "Invite again")
        child_id = retry_waiting_match(self.swiper, old.pk).match_id
        state = waiting_retry_state(old, self.swiper)
        self.assertFalse(state["can_retry"])
        self.assertEqual(state["later_attempt_url"], reverse("chat", args=[child_id]))
        self.assertIsNone(waiting_retry_state(old, self.outsider)["later_attempt_url"])

    def test_background_expiration_closes_wait_at_activity_end_without_ending_chat_early(self):
        waiting = handle_swipe(self.swiper, self.post.pk, Swipe.Action.INTERESTED)
        ActivityPost.objects.filter(pk=self.post.pk).update(expected_end_time=self.now - timedelta(seconds=1))
        result = refresh_expired_records()
        match = Match.objects.get(pk=waiting.match_id)
        self.post.refresh_from_db()
        self.assertEqual(result["matches"], 1)
        self.assertEqual(match.status, Match.Status.EXPIRED)
        self.assertEqual(self.post.status, ActivityPost.Status.EXPIRED)
        self.assertFalse(waiting_retry_state(match, self.swiper)["can_retry"])
        Match.objects.filter(pk=match.pk).update(status=Match.Status.CHATTING, chat_started_at=self.now,
            chat_expires_at=self.now + timedelta(minutes=5))
        self.assertEqual(refresh_match(match.pk).status, Match.Status.CHATTING)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locking is required")
@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules", PLUSONE_NEW_MATCHES_ENABLED=True)
class WaitingRetryConcurrencyTests(WaitingFixtures, TransactionTestCase):
    def test_simultaneous_participants_retry_create_one_child_and_preserve_old_deadline(self):
        old = self.timed_out()
        deadline = old.waiting_expires_at
        barrier = Barrier(2)

        def retry(actor_id):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=actor_id)
                barrier.wait(timeout=10)
                return retry_waiting_match(actor, old.pk)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(retry, [self.poster.pk, self.swiper.pk]))
        self.assertEqual(len({result.match_id for result in results}), 1)
        self.assertEqual({result.outcome for result in results}, {SwipeOutcome.MATCH_CREATED, SwipeOutcome.MATCH_EXISTS})
        self.assertEqual(Match.objects.filter(retry_of=old).count(), 1)
        old.refresh_from_db()
        self.assertEqual(old.waiting_expires_at, deadline)
        self.assertEqual(old.status, Match.Status.EXPIRED)
        child = Match.objects.get(retry_of=old)
        child_deadline = child.waiting_expires_at
        self.assertEqual(retry_waiting_match(self.swiper, old.pk).match_id, child.pk)
        child.refresh_from_db()
        self.assertEqual(child.waiting_expires_at, child_deadline)
