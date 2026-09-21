"""Behavioral regressions for the production lifecycle and request contracts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from plusone.models import ActivityPost, CampusLocation, ChatMessage, Match, ProductEvent, SafetyReport, UserProfile
from plusone.services.chat import close_match, create_chat_message, record_agreement, report_match
from plusone.services.identity import retire_anonymous_identity
from plusone.services.lifecycle import refresh_match, set_presence, locked_match, system_message_locked
from plusone.services.matching import handle_swipe
from plusone.services.requests import RequestError, consume_limit


class Fixtures:
    def setUp(self):
        User = get_user_model()
        self.poster = User.objects.create_user("anon_reliable_poster")
        self.guest = User.objects.create_user("anon_reliable_guest")
        self.outsider = User.objects.create_user("anon_reliable_outsider")
        self.location, _ = CampusLocation.objects.get_or_create(name="Reliability library", defaults={"location_type": "study", "area": "Campus"})
        self.post = ActivityPost.objects.create(user=self.poster, title="Library session", description="Study together",
            activity_type="study", location=self.location, start_time=timezone.now() + timedelta(hours=2), expire_time=timezone.now() + timedelta(minutes=30))

    def waiting(self):
        result = handle_swipe(self.guest, self.post.pk, "interested")
        return Match.objects.get(pk=result.match_id)

    def chatting(self):
        match = self.waiting()
        set_presence(match.pk, self.poster, True)
        return set_presence(match.pk, self.guest, True)

    def publish_data(self):
        start_time = (timezone.localtime() + timedelta(hours=2)).replace(second=0, microsecond=0)
        return {"action": "publish", "request_id": str(uuid4()), "title": "Lunch together", "description": "Public canteen",
            "activity_type": "food", "location": self.location.pk,
            "start_time": start_time.strftime("%Y-%m-%dT%H:%M"),
            "expected_end_time": (start_time + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "expire_minutes": "30"}


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class LifecycleTests(Fixtures, TestCase):
    def test_waiting_reserves_slot_without_start_or_messages(self):
        match = self.waiting()
        self.assertEqual(match.status, Match.Status.WAITING)
        self.assertIsNone(match.chat_expires_at)
        self.assertIsNone(match.chat_started_at)
        self.assertFalse(match.messages.exists())
        self.assertLessEqual(match.waiting_expires_at, timezone.now() + timedelta(minutes=10))
        self.assertEqual(self.post.held_spots, 1)
        self.client.force_login(self.poster)
        self.client.get(reverse("chat", args=[match.pk]))
        match.refresh_from_db()
        self.assertIsNone(match.poster_last_present_at)
        self.assertFalse(record_agreement(match.pk, self.poster).recorded)
        with patch("plusone.services.chat.moderate_text") as moderate:
            message, result = create_chat_message(match, self.poster, "hello", uuid4())
        self.assertIsNone(message)
        moderate.assert_not_called()

    def test_only_two_recent_foreground_signals_activate_once(self):
        match = self.waiting()
        set_presence(match.pk, self.poster, True)
        Match.objects.filter(pk=match.pk).update(poster_last_present_at=timezone.now() - timedelta(seconds=16))
        self.assertEqual(set_presence(match.pk, self.guest, True).status, Match.Status.WAITING)
        current = set_presence(match.pk, self.poster, True)
        self.assertEqual(current.status, Match.Status.CHATTING)
        deadline = current.chat_expires_at
        self.assertEqual(deadline - current.chat_started_at, timedelta(minutes=5))
        set_presence(match.pk, self.poster, False)
        set_presence(match.pk, self.guest, False)
        set_presence(match.pk, self.poster, True)
        current.refresh_from_db()
        self.assertEqual(current.chat_expires_at, deadline)
        self.assertEqual(current.messages.filter(is_system=True).count(), 1)
        self.assertEqual(ProductEvent.objects.filter(name="chat_started", match=current).count(), 1)

    def test_hidden_signal_and_nonparticipant_do_not_activate(self):
        match = self.waiting()
        set_presence(match.pk, self.poster, True)
        set_presence(match.pk, self.poster, False)
        self.assertIsNone(set_presence(match.pk, self.outsider, True))
        self.assertEqual(set_presence(match.pk, self.guest, True).status, Match.Status.WAITING)

    def test_timeout_releases_card_and_retry_cannot_renew_match(self):
        match = self.waiting()
        Match.objects.filter(pk=match.pk).update(waiting_expires_at=timezone.now() - timedelta(seconds=1))
        refresh_match(match.pk)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.ACTIVE)
        retry = handle_swipe(self.guest, self.post.pk, "interested")
        self.assertEqual(retry.match_id, match.pk)
        self.assertEqual(set_presence(match.pk, self.poster, True).status, Match.Status.EXPIRED)
        self.assertEqual(Match.objects.filter(post=self.post).count(), 1)

    def test_waiting_deadline_is_bounded_by_card(self):
        deadline = timezone.now() + timedelta(minutes=2)
        ActivityPost.objects.filter(pk=self.post.pk).update(expire_time=deadline)
        self.assertEqual(self.waiting().waiting_expires_at, deadline)

    def test_chat_expiry_from_poll_releases_capacity(self):
        match = self.chatting()
        Match.objects.filter(pk=match.pk).update(chat_expires_at=timezone.now() - timedelta(seconds=1))
        self.client.force_login(self.guest)
        data = self.client.get(reverse("chat_messages", args=[match.pk])).json()
        self.assertEqual(data["phase"], "expired")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, "active")

    def test_cancelled_post_never_reopens_with_stale_instance(self):
        from plusone.services.capacity import sync_post_status_for_capacity
        from plusone.services.posts import cancel_activity_post
        match = self.waiting()
        cancel_activity_post(self.post)
        sync_post_status_for_capacity(self.post)
        self.post.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(self.post.status, "cancelled")
        self.assertEqual(match.status, "expired")

    def test_identity_reset_owner_cancels_guest_releases(self):
        match = self.waiting()
        retire_anonymous_identity(self.guest)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, "active")
        other = handle_swipe(self.outsider, self.post.pk, "interested")
        retire_anonymous_identity(self.poster)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, "cancelled")
        self.assertEqual(Match.objects.get(pk=other.match_id).status, "expired")

    def test_report_after_agreement_preserves_history_and_deduplicates(self):
        match = self.chatting()
        record_agreement(match.pk, self.poster)
        record_agreement(match.pk, self.guest)
        report_match(match.pk, self.poster, "harassment", "First concern")
        report_match(match.pk, self.poster, "unsafe_meeting", "Additional context")
        match.refresh_from_db()
        self.assertEqual(match.status, "agreed")
        self.assertEqual(SafetyReport.objects.filter(match=match).count(), 1)
        self.assertEqual(SafetyReport.objects.get(match=match).reason, "Additional context")
        with self.assertRaises(RequestError):
            report_match(match.pk, self.outsider)

    def test_report_waiting_closes_and_agreement_is_once_per_person(self):
        match = self.waiting()
        report_match(match.pk, self.poster)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, "active")
        self.assertEqual(Match.objects.get(pk=match.pk).status, "declined")

    def test_report_supplement_reopens_resolved_and_expired_report_is_explicit(self):
        match = self.chatting()
        report = report_match(match.pk, self.poster, reason="Initial concern")
        SafetyReport.objects.filter(pk=report.pk).update(status="resolved", handling_notes="Reviewed")
        report = report_match(match.pk, self.poster, reason="More context")
        self.assertEqual(report.status, "pending")
        self.assertEqual(report.handling_notes, "Reviewed")
        SafetyReport.objects.filter(pk=report.pk).update(created_at=timezone.now() - timedelta(days=91))
        with self.assertRaises(RequestError) as expired:
            report_match(match.pk, self.poster, reason="Too late")
        self.assertEqual(expired.exception.status, 410)

    def test_retired_owner_fences_swipe_and_activation_before_drain(self):
        UserProfile.objects.create(user=self.poster, retired_at=timezone.now())
        result = handle_swipe(self.guest, self.post.pk, "interested")
        self.assertEqual(result.outcome, "inactive_post")
        UserProfile.objects.filter(user=self.poster).update(retired_at=None)
        match = self.waiting()
        set_presence(match.pk, self.poster, True)
        UserProfile.objects.filter(user=self.poster).update(retired_at=timezone.now())
        self.assertEqual(set_presence(match.pk, self.guest, True).status, "expired")
        self.assertFalse(ProductEvent.objects.filter(name="chat_started").exists())


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class RequestReliabilityTests(Fixtures, TestCase):
    def test_message_replay_after_closed_and_conflicting_payload(self):
        match = self.chatting()
        key = uuid4()
        original, _ = create_chat_message(match, self.poster, "Hello there", key)
        close_match(match.pk, self.guest, Match.CloseReason.DECLINED)
        with patch("plusone.services.chat.moderate_text") as moderate:
            replay, result = create_chat_message(match, self.poster, "Hello there", key)
        self.assertEqual(replay.pk, original.pk)
        self.assertTrue(result["replayed"])
        moderate.assert_not_called()
        with self.assertRaises(RequestError) as conflict:
            create_chat_message(match, self.poster, "different", key)
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(ProductEvent.objects.filter(name="message_sent").count(), 1)

    def test_publish_replay_even_after_form_time_is_past(self):
        self.client.force_login(self.poster)
        data = self.publish_data()
        original = self.client.post(reverse("create_post"), data)
        self.assertEqual(original.status_code, 302)
        with patch("plusone.views.moderate_activity_form") as moderate:
            replay = self.client.post(reverse("create_post"), data)
        self.assertEqual(replay.url, original.url)
        moderate.assert_not_called()
        self.assertEqual(ProductEvent.objects.filter(name="publish_card").count(), 1)
        data["description"] = "changed"
        self.assertEqual(self.client.post(reverse("create_post"), data).status_code, 409)

    def test_pre_end_time_request_replays_only_when_legacy_payload_is_unchanged(self):
        from plusone.services.posts import legacy_publish_fingerprint

        self.client.force_login(self.poster)
        data = self.publish_data()
        data.pop("expected_end_time")
        data["location"] = str(data["location"])
        self.post.request_id = data["request_id"]
        self.post.request_fingerprint = legacy_publish_fingerprint(data)
        self.post.save(update_fields=["request_id", "request_fingerprint"])

        with patch("plusone.views.moderate_activity_form") as moderate:
            replay = self.client.post(reverse("create_post"), data)
        self.assertRedirects(replay, reverse("post_detail", args=[self.post.pk]))
        moderate.assert_not_called()

        data["description"] = "changed"
        self.assertEqual(self.client.post(reverse("create_post"), data).status_code, 409)
        data["description"] = "Public canteen"
        data["expected_end_time"] = ""
        self.assertEqual(self.client.post(reverse("create_post"), data).status_code, 409)

    def test_missing_request_id_is_explicit_for_both_endpoints(self):
        self.client.force_login(self.poster)
        data = self.publish_data()
        del data["request_id"]
        response = self.client.post(reverse("create_post"), data)
        self.assertContains(response, "Refresh before submitting", status_code=400)
        match = self.chatting()
        self.assertEqual(self.client.post(reverse("chat_messages", args=[match.pk]), {"message": "hi"}).status_code, 400)

    def test_unavailable_moderation_keeps_html_input_and_blocks_writes(self):
        self.client.force_login(self.poster)
        data = self.publish_data()
        with patch("plusone.views.moderate_activity_form", return_value={"service_unavailable": True, "reason": "temporarily unavailable"}):
            response = self.client.post(reverse("create_post"), data)
        self.assertContains(response, data["description"], status_code=503)
        self.assertFalse(ActivityPost.objects.filter(request_id=data["request_id"]).exists())
        match = self.chatting()
        with patch("plusone.services.chat.moderate_text", return_value={"service_unavailable": True}):
            response = self.client.post(reverse("chat", args=[match.pk]), {"action": "send", "message": "Keep this draft", "request_id": str(uuid4())})
        self.assertContains(response, "Keep this draft", status_code=503)
        self.assertFalse(ChatMessage.objects.filter(message="Keep this draft").exists())

    def test_edit_during_matching_preserves_new_match_and_form(self):
        self.client.force_login(self.poster)
        data = self.publish_data()
        data["action"] = "save"
        def match_during_review(*args):
            self.waiting()
            return {"flagged": False}
        with patch("plusone.views.moderate_activity_form", side_effect=match_during_review):
            response = self.client.post(reverse("edit_post", args=[self.post.pk]), data)
        self.assertContains(response, "Your changes were not saved", status_code=409)
        self.assertContains(response, data["description"], status_code=409)
        self.post.refresh_from_db()
        self.assertEqual(self.post.title, "Library session")
        self.assertEqual(self.post.status, "matched")
        self.assertFalse(ProductEvent.objects.filter(name="publish_card").exists())

    def test_edit_emits_edit_not_publish(self):
        self.client.force_login(self.poster)
        data = self.publish_data()
        data["action"] = "save"
        self.assertEqual(self.client.post(reverse("edit_post", args=[self.post.pk]), data).status_code, 302)
        self.assertEqual(ProductEvent.objects.filter(name="edit_card").count(), 1)
        self.assertFalse(ProductEvent.objects.filter(name="publish_card").exists())

    def test_reset_during_publish_review_prevents_old_identity_write(self):
        self.client.force_login(self.poster)
        def reset_during_review(*args):
            retire_anonymous_identity(self.poster)
            return {"flagged": False}
        data = self.publish_data()
        with patch("plusone.views.moderate_activity_form", side_effect=reset_during_review):
            response = self.client.post(reverse("create_post"), data)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ActivityPost.objects.filter(request_id=data["request_id"]).exists())

    def test_poll_state_includes_flags_cursor_and_server_clock(self):
        match = self.chatting()
        self.client.force_login(self.poster)
        url = reverse("chat_messages", args=[match.pk])
        first = self.client.get(url).json()
        record_agreement(match.pk, self.guest)
        second = self.client.get(url, {"after": first["next_cursor"]}).json()
        self.assertFalse(first["other_agreed"])
        self.assertTrue(second["other_agreed"])
        self.assertEqual(second["next_cursor"], first["next_cursor"])
        self.assertIn("server_time", second)
        self.assertIn("phase_deadline", second)

    def test_session_updates_does_not_create_identity_or_leak_participants(self):
        count = get_user_model().objects.count()
        response = self.client.get(reverse("session_updates"))
        self.assertFalse(response.json()["authenticated"])
        self.assertEqual(get_user_model().objects.count(), count)
        self.waiting()
        self.client.force_login(self.poster)
        data = self.client.get(reverse("session_updates")).json()
        self.assertEqual(data["waiting_count"], 1)
        self.assertNotIn(self.guest.username, str(data))
        self.assertNotIn("swiper", str(data))

    def test_input_limit_final_and_raw(self):
        self.client.force_login(self.poster)
        for key in ["raw_text", "description"]:
            data = self.publish_data()
            data[key] = "x" * 2001
            response = self.client.post(reverse("create_post"), data)
            self.assertNotEqual(response.status_code, 302)
            self.assertFalse(ActivityPost.objects.filter(request_id=data["request_id"]).exists())

    def test_database_rate_limit_and_retry_after(self):
        self.client.force_login(self.poster)
        for _ in range(6):
            consume_limit(self.poster, "ai")
        response = self.client.post(reverse("create_post"), {"action": "assist", "raw_text": "Library together"})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)

    def test_uuid_case_does_not_consume_quota_twice(self):
        key = str(uuid4())
        for _ in range(9):
            consume_limit(self.poster, "publish")
        consume_limit(self.poster, "publish", request_id=key.upper(), digest="same")
        consume_limit(self.poster, "publish", request_id=key.lower(), digest="same")
        from plusone.models import RateLimitBucket
        self.assertEqual(RateLimitBucket.objects.get(user=self.poster, scope="publish").count, 10)

    def test_event_references_survive_business_record_deletion(self):
        from plusone.management.commands.funnel_report import build_report
        match = self.chatting()
        record_agreement(match.pk, self.poster)
        record_agreement(match.pk, self.guest)
        before = build_report(7)["funnel"]
        self.post.delete()
        after = build_report(7)["funnel"]
        self.assertEqual(before, after)
        self.assertEqual(after["matches_both_agreed"], 1)

    def test_readiness_does_not_create_sessions(self):
        response = self.client.get(reverse("readyz"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sessionid", response.cookies)

    def test_provider_total_deadline_cancels_inflight_request(self):
        from plusone.ai_services.client import chat_completion
        state = {"cancelled": False, "closed": False}
        class FakeClient:
            def __init__(self):
                self.chat = self.completions = self
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                state["closed"] = True
            async def create(self, **kwargs):
                try:
                    await asyncio.sleep(1)
                finally:
                    state["cancelled"] = True
        with self.assertRaises(TimeoutError):
            chat_completion(FakeClient(), {"model": "test", "strategy": "openai"}, timeout=0.01)
        self.assertEqual(state, {"cancelled": True, "closed": True})


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class PostgreSQLConcurrencyTests(Fixtures, TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock races require PostgreSQL")
        super().setUp()

    def parallel(self, funcs):
        barrier = Barrier(len(funcs))
        def run(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return fn()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(funcs)) as pool:
            return list(pool.map(run, funcs))

    def test_simultaneous_presence_activates_exactly_once(self):
        match = self.waiting()
        self.parallel([lambda: set_presence(match.pk, self.poster, True), lambda: set_presence(match.pk, self.guest, True)])
        match.refresh_from_db()
        self.assertEqual(match.status, "chatting")
        self.assertEqual(match.messages.count(), 1)
        self.assertEqual(ProductEvent.objects.filter(name="chat_started").count(), 1)

    def test_competing_matches_hold_only_one_slot(self):
        results = self.parallel([lambda: handle_swipe(self.guest, self.post.pk, "interested"), lambda: handle_swipe(self.outsider, self.post.pk, "interested")])
        self.assertEqual(Match.objects.filter(post=self.post).count(), 1)
        self.assertEqual(sum(r.outcome == "match_created" for r in results), 1)

    def test_simultaneous_duplicate_messages_are_saved_once(self):
        match = self.chatting()
        key = uuid4()
        for _ in range(29):
            consume_limit(self.poster, "message")
        results = self.parallel([lambda: create_chat_message(match, self.poster, "Hello", key), lambda: create_chat_message(match, self.poster, "Hello", key)])
        self.assertEqual(results[0][0].pk, results[1][0].pk)
        self.assertEqual(ChatMessage.objects.filter(request_id=key).count(), 1)
        self.assertEqual(ProductEvent.objects.filter(name="message_sent").count(), 1)

    def test_poll_cannot_advance_past_an_uncommitted_system_message(self):
        from django.test import Client
        match = self.chatting()
        client = Client()
        client.force_login(self.poster)
        entered, release = Event(), Event()
        def writer():
            close_old_connections()
            try:
                with locked_match(match.pk) as current:
                    message = system_message_locked(current, "Ordered system message")
                    entered.set()
                    if not release.wait(timeout=5):
                        raise TimeoutError("test did not release writer")
                    return message.pk
            finally:
                connections.close_all()
        def poll():
            close_old_connections()
            try:
                return client.get(reverse("chat_messages", args=[match.pk])).json()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            write_result = pool.submit(writer)
            self.assertTrue(entered.wait(timeout=5))
            poll_result = pool.submit(poll)
            try:
                with self.assertRaises(TimeoutError):
                    poll_result.result(timeout=0.1)
            finally:
                release.set()
            message_id = write_result.result(timeout=5)
            payload = poll_result.result(timeout=5)
        self.assertIn(message_id, [row["id"] for row in payload["messages"]])
        self.assertGreaterEqual(payload["next_cursor"], message_id)
