"""Web Push acceptance uses fixed synthetic keys and mocked providers only."""

import base64
import io
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import ActivityPost, CampusLocation, Match, PushDelivery, PushSubscription, UserBlock, UserProfile
from .services.push_notifications import _claim, _provider_send, push_status, run_push_updates, subscribe, unsubscribe
from .services.requests import RequestError


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


# The published P-256 generator point and scalar 1 are test vectors, not keys
# generated for, installed on, or sent by any deployment.
PUBLIC = encoded(bytes.fromhex(
    "04"
    "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
    "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5"
))
PRIVATE = encoded(bytes.fromhex("00" * 31 + "01"))
AUTH = encoded(b"synthetic-auth12")
PUSH_SETTINGS = {
    "PLUSONE_WEB_PUSH_ENABLED": True,
    "PLUSONE_VAPID_PUBLIC_KEY": PUBLIC, "PLUSONE_VAPID_PRIVATE_KEY": PRIVATE,
    "PLUSONE_VAPID_SUBJECT": "mailto:push-operator@example.test",
}


@override_settings(**PUSH_SETTINGS)
class WebPushAcceptanceTests(TransactionTestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=dt_timezone.utc)
        clock = patch("django.utils.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        users = get_user_model()
        self.owner = users.objects.create(username="push_owner")
        self.guest = users.objects.create(username="push_guest")
        self.outsider = users.objects.create(username="push_outsider")
        self.location = CampusLocation.objects.create(name="Test public library", area="Test", location_type="study")
        self.post = ActivityPost.objects.create(
            user=self.owner, title="Do not expose this activity title on a locked screen", activity_type="study",
            location=self.location, start_time=self.now + timedelta(minutes=20), expected_end_time=self.now + timedelta(hours=1),
            expire_time=self.now + timedelta(minutes=45), status=ActivityPost.Status.MATCHED,
        )
        self.match = Match.objects.create(
            post=self.post, poster=self.owner, swiper=self.guest, status=Match.Status.WAITING,
            waiting_expires_at=self.now + timedelta(minutes=10),
        )

    def browser_subscription(self, suffix="fixed-test-token", endpoint=None, auth=AUTH):
        return {"endpoint": endpoint or f"https://fcm.googleapis.com/fcm/send/{suffix}", "keys": {"p256dh": PUBLIC, "auth": auth}}

    def test_readiness_exposes_only_public_key_and_requires_complete_matching_configuration(self):
        self.assertEqual(push_status(), {"available": True, "public_key": PUBLIC})
        for config in (
            {"PLUSONE_WEB_PUSH_ENABLED": False}, {"PLUSONE_VAPID_PRIVATE_KEY": ""}, {"PLUSONE_VAPID_PUBLIC_KEY": "wrong"},
            {"PLUSONE_VAPID_PRIVATE_KEY": encoded(bytes.fromhex("00" * 31 + "02"))}, {"PLUSONE_VAPID_SUBJECT": "http://localhost"},
        ):
            with self.subTest(config=list(config)), override_settings(**config):
                self.assertEqual(push_status(), {"available": False, "public_key": ""})
        with override_settings(PLUSONE_VAPID_SUBJECT="https://operator.example.test/contact"):
            self.assertTrue(push_status()["available"])

    def test_subscribe_requires_opt_in_configuration_and_is_idempotent(self):
        with override_settings(PLUSONE_WEB_PUSH_ENABLED=False):
            with self.assertRaises(RequestError) as error:
                subscribe(self.owner, self.browser_subscription())
            self.assertEqual(error.exception.status, 503)
        first = subscribe(self.owner, self.browser_subscription())
        replay = subscribe(self.owner, self.browser_subscription())
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(PushSubscription.objects.count(), 1)

    def test_endpoint_validation_prevents_custom_hosts_private_addresses_credentials_and_http(self):
        for endpoint in (
            "http://fcm.googleapis.com/push/token", "https://127.0.0.1/token", "https://localhost/token",
            "https://10.0.0.1/token", "https://[::1]/token", "https://push.example.test/token",
            "https://fcm.googleapis.com.attacker.test/token", "https://user:password@fcm.googleapis.com/token",
            "https://fcm.googleapis.com:8443/token", "https://fcm.googleapis.com/token#fragment",
            "https://fcm.googleapis.com/token\n", "https://fcm.googleapis.com@attacker.test/token",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(RequestError):
                subscribe(self.owner, self.browser_subscription(endpoint=endpoint))
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_configured_allowlist_cannot_enable_arbitrary_ssrf_hosts(self):
        with override_settings(PLUSONE_WEB_PUSH_ALLOWED_HOSTS=["push.example.test"]):
            with self.assertRaises(RequestError):
                subscribe(self.owner, self.browser_subscription(endpoint="https://push.example.test/token"))
            with self.assertRaises(RequestError):
                subscribe(self.owner, self.browser_subscription())

    def test_invalid_browser_encryption_material_is_rejected(self):
        for key, value in (("auth", "bad"), ("auth", "*" * 24), ("p256dh", encoded(b"\x04" + b"\x00" * 64))):
            payload = self.browser_subscription()
            payload["keys"][key] = value
            with self.subTest(key=key), self.assertRaises(RequestError):
                subscribe(self.owner, payload)

    def test_supported_mozilla_apple_and_windows_hosts_are_accepted(self):
        for endpoint in (
            "https://updates.push.services.mozilla.com/wpush/v2/test-vector",
            "https://web.push.apple.com/Q/test-vector", "https://wns2-test.notify.windows.com/?token=test-vector",
        ):
            subscribe(self.owner, self.browser_subscription(endpoint=endpoint))
        self.assertEqual(PushSubscription.objects.count(), 3)

    def test_foreign_active_subscription_cannot_be_stolen_or_unsubscribed(self):
        saved = subscribe(self.owner, self.browser_subscription())
        with self.assertRaises(RequestError) as error:
            subscribe(self.outsider, self.browser_subscription())
        self.assertEqual(error.exception.status, 409)
        self.assertFalse(unsubscribe(self.outsider, saved.endpoint))
        saved.refresh_from_db()
        self.assertTrue(saved.is_active)
        self.assertTrue(unsubscribe(self.owner, saved.endpoint))

    def test_unsubscribe_uses_the_deactivation_time_for_inactive_retention(self):
        saved = subscribe(self.owner, self.browser_subscription())
        PushSubscription.objects.filter(pk=saved.pk).update(updated_at=self.now - timedelta(days=40))
        unsubscribe(self.owner, saved.endpoint)
        saved.refresh_from_db()
        self.assertFalse(saved.is_active)
        self.assertEqual(saved.updated_at, self.now)

    def test_identity_reset_rebind_requires_inactive_retired_owner_and_matching_keys(self):
        saved = subscribe(self.owner, self.browser_subscription())
        PushDelivery.objects.create(subscription=saved, notification_key="waiting:old-user", delivered_at=self.now)
        saved.is_active = False
        saved.save(update_fields=["is_active"])
        with self.assertRaises(RequestError):
            subscribe(self.outsider, self.browser_subscription())
        UserProfile.objects.create(user=self.owner, display_name="Old", retired_at=self.now)
        with self.assertRaises(RequestError):
            subscribe(self.outsider, self.browser_subscription(auth=encoded(b"different-auth12")))
        rebound = subscribe(self.outsider, self.browser_subscription())
        self.assertEqual(rebound.pk, saved.pk)
        self.assertEqual(rebound.user_id, self.outsider.pk)
        self.assertFalse(rebound.push_deliveries.exists())
        sender = Mock()
        run_push_updates(send=True, sender=sender)
        sender.assert_not_called()

    def test_device_quota_is_bounded_and_existing_device_replay_still_works(self):
        for index in range(5):
            subscribe(self.owner, self.browser_subscription(suffix=f"test-{index}"))
        with self.assertRaises(RequestError) as error:
            subscribe(self.owner, self.browser_subscription(suffix="too-many"))
        self.assertEqual(error.exception.status, 429)
        subscribe(self.owner, self.browser_subscription(suffix="test-0"))

    def test_dry_run_does_not_contact_provider_or_write_delivery_state(self):
        saved = subscribe(self.owner, self.browser_subscription())
        before = saved.updated_at
        sender = Mock()
        report = run_push_updates(sender=sender)
        self.assertEqual(report["eligible"], 1)
        sender.assert_not_called()
        self.assertFalse(PushDelivery.objects.exists())
        saved.refresh_from_db()
        self.assertEqual(saved.updated_at, before)

    def test_delivery_runs_outside_locks_with_private_body_and_replay_is_not_resent(self):
        saved = subscribe(self.owner, self.browser_subscription())
        captured = []

        def provider(subscription, payload):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(subscription.user_id, self.owner.pk)
            captured.append(payload)
            return SimpleNamespace(status_code=201)

        first = run_push_updates(send=True, sender=provider)
        replay = run_push_updates(send=True, sender=provider)
        self.assertEqual((first["delivered"], replay["delivered"]), (1, 0))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["url"], f"/chat/{self.match.pk}/")
        self.assertNotIn(self.post.title, json.dumps(captured))
        self.assertNotIn(saved.endpoint, json.dumps(captured))
        ledger = PushDelivery.objects.get(subscription=saved)
        self.assertEqual(ledger.attempts, 1)
        self.assertIsNotNone(ledger.delivered_at)

    def test_nonparticipant_rows_are_never_delivered_even_if_update_helper_is_wrong(self):
        subscribe(self.outsider, self.browser_subscription())
        fake = {"id": f"waiting:{self.match.pk}", "url": f"/chat/{self.match.pk}/", "title": "Private", "body": "Secret",
                "created_at": self.now.isoformat(), "is_read": False}
        sender = Mock()
        with patch("plusone.services.push_notifications.notification_rows", return_value=[fake]):
            run_push_updates(send=True, sender=sender)
        sender.assert_not_called()
        self.assertFalse(PushDelivery.objects.exists())

    def test_provider_gone_disables_subscription_and_stores_only_error_code(self):
        saved = subscribe(self.owner, self.browser_subscription())
        secret_error = Exception(f"secret endpoint {saved.endpoint}")
        secret_error.response = SimpleNamespace(status_code=410)
        sender = Mock(side_effect=secret_error)
        report = run_push_updates(send=True, sender=sender)
        self.assertEqual((report["failed"], report["expired"]), (1, 1))
        saved.refresh_from_db()
        self.assertFalse(saved.is_active)
        self.assertEqual(PushDelivery.objects.get(subscription=saved).last_error_code, "provider_410")
        run_push_updates(send=True, sender=sender)
        self.assertEqual(sender.call_count, 1)

    def test_temporary_failure_retries_after_backoff_then_stops_at_limit(self):
        subscribe(self.owner, self.browser_subscription())
        payload = {"notification_id": "synthetic:retry", "tag": "plusone:synthetic:retry", "title": "Update", "body": "Open your plan", "url": "/dashboard/"}
        sender = Mock(return_value=SimpleNamespace(status_code=503))
        with patch("plusone.services.push_notifications._eligible_rows", return_value=[payload]):
            run_push_updates(send=True, sender=sender)
            run_push_updates(send=True, sender=sender)
            self.assertEqual(sender.call_count, 1)
            for attempt in range(2, 6):
                retry_at = PushDelivery.objects.get().next_attempt_at
                with patch("django.utils.timezone.now", return_value=retry_at):
                    run_push_updates(send=True, sender=sender)
                self.assertEqual(PushDelivery.objects.get().attempts, attempt)
            with patch("django.utils.timezone.now", return_value=self.now + timedelta(days=1)):
                run_push_updates(send=True, sender=sender)
            self.assertEqual(sender.call_count, 5)

    def test_claim_lease_prevents_two_workers_from_claiming_same_delivery(self):
        saved = subscribe(self.owner, self.browser_subscription())
        self.assertIsNotNone(_claim(saved.pk, "synthetic:lease", self.now))
        self.assertIsNone(_claim(saved.pk, "synthetic:lease", self.now))
        self.assertEqual(PushDelivery.objects.get().attempts, 1)

    def _reset_and_rebind_subscription(self, saved):
        UserProfile.objects.create(user=self.owner, display_name="Retired", retired_at=self.now)
        PushSubscription.objects.filter(pk=saved.pk).update(is_active=False)
        return subscribe(self.outsider, self.browser_subscription())

    def test_rebind_after_scan_before_claim_cannot_deliver_previous_owners_payload(self):
        saved = subscribe(self.owner, self.browser_subscription())

        def rebound_claim(subscription_id, key, now, *, expected_user_id=None):
            self.assertEqual(expected_user_id, self.owner.pk)
            self._reset_and_rebind_subscription(saved)
            return _claim(subscription_id, key, now, expected_user_id=expected_user_id)

        sender = Mock(return_value=SimpleNamespace(status_code=201))
        with patch("plusone.services.push_notifications._claim", side_effect=rebound_claim):
            report = run_push_updates(send=True, sender=sender)
        sender.assert_not_called()
        self.assertEqual(report["delivered"], 0)
        saved.refresh_from_db()
        self.assertEqual(saved.user_id, self.outsider.pk)
        self.assertFalse(PushDelivery.objects.filter(subscription=saved).exists())

    def test_rebind_after_claim_before_send_cannot_deliver_previous_owners_payload(self):
        saved = subscribe(self.owner, self.browser_subscription())

        def rebound_claim(subscription_id, key, now, *, expected_user_id=None):
            claimed = _claim(subscription_id, key, now, expected_user_id=expected_user_id)
            self.assertIsNotNone(claimed)
            self._reset_and_rebind_subscription(saved)
            return claimed

        sender = Mock(return_value=SimpleNamespace(status_code=201))
        with patch("plusone.services.push_notifications._claim", side_effect=rebound_claim):
            report = run_push_updates(send=True, sender=sender)
        sender.assert_not_called()
        self.assertEqual(report["delivered"], 0)
        saved.refresh_from_db()
        self.assertEqual(saved.user_id, self.outsider.pk)
        self.assertFalse(PushDelivery.objects.filter(subscription=saved).exists())

    def test_stale_wait_and_elapsed_wait_deadline_are_not_notified(self):
        subscribe(self.owner, self.browser_subscription())
        sender = Mock()
        Match.objects.filter(pk=self.match.pk).update(created_at=self.now - timedelta(minutes=11))
        run_push_updates(send=True, sender=sender)
        Match.objects.filter(pk=self.match.pk).update(created_at=self.now, waiting_expires_at=self.now - timedelta(seconds=1))
        run_push_updates(send=True, sender=sender)
        sender.assert_not_called()

    def test_dry_run_skips_legacy_wait_when_activity_ended_before_recruiting_deadline(self):
        subscribe(self.owner, self.browser_subscription())
        ActivityPost.objects.filter(pk=self.post.pk).update(
            start_time=self.now - timedelta(hours=1), expected_end_time=self.now - timedelta(seconds=1),
            expire_time=self.now + timedelta(minutes=45),
        )
        # An old stored wait can still carry a future wait/recruiting deadline.
        # Preview must apply the activity end without changing historical state.
        sender = Mock()
        report = run_push_updates(sender=sender)
        self.assertEqual(report["eligible"], 0)
        sender.assert_not_called()
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.WAITING)
        self.assertFalse(PushDelivery.objects.exists())

    def test_current_near_meeting_reminder_survives_age_cutoff_but_historical_plan_does_not(self):
        subscribe(self.owner, self.browser_subscription())
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.Status.AGREED, plan_meeting_at=self.now + timedelta(minutes=5),
            plan_expected_end_at=self.now + timedelta(hours=1), plan_confirmed_at=self.now - timedelta(hours=1),
        )
        captured = []
        run_push_updates(send=True, sender=lambda sub, payload: captured.append(payload))
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0]["notification_id"].startswith("reminder:"))
        Match.objects.filter(pk=self.match.pk).update(plan_meeting_at=self.now - timedelta(minutes=16), plan_revision=2)
        report = run_push_updates(send=True, sender=lambda sub, payload: captured.append(payload))
        self.assertEqual(report["eligible"], 0)

    def test_retired_identity_never_receives_updates(self):
        subscribe(self.owner, self.browser_subscription())
        UserProfile.objects.create(user=self.owner, display_name="Retired", retired_at=self.now)
        sender = Mock()
        run_push_updates(send=True, sender=sender)
        sender.assert_not_called()

    def test_blocked_unconverged_agreement_never_pushes_ready_plan_or_reminder(self):
        subscribe(self.owner, self.browser_subscription())
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.Status.AGREED, plan_meeting_at=self.now + timedelta(minutes=5),
            plan_expected_end_at=self.now + timedelta(hours=1), plan_confirmed_at=self.now,
        )
        UserBlock.objects.create(blocker=self.guest, target=self.owner)
        sender = Mock(return_value=SimpleNamespace(status_code=201))
        preview = run_push_updates(sender=sender)
        self.assertEqual(preview["eligible"], 0)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.meetup_cancelled_at)
        run_push_updates(send=True, sender=sender)
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.meetup_cancelled_at)
        for call in sender.call_args_list:
            self.assertFalse(call.args[1]["notification_id"].startswith(("plan:", "reminder:")))

    def test_provider_http_session_disables_redirects_and_environment_proxy(self):
        saved = subscribe(self.owner, self.browser_subscription())

        def webpush_mock(**kwargs):
            session = kwargs["requests_session"]
            self.assertFalse(session.trust_env)
            self.assertEqual(kwargs["timeout"], 10)
            self.assertEqual(kwargs["ttl"], 300)
            return session.post(saved.endpoint, data=b"mocked encrypted bytes", timeout=10)

        with patch("pywebpush.webpush", side_effect=webpush_mock), patch("requests.Session.request", return_value=SimpleNamespace(status_code=201)) as request:
            _provider_send(saved, {"title": "Update", "body": "Review your plan", "url": "/dashboard/"})
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_command_defaults_to_dry_run_and_send_fails_closed_when_unconfigured(self):
        subscribe(self.owner, self.browser_subscription())
        output = io.StringIO()
        with patch("plusone.services.push_notifications._provider_send") as provider:
            call_command("send_push_updates", stdout=output)
            provider.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["mode"], "dry_run")
        self.assertNotIn("fixed-test-token", output.getvalue())
        with override_settings(PLUSONE_WEB_PUSH_ENABLED=False), self.assertRaises(CommandError):
            call_command("send_push_updates", "--send", stdout=io.StringIO())

    def test_send_rotates_bounded_subscription_scan_without_starving_newer_devices(self):
        first = subscribe(self.outsider, self.browser_subscription(suffix="no-matches"))
        second = subscribe(self.owner, self.browser_subscription(suffix="has-match"))
        PushSubscription.objects.filter(pk=first.pk).update(updated_at=self.now - timedelta(seconds=2))
        PushSubscription.objects.filter(pk=second.pk).update(updated_at=self.now - timedelta(seconds=1))
        sender = Mock(return_value=SimpleNamespace(status_code=201))
        self.assertEqual(run_push_updates(send=True, batch_size=1, sender=sender)["eligible"], 0)
        self.assertEqual(run_push_updates(send=True, batch_size=1, sender=sender)["delivered"], 1)
