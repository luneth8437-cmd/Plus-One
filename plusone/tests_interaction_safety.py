"""Safety choices, immediate closure and private explanations across HTTP."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from plusone.context_processors import session_scope
from plusone.models import ActivityPost, ActivityReport, CampusLocation, Match, ProductEvent, SafetyReport, UserBlock, UserProfile
from plusone.services.chat import report_match
from plusone.services.lifecycle import locked_match, end_locked, phase_payload
from plusone.services.safety import block_post_owner, block_user, closed_notice, close_blocked_relationships, report_post


class SafetyInteractionFixtures:
    def setUp(self):
        self.now = timezone.now().replace(second=0, microsecond=0)
        clock = patch("django.utils.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        users = get_user_model()
        self.actor = users.objects.create_user("anon_interaction_safety_actor")
        self.target = users.objects.create_user("anon_interaction_safety_target")
        self.outsider = users.objects.create_user("anon_interaction_safety_outsider")
        self.location = CampusLocation.objects.create(name="Safety interaction library", area="Test", location_type="study")

    def card(self, owner=None, *, ended=False):
        return ActivityPost.objects.create(user=owner or self.target, title="Public safety interaction",
            description="Meet in a public place", activity_type="study", location=self.location,
            start_time=self.now + timedelta(hours=-2 if ended else 2),
            expected_end_time=self.now + timedelta(hours=-1 if ended else 3),
            expire_time=self.now + timedelta(hours=1))

    def relationship(self, status=Match.Status.WAITING, *, owner=None, guest=None, ended=False):
        post = self.card(owner, ended=ended)
        post.status = ActivityPost.Status.MATCHED
        post.save(update_fields=["status"])
        return Match.objects.create(post=post, poster=post.user, swiper=guest or self.actor, status=status,
            waiting_expires_at=self.now + timedelta(minutes=10),
            chat_started_at=self.now if status == Match.Status.CHATTING else None,
            chat_expires_at=self.now + timedelta(minutes=5) if status == Match.Status.CHATTING else None,
            meeting_point=self.location.name, plan_meeting_at=post.start_time, plan_expected_end_at=post.expected_end_time,
            poster_agreed=status == Match.Status.AGREED, swiper_agreed=status == Match.Status.AGREED,
            poster_agreed_revision=1 if status == Match.Status.AGREED else None,
            swiper_agreed_revision=1 if status == Match.Status.AGREED else None,
            plan_confirmed_at=self.now - timedelta(hours=3) if status == Match.Status.AGREED else None)

    def report_http(self, match, *, block=False):
        self.client.force_login(self.actor)
        data = {"action": "report", "category": "harassment", "reason": "Private reporter evidence",
                "session_scope": session_scope(self.actor)}
        if block:
            data["block_user"] = "yes"
        return self.client.post(reverse("chat", args=[match.pk]), data)


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules", PLUSONE_NEW_MATCHES_ENABLED=True)
class SafetyInteractionTests(SafetyInteractionFixtures, TestCase):
    def test_unchecked_report_closes_current_wait_without_creating_block(self):
        match = self.relationship()
        later = self.relationship(Match.Status.AGREED)
        self.assertEqual(self.report_http(match).status_code, 302)
        match.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(match.status, Match.Status.DECLINED)
        self.assertEqual(match.close_reason, Match.CloseReason.REPORTED)
        self.assertTrue(SafetyReport.objects.filter(match=match, reporter=self.actor).exists())
        self.assertFalse(UserBlock.objects.exists())
        self.assertIsNone(later.meetup_cancelled_at)

    def test_unchecked_report_still_rejects_a_retired_actor_before_any_write(self):
        match = self.relationship(Match.Status.AGREED)
        UserProfile.objects.update_or_create(user=self.actor, defaults={"display_name": "Retired", "retired_at": self.now})
        response = self.report_http(match)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(SafetyReport.objects.exists())
        self.assertFalse(UserBlock.objects.exists())
        match.refresh_from_db()
        self.assertIsNone(match.meetup_cancelled_at)

    def test_checked_report_closes_other_pair_plans_but_preserves_finished_and_unrelated(self):
        current = self.relationship(Match.Status.CHATTING)
        later = self.relationship(Match.Status.AGREED)
        finished = self.relationship(Match.Status.AGREED, ended=True)
        unrelated = self.relationship(owner=self.outsider)
        evidence = ProductEvent.objects.create(name=ProductEvent.Name.MEETUP_CONFIRMED, match=finished, user=self.actor)
        self.assertEqual(self.report_http(current, block=True).status_code, 302)
        self.assertTrue(UserBlock.objects.filter(blocker=self.actor, target=self.target).exists())
        for match in (current, later, finished, unrelated):
            match.refresh_from_db()
        self.assertEqual(current.close_reason, Match.CloseReason.REPORTED)
        self.assertIsNotNone(later.meetup_cancelled_at)
        self.assertEqual(later.post.__class__.objects.get(pk=later.post_id).status, ActivityPost.Status.PAUSED)
        self.assertIsNone(finished.meetup_cancelled_at)
        self.assertTrue(ProductEvent.objects.filter(pk=evidence.pk).exists())
        self.assertEqual(unrelated.status, Match.Status.WAITING)

    def test_public_block_finishes_both_roles_before_http_response(self):
        waiting = self.relationship()
        chatting = self.relationship(Match.Status.CHATTING, owner=self.actor, guest=self.target)
        agreed = self.relationship(Match.Status.AGREED)
        self.client.force_login(self.actor)
        response = self.client.post(reverse("post_safety", args=[waiting.post_id]),
            {"action": "block", "session_scope": session_scope(self.actor)})
        self.assertEqual(response.status_code, 302)
        for match in (waiting, chatting, agreed):
            match.refresh_from_db()
        self.assertEqual(waiting.close_reason, Match.CloseReason.BLOCKED)
        self.assertEqual(chatting.close_reason, Match.CloseReason.BLOCKED)
        self.assertIsNotNone(agreed.meetup_cancelled_at)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["open_matches"], [])
        self.assertEqual(dashboard.context["handoff_matches"], [])

    def test_repeated_public_block_does_not_fabricate_or_repeat_completed_history(self):
        live = self.relationship()
        finished = self.relationship(Match.Status.AGREED, ended=True)
        # Imported history may not yet have a stored snapshot. Blocking must
        # not initialize it or add cancellation/event evidence retroactively.
        Match.objects.filter(pk=finished.pk).update(plan_meeting_at=None, plan_expected_end_at=None, meeting_point="")
        ActivityPost.objects.filter(pk=finished.post_id).update(expected_end_time=None)
        before = Match.objects.filter(pk=finished.pk).values().get()
        block_post_owner(live.post_id, self.actor)
        messages = live.messages.count()
        events = ProductEvent.objects.filter(match=live).count()
        block_user(self.actor, self.target.pk)
        self.assertEqual(Match.objects.filter(pk=finished.pk).values().get(), before)
        self.assertEqual(live.messages.count(), messages)
        self.assertEqual(ProductEvent.objects.filter(match=live).count(), events)

    def test_public_report_respects_block_choice_and_closes_only_when_selected(self):
        agreed = self.relationship(Match.Status.AGREED)
        report_post(agreed.post_id, self.actor, reason="Review this card", block=False)
        agreed.refresh_from_db()
        self.assertIsNone(agreed.meetup_cancelled_at)
        report_post(agreed.post_id, self.actor, reason="Block contact as well", block=True)
        agreed.refresh_from_db()
        self.assertIsNotNone(agreed.meetup_cancelled_at)
        self.assertEqual(ActivityReport.objects.filter(post_id=agreed.post_id, reporter=self.actor).count(), 1)

    def test_blocked_detail_has_no_matching_cta_for_either_direction_and_remains_active_for_third_person(self):
        target_card = self.card()
        actor_card = self.card(self.actor)
        block_user(self.actor, self.target.pk)
        for user, card in ((self.actor, target_card), (self.target, actor_card)):
            with self.subTest(viewer=user.pk):
                self.client.force_login(user)
                response = self.client.get(reverse("post_detail", args=[card.pk]))
                self.assertContains(response, "Matching is unavailable between these guest identities.")
                self.assertNotContains(response, 'value="interested"')
                self.assertNotContains(response, 'value="pass"')
                self.assertNotContains(response, "This Plus One is no longer active.")
                self.assertNotContains(response, "blocked you")
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("post_detail", args=[target_card.pk]))
        self.assertContains(response, 'value="interested"')
        target_card.refresh_from_db()
        self.assertEqual(target_card.status, ActivityPost.Status.ACTIVE)

    def test_report_consequences_are_server_rendered_for_each_phase(self):
        cases = (
            (Match.Status.WAITING, False, "Reporting ends this waiting room for both people."),
            (Match.Status.CHATTING, False, "Reporting ends this chat for both people."),
            (Match.Status.AGREED, False, "Reporting cancels this confirmed meetup."),
            (Match.Status.AGREED, True, "It does not change an already ended or cancelled meetup."),
        )
        self.client.force_login(self.actor)
        for status, ended, explanation in cases:
            with self.subTest(status=status, ended=ended):
                match = self.relationship(status, ended=ended)
                response = self.client.get(reverse("chat", args=[match.pk]))
                self.assertContains(response, explanation)
                self.assertContains(response, "blocking also ends your other open matches")
        public = self.client.get(reverse("post_detail", args=[self.card().pk]))
        self.assertContains(public, "Reporting the card alone does not end existing matches")
        self.assertContains(public, "Blocking this guest ends your open matches")

    def test_closed_notice_does_not_expose_private_safety_reason_or_reporter(self):
        report = self.relationship(Match.Status.CHATTING)
        report_match(report.pk, self.actor, "harassment", "Private reporter evidence", block=False)
        blocked = self.relationship()
        block_user(self.actor, self.target.pk)
        self.client.force_login(self.target)
        for match in (report, blocked):
            match.refresh_from_db()
            response = self.client.get(reverse("chat", args=[match.pk]))
            notice = response.context["closure_notice"]
            self.assertIn("This match has ended.", notice)
            self.assertNotIn("Time ran out", notice)
            self.assertNotIn("reported", notice.lower())
            self.assertNotIn("blocked", notice.lower())
            self.assertNotContains(response, "Private reporter evidence")
            payload = self.client.get(reverse("chat_messages", args=[match.pk])).json()
            self.assertEqual(payload["closure_notice"], notice)
            self.assertNotIn("close_reason", payload)

    def test_timeout_and_activity_cancellation_have_distinct_accurate_notices(self):
        for status, reason, expected in (
            (Match.Status.WAITING, Match.CloseReason.TIMEOUT, "The wait ended before you both joined."),
            (Match.Status.CHATTING, Match.CloseReason.TIMEOUT, "Time ran out before you both agreed."),
            (Match.Status.WAITING, Match.CloseReason.CANCELLED, "This activity was cancelled."),
            (Match.Status.CHATTING, Match.CloseReason.RESET, "This match has ended."),
        ):
            with self.subTest(status=status, reason=reason):
                match = self.relationship(status)
                with locked_match(match.pk) as current:
                    end_locked(current, reason=reason)
                    self.assertIn(expected, closed_notice(current))
                    self.assertIn(expected, phase_payload(current, self.actor)["closure_notice"])


@skipUnless(connection.vendor == "postgresql", "PostgreSQL verifies actual row locks")
@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class SafetyInteractionConcurrencyTests(SafetyInteractionFixtures, TransactionTestCase):
    def test_opposite_blocks_close_multiple_cards_without_nested_user_lock_inversion(self):
        waiting = self.relationship()
        chatting = self.relationship(Match.Status.CHATTING, owner=self.actor, guest=self.target)
        agreed = self.relationship(Match.Status.AGREED)
        finished = self.relationship(Match.Status.AGREED, ended=True)
        barrier = Barrier(2)
        transaction_states = []
        original_close = close_blocked_relationships

        def coordinated_close(first, second):
            transaction_states.append(connections["default"].in_atomic_block)
            barrier.wait(timeout=5)
            return original_close(first, second)

        def block(first, second):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=first)
                block_user(actor, second)
            finally:
                connections["default"].close()

        with patch("plusone.services.safety.close_blocked_relationships", side_effect=coordinated_close):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(block, self.actor.pk, self.target.pk), pool.submit(block, self.target.pk, self.actor.pk)]
                for future in futures:
                    future.result(timeout=15)
        self.assertEqual(transaction_states, [False, False])
        for match in (waiting, chatting, agreed, finished):
            match.refresh_from_db()
        self.assertEqual(waiting.close_reason, Match.CloseReason.BLOCKED)
        self.assertEqual(chatting.close_reason, Match.CloseReason.BLOCKED)
        self.assertIsNotNone(agreed.meetup_cancelled_at)
        self.assertIsNone(finished.meetup_cancelled_at)
        self.assertEqual(UserBlock.objects.count(), 2)
        self.assertEqual(ProductEvent.objects.filter(match=agreed, name=ProductEvent.Name.MEETUP_CANCELLED).count(), 1)
