"""Confirmed plans allow bounded coordination, with current-time arrival estimates."""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .context_processors import session_scope
from .models import ActivityPost, CampusLocation, Match, MeetupAction, ProductEvent
from .services.meetups import perform_meetup_action, plan_payload
from .services.lifecycle import phase_payload
from .services.requests import RequestError


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules", TIME_ZONE="Asia/Shanghai")
class ArrivalCoordinationTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=dt_timezone.utc)
        clock = patch("django.utils.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        users = get_user_model()
        self.poster = users.objects.create_user("arrival_poster")
        self.swiper = users.objects.create_user("arrival_swiper")
        self.outsider = users.objects.create_user("arrival_outsider")
        location = CampusLocation.objects.create(name="Arrival test library", area="Test public campus", location_type="study")
        self.post = ActivityPost.objects.create(
            user=self.poster, title="Real public study plan", activity_type="study", location=location,
            start_time=self.now - timedelta(minutes=10), expected_end_time=self.now + timedelta(minutes=50),
            expire_time=self.now + timedelta(minutes=40), status=ActivityPost.Status.MATCHED,
        )
        self.match = Match.objects.create(
            post=self.post, poster=self.poster, swiper=self.swiper, status=Match.Status.AGREED,
            meeting_point="Library public south entrance", plan_meeting_at=self.post.start_time,
            plan_expected_end_at=self.post.expected_end_time, plan_confirmed_at=self.now - timedelta(minutes=20),
            poster_agreed=True, swiper_agreed=True, poster_agreed_revision=1, swiper_agreed_revision=1,
            chat_started_at=self.now - timedelta(minutes=20), chat_expires_at=self.now - timedelta(minutes=15),
        )
        self.original_plan = (self.match.meeting_point, self.match.plan_meeting_at, self.match.plan_expected_end_at, self.match.plan_revision)

    def action(self, action, user=None, request_id=None, revision=1, **kwargs):
        return perform_meetup_action(self.match.pk, user or self.poster, action, request_id or uuid4(), revision, **kwargs)

    def http_action(self, action, *, user=None, request_id=None, accept_json=True, **fields):
        actor = user or self.poster
        self.client.force_login(actor)
        return self.client.post(reverse("chat_plan", args=[self.match.pk]), {
            "action": action, "revision": "1", "request_id": str(request_id or uuid4()), "session_scope": session_scope(actor), **fields,
        }, HTTP_ACCEPT="application/json" if accept_json else "text/html")

    def assert_plan_preserved(self):
        self.match.refresh_from_db()
        self.assertEqual((self.match.meeting_point, self.match.plan_meeting_at, self.match.plan_expected_end_at, self.match.plan_revision), self.original_plan)
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertFalse(phase_payload(self.match, self.poster)["chat_active"])
        self.assertEqual((self.match.poster_meetup_outcome, self.match.swiper_meetup_outcome), ("", ""))

    def test_delay_estimate_uses_now_after_planned_meeting_time_has_passed(self):
        result = self.action("delayed", delay_minutes=5)
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_arrival_eta, self.now + timedelta(minutes=5))
        self.assertEqual(self.match.poster_arrival_updated_at, self.now)
        self.assertEqual(result["plan"]["viewer_arrival_eta_display"], "Oct 03, 20:05")
        self.assertEqual(result["plan"]["viewer_arrival_updated_at_display"], "Oct 03, 20:00")
        self.assertFalse(result["plan"]["viewer_delay_expired"])
        self.assert_plan_preserved()

    def test_replaying_lost_response_does_not_move_eta_forward_or_create_extra_event(self):
        request_id = uuid4()
        accepted = self.action("delayed", request_id=request_id, delay_minutes=10)
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(minutes=7)):
            replay = self.action("delayed", request_id=request_id, delay_minutes=10)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["plan"]["viewer_arrival_eta"], accepted["plan"]["viewer_arrival_eta"])
        self.assertEqual(replay["plan"]["viewer_arrival_updated_at"], self.now.isoformat())
        self.assertEqual(ProductEvent.objects.filter(match=self.match, name=ProductEvent.Name.MEETUP_STATUS_UPDATED).count(), 1)

    def test_expired_eta_asks_for_update_instead_of_claiming_future_arrival(self):
        self.action("delayed", delay_minutes=5)
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(minutes=6)):
            payload = plan_payload(Match.objects.get(pk=self.match.pk), self.swiper)
        self.assertTrue(payload["other_delay_expired"])
        self.assertIn("estimate has passed", payload["other_arrival_label"])
        self.assertNotIn("Expected around", payload["other_arrival_label"])
        self.assertEqual(payload["other_arrival_updated_at"], self.now.isoformat())

    def test_arrival_replaces_delay_with_fixed_point_signal_without_claiming_meetup(self):
        self.action("delayed", delay_minutes=10)
        result = self.action("arrived")
        self.match.refresh_from_db()
        self.assertEqual(self.match.poster_arrival_status, Match.ArrivalStatus.ARRIVED)
        self.assertEqual(self.match.poster_coordination_signal, "at_point")
        self.assertIsNone(self.match.poster_arrival_eta)
        self.assertEqual(self.match.poster_delay_minutes, 0)
        self.assertEqual(result["plan"]["viewer_arrival_label"], "At the agreed meeting point")
        self.assert_plan_preserved()

    def test_all_fixed_signals_only_update_the_actor_and_keep_free_chat_closed(self):
        for signal in Match.CoordinationSignal.values:
            result = self.action("coordination", coordination_signal=signal)
            self.assertEqual(result["plan"]["viewer_coordination_signal"], signal)
            self.assertEqual(result["plan"]["other_status"], Match.ArrivalStatus.PENDING)
            self.assert_plan_preserved()
        self.assertFalse(self.match.messages.filter(is_system=False).exists())

    def test_clearing_coordination_restores_pending_and_clears_signal_eta_and_time(self):
        self.action("coordination", coordination_signal="cant_find")
        self.action("arrived", user=self.swiper)
        result = self.action("coordination", coordination_signal="clear")
        self.match.refresh_from_db()
        self.assertEqual(result["plan"]["viewer_status"], Match.ArrivalStatus.PENDING)
        self.assertEqual(result["plan"]["viewer_coordination_signal"], "")
        self.assertIsNone(self.match.poster_arrival_eta)
        self.assertIsNone(self.match.poster_arrival_updated_at)
        self.assertEqual(result["plan"]["other_status"], Match.ArrivalStatus.ARRIVED)
        self.assert_plan_preserved()

    def test_clearing_delay_clears_only_actor_estimate(self):
        self.action("delayed", delay_minutes=5)
        self.action("delayed", user=self.swiper, delay_minutes=10)
        result = self.action("delayed", delay_minutes=0)
        self.assertEqual(result["plan"]["viewer_status"], Match.ArrivalStatus.PENDING)
        self.assertIsNone(result["plan"]["viewer_arrival_eta"])
        self.assertIsNone(result["plan"]["viewer_arrival_updated_at"])
        self.assertEqual(result["plan"]["other_status"], Match.ArrivalStatus.DELAYED)

    def test_estimate_at_or_after_agreed_end_is_refused_and_buttons_explain_remaining_options(self):
        Match.objects.filter(pk=self.match.pk).update(plan_expected_end_at=self.now + timedelta(minutes=5))
        with self.assertRaises(RequestError) as error:
            self.action("delayed", delay_minutes=5)
        self.assertEqual(error.exception.status, 409)
        self.assertIn("Cancel the meetup", str(error.exception))
        self.match.refresh_from_db()
        self.assertIsNone(self.match.poster_arrival_eta)
        self.assertFalse(MeetupAction.objects.filter(match=self.match).exists())
        payload = plan_payload(self.match, self.poster)
        self.assertFalse(payload["can_delay_5"])
        self.assertFalse(payload["can_delay_10"])
        self.assertTrue(payload["can_arrive"])
        self.assertTrue(payload["can_delay"])

    def test_coordination_rejects_unconfirmed_future_closed_and_stale_plan_states(self):
        states = (
            {"status": Match.Status.CHATTING, "chat_expires_at": self.now + timedelta(minutes=5)},
            {"status": Match.Status.AGREED, "plan_meeting_at": self.now + timedelta(minutes=31), "plan_expected_end_at": self.now + timedelta(hours=2)},
            {"status": Match.Status.AGREED, "plan_meeting_at": self.post.start_time, "plan_expected_end_at": self.now},
            {"status": Match.Status.AGREED, "plan_expected_end_at": self.post.expected_end_time, "meetup_cancelled_at": self.now},
        )
        for state in states:
            Match.objects.filter(pk=self.match.pk).update(**state)
            with self.subTest(state=state), self.assertRaises(RequestError):
                self.action("coordination", coordination_signal="cant_find")
        Match.objects.filter(pk=self.match.pk).update(meetup_cancelled_at=None, plan_revision=2)
        with self.assertRaises(RequestError) as error:
            self.action("coordination", coordination_signal="cant_find", revision=1)
        self.assertEqual(error.exception.status, 409)

    def test_unrestricted_text_and_other_actor_are_rejected(self):
        for signal in ("contact me", "go somewhere else", "", "tel:12345678"):
            with self.subTest(signal=signal), self.assertRaises(RequestError):
                self.action("coordination", coordination_signal=signal)
        with self.assertRaises(RequestError) as error:
            self.action("coordination", user=self.outsider, coordination_signal="cant_find")
        self.assertEqual(error.exception.status, 403)

    def test_new_request_can_update_estimate_but_arrival_never_unlocks_early_no_show_feedback(self):
        self.action("delayed", delay_minutes=5)
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(minutes=6)):
            result = self.action("delayed", delay_minutes=10)
        self.assertEqual(result["plan"]["viewer_arrival_eta"], (self.now + timedelta(minutes=16)).isoformat())
        self.action("coordination", coordination_signal="cant_find")
        with self.assertRaises(RequestError) as error:
            self.action("outcome", outcome="not_met", outcome_reason="no_show")
        self.assertEqual(error.exception.status, 409)

    def test_ended_or_cancelled_status_is_historical_and_preserves_evidence(self):
        self.action("coordination", coordination_signal="at_entrance")
        with patch("django.utils.timezone.now", return_value=self.post.expected_end_time + timedelta(seconds=1)):
            payload = plan_payload(Match.objects.get(pk=self.match.pk), self.poster)
        self.assertEqual(payload["meetup_status"], "finished")
        self.assertTrue(payload["viewer_arrival_label"].startswith("Last report:"))
        self.assertFalse(payload["can_coordinate"])
        self.assertFalse(payload["can_arrive"])
        self.assertEqual(payload["viewer_arrival_updated_at"], self.now.isoformat())

    def test_legacy_delay_has_no_fabricated_estimate_or_timestamp(self):
        Match.objects.filter(pk=self.match.pk).update(poster_arrival_status=Match.ArrivalStatus.DELAYED, poster_delay_minutes=10)
        payload = plan_payload(Match.objects.get(pk=self.match.pk), self.poster)
        self.assertIsNone(payload["viewer_arrival_eta"])
        self.assertIsNone(payload["viewer_arrival_updated_at"])
        self.assertIn("not recorded", payload["viewer_arrival_label"])

    def test_http_fixed_signal_is_visible_to_counterparty_in_campus_time(self):
        response = self.http_action("coordination", coordination_signal="cant_find")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"]["viewer_coordination_signal"], "cant_find")
        self.client.force_login(self.swiper)
        state = self.client.get(reverse("chat_messages", args=[self.match.pk])).json()["plan"]
        self.assertEqual(state["other_coordination_signal"], "cant_find")
        self.assertEqual(state["other_arrival_updated_at_display"], "Oct 03, 20:00")
        self.assertIn("cannot find you", state["other_arrival_label"])
        self.assert_plan_preserved()

    def test_http_delay_and_lost_response_replay_preserve_original_eta_and_render_without_js(self):
        request_id = uuid4()
        first = self.http_action("delayed", request_id=request_id, delay_minutes="5")
        self.assertEqual(first.status_code, 200)
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(minutes=2)):
            replay = self.http_action("delayed", request_id=request_id, delay_minutes="5")
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["plan"]["viewer_arrival_eta_display"], "Oct 03, 20:05")
        rendered = self.client.get(reverse("chat", args=[self.match.pk]))
        self.assertContains(rendered, "Expected around Oct 03, 20:05")
        self.assertContains(rendered, "Updated Oct 03, 20:00")
        native = self.http_action("coordination", coordination_signal="at_entrance", accept_json=False)
        self.assertRedirects(native, reverse("chat", args=[self.match.pk]))

    def test_http_outsider_and_unrestricted_signal_cannot_coordinate(self):
        self.assertEqual(self.http_action("coordination", user=self.outsider, coordination_signal="cant_find").status_code, 403)
        self.assertEqual(self.http_action("coordination", coordination_signal="send me your number").status_code, 400)
        self.assertFalse(MeetupAction.objects.filter(match=self.match).exists())
