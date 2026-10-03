import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from plusone.models import ActivityPost, ActivityReport, BrowserBudgetBucket, CampusLocation, Match, PresenceLease, ProductEvent, PushDelivery, PushSubscription, UserProfile
from plusone.services.cleanup import cleanup_stale_records, stale_anonymous_users
from plusone.services.operations import production_audit


class ProductOperationsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.poster = User.objects.create_user("anon_operations_publisher")
        self.guest = User.objects.create_user("anon_operations_guest")
        self.post = ActivityPost.objects.create(user=self.poster, title="Operations acceptance plan", description="",
            activity_type=ActivityPost.ActivityType.STUDY, location=CampusLocation.objects.first(),
            start_time=timezone.now() - timedelta(days=3), expire_time=timezone.now() - timedelta(days=2))
        self.match = Match.objects.create(post=self.post, poster=self.poster, swiper=self.guest,
            status=Match.Status.WAITING, waiting_expires_at=timezone.now() - timedelta(days=2))

    def stale_actors(self):
        for user in (self.poster, self.guest):
            UserProfile.objects.update_or_create(user=user, defaults={"last_seen_at": timezone.now() - timedelta(days=10)})

    def test_default_maintenance_is_a_nonmutating_preview(self):
        self.stale_actors()
        before = {"post": self.post.status, "match": self.match.status,
                  "users": get_user_model().objects.count(), "events": ProductEvent.objects.count()}
        output = StringIO()
        call_command("maintain_product", stdout=output)
        report = json.loads(output.getvalue())
        self.post.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.post.status, before["post"])
        self.assertEqual(self.match.status, before["match"])
        self.assertEqual(get_user_model().objects.count(), before["users"])
        self.assertEqual(ProductEvent.objects.count(), before["events"])
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["expired_counts"]["matches"], 1)
        self.assertIn("current eligibility", report["preview_basis"])

    def test_committed_maintenance_expires_before_cleanup_and_records_success(self):
        self.stale_actors()
        output = StringIO()
        call_command("maintain_product", commit=True, batch_size=1, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["expired_counts"]["matches"], 1)
        self.assertEqual(report["cleanup_counts"]["users"], 1)
        self.assertEqual(get_user_model().objects.filter(pk__in=[self.poster.pk, self.guest.pk]).count(), 1)
        event = ProductEvent.objects.get(name=ProductEvent.Name.MAINTENANCE_COMPLETED)
        self.assertIsNone(event.user_id)
        self.assertNotIn("browser_key", str(event.properties))
        self.assertTrue(report["successful_run_recorded"])

    def test_deferred_expiry_never_runs_cleanup_or_records_success(self):
        with patch("plusone.management.commands.maintain_product.refresh_expired_records", return_value={"deferred": True}), \
             patch("plusone.management.commands.maintain_product.cleanup_stale_records") as cleanup:
            with self.assertRaisesMessage(CommandError, "cleanup was not run"):
                call_command("maintain_product", commit=True, stdout=StringIO())
            cleanup.assert_not_called()
        self.assertFalse(ProductEvent.objects.filter(name=ProductEvent.Name.MAINTENANCE_COMPLETED).exists())

    def test_expired_browser_budgets_are_bounded_and_only_removed_in_commit(self):
        now = timezone.now()
        for number in range(3):
            BrowserBudgetBucket.objects.create(browser_key=str(number).zfill(64), scope="post",
                window_start=now - timedelta(days=2), expires_at=now - timedelta(days=1))
        BrowserBudgetBucket.objects.create(browser_key="f" * 64, scope="post", window_start=now,
                                          expires_at=now + timedelta(days=1))
        preview = cleanup_stale_records(dry_run=True, batch_size=2)
        self.assertEqual(preview["browser_budget_buckets"], 2)
        self.assertEqual(BrowserBudgetBucket.objects.count(), 4)
        cleanup_stale_records(dry_run=False, batch_size=2)
        self.assertEqual(BrowserBudgetBucket.objects.count(), 2)
        self.assertTrue(BrowserBudgetBucket.objects.filter(browser_key="f" * 64).exists())

    def test_presence_cleanup_keeps_unexpired_and_unknown_expiry_leases(self):
        from uuid import uuid4
        now = timezone.now()
        expired = PresenceLease.objects.create(match=self.match, user=self.poster, tab_id=uuid4(),
            expires_at=now - timedelta(hours=1))
        live = PresenceLease.objects.create(match=self.match, user=self.poster, tab_id=uuid4(),
            expires_at=now + timedelta(hours=1))
        unknown = PresenceLease.objects.create(match=self.match, user=self.guest, tab_id=uuid4())
        self.match.status = Match.Status.EXPIRED
        self.match.save(update_fields=["status"])
        preview = cleanup_stale_records(dry_run=True)
        self.assertEqual(preview["presence_leases"], 1)
        self.assertTrue(PresenceLease.objects.filter(pk=expired.pk).exists())
        cleanup_stale_records(dry_run=False)
        self.assertFalse(PresenceLease.objects.filter(pk=expired.pk).exists())
        self.assertEqual(PresenceLease.objects.filter(pk__in=[live.pk, unknown.pk]).count(), 2)

    def test_expired_live_presence_tombstones_cannot_be_purged(self):
        from uuid import uuid4
        tombstone = PresenceLease.objects.create(match=self.match, user=self.poster, tab_id=uuid4(),
            sequence=17, visible=False, expires_at=timezone.now() - timedelta(hours=1))
        counts = cleanup_stale_records(dry_run=False)
        self.assertEqual(counts["presence_leases"], 0)
        tombstone.refresh_from_db()
        self.assertEqual(tombstone.sequence, 17)

    def test_inactive_retired_push_endpoints_are_removed_without_touching_active_subscription(self):
        UserProfile.objects.update_or_create(user=self.poster, defaults={"retired_at": timezone.now()})
        retired = PushSubscription.objects.create(user=self.poster, endpoint="https://push.example.test/retired", endpoint_digest="r" * 64,
            p256dh="test-key", auth="test-auth", is_active=False)
        live = PushSubscription.objects.create(user=self.guest, endpoint="https://push.example.test/active", endpoint_digest="a" * 64,
            p256dh="test-key", auth="test-auth")
        inactive = PushSubscription.objects.create(user=self.guest, endpoint="https://push.example.test/inactive", endpoint_digest="i" * 64,
            p256dh="test-key", auth="test-auth", is_active=False)
        PushDelivery.objects.create(subscription=retired, notification_key="expired:test")
        preview = cleanup_stale_records(dry_run=True)
        self.assertEqual(preview["push_subscriptions"], 1)
        self.assertTrue(PushSubscription.objects.filter(pk=retired.pk).exists())
        cleanup_stale_records(dry_run=False)
        self.assertFalse(PushSubscription.objects.filter(pk=retired.pk).exists())
        self.assertTrue(PushSubscription.objects.filter(pk=live.pk).exists())
        self.assertTrue(PushSubscription.objects.filter(pk=inactive.pk).exists())

    def test_old_delivery_ledger_is_kept_for_a_still_current_future_plan(self):
        future = timezone.now() + timedelta(days=120)
        self.post.start_time = future
        self.post.save(update_fields=["start_time"])
        self.match.status = Match.Status.AGREED
        self.match.plan_meeting_at = future
        self.match.plan_expected_end_at = future + timedelta(hours=1)
        self.match.save(update_fields=["status", "plan_meeting_at", "plan_expected_end_at"])
        subscription = PushSubscription.objects.create(user=self.poster, endpoint="https://push.example.test/future", endpoint_digest="f" * 64,
            p256dh="test-key", auth="test-auth")
        current = PushDelivery.objects.create(subscription=subscription, notification_key=f"plan:{self.match.pk}:{self.match.plan_revision}")
        expired = PushDelivery.objects.create(subscription=subscription, notification_key="expired:test")
        PushDelivery.objects.filter(pk__in=[current.pk, expired.pk]).update(created_at=timezone.now() - timedelta(days=91))
        counts = cleanup_stale_records(dry_run=False)
        self.assertEqual(counts["push_deliveries"], 1)
        self.assertTrue(PushDelivery.objects.filter(pk=current.pk).exists())
        self.assertFalse(PushDelivery.objects.filter(pk=expired.pk).exists())

    def test_unresolved_activity_report_preserves_publisher_and_reporter_then_expires(self):
        self.stale_actors()
        self.match.delete()
        self.post.status = ActivityPost.Status.EXPIRED
        self.post.save(update_fields=["status"])
        report = ActivityReport.objects.create(post=self.post, reporter=self.guest, reason="Test safety evidence")
        self.assertFalse(stale_anonymous_users().filter(pk__in=[self.poster.pk, self.guest.pk]).exists())
        counts = cleanup_stale_records(dry_run=False)
        self.assertEqual(counts["users"], 0)
        self.assertTrue(ActivityReport.objects.filter(pk=report.pk).exists())
        ActivityReport.objects.filter(pk=report.pk).update(created_at=timezone.now() - timedelta(days=91))
        counts = cleanup_stale_records(dry_run=False)
        self.assertEqual(counts["activity_reports"], 1)
        self.assertEqual(counts["users"], 2)

    @override_settings(DEBUG=False, PLUSONE_MODERATION_MODE="external", PLUSONE_NEW_MATCHES_ENABLED=True)
    def test_audit_reports_presence_without_secrets_or_live_connectivity_claims(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "private-api-value", "DATABASE_URL": "private-db-url"}), \
             patch("plusone.services.operations._commit_identity", return_value=("a" * 40, "RENDER_GIT_COMMIT")):
            report = production_audit("a" * 40)
        rendered = json.dumps(report)
        self.assertNotIn("private-api-value", rendered)
        self.assertNotIn("private-db-url", rendered)
        self.assertTrue(report["external_credentials_present"])
        self.assertTrue(report["database_ready"])
        self.assertTrue(report["configuration_checks_passed"])
        self.assertFalse(report["production_verified"])
        self.assertEqual(report["maintenance"]["status"], "unknown")

    @override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules", PLUSONE_NEW_MATCHES_ENABLED=False)
    def test_strict_audit_fails_with_clear_configuration_facts(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""}):
            output = StringIO()
            with self.assertRaises(CommandError):
                call_command("production_audit", strict=True, stdout=output)
        report = json.loads(output.getvalue())
        self.assertIn("new_matching_disabled", report["issues"])
        self.assertIn("external_credentials_absent", report["issues"])
        self.assertEqual(report["maintenance"]["status"], "unknown")

    def import_places(self, rows, **options):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "public-campus-places.json"
            path.write_text(json.dumps(rows))
            output = StringIO()
            call_command("configure_campus_locations", file=str(path), stdout=output, **options)
        return json.loads(output.getvalue())

    def test_campus_import_preview_changes_nothing_and_commit_is_idempotent(self):
        rows = [{"name": "Acceptance public library entrance", "area": "Acceptance campus",
                 "location_type": "study", "latitude": 52.5, "longitude": 13.4}]
        before = CampusLocation.objects.count()
        preview = self.import_places(rows)
        self.assertEqual(preview["mode"], "dry_run")
        self.assertEqual(CampusLocation.objects.count(), before)
        self.import_places(rows, commit=True)
        place = CampusLocation.objects.get(name=rows[0]["name"])
        self.assertEqual(float(place.latitude), 52.5)
        again = self.import_places(rows, commit=True)
        self.assertEqual(again["create"], [])
        self.assertEqual(again["update"], [])
        self.assertEqual(CampusLocation.objects.count(), before + 1)

    def test_campus_import_only_removes_explicitly_unlisted_unused_places(self):
        used_id = self.post.location_id
        unreferenced = CampusLocation.objects.create(name="Acceptance unused public place", area="Acceptance campus", location_type="other")
        rows = [{"name": "Acceptance operator-supplied place", "area": "Acceptance campus", "location_type": "outdoor"}]
        self.import_places(rows, commit=True)
        self.assertTrue(CampusLocation.objects.filter(pk=unreferenced.pk).exists())
        preview = self.import_places(rows, remove_unused_unlisted=True)
        self.assertIn(self.post.location.name, preview["preserve_referenced_unlisted"])
        self.assertTrue(CampusLocation.objects.filter(pk=unreferenced.pk).exists())
        self.import_places(rows, commit=True, remove_unused_unlisted=True)
        self.assertFalse(CampusLocation.objects.filter(pk=unreferenced.pk).exists())
        self.assertTrue(CampusLocation.objects.filter(pk=used_id).exists())
        self.assertTrue(ActivityPost.objects.filter(pk=self.post.pk).exists())

    def test_invalid_campus_coordinates_or_duplicate_names_never_write(self):
        before = CampusLocation.objects.count()
        base = {"name": "Acceptance operator-supplied place", "area": "Acceptance campus", "location_type": "study"}
        for rows in ([dict(base, latitude=95, longitude=13)], [base, base], [dict(base, secret="not allowed")]):
            with self.assertRaises(CommandError):
                self.import_places(rows, commit=True)
            self.assertEqual(CampusLocation.objects.count(), before)

    @override_settings(DEBUG=False)
    def test_normal_demo_seed_is_refused_in_production_without_writing(self):
        users = get_user_model().objects.count()
        posts = ActivityPost.objects.count()
        locations = CampusLocation.objects.count()
        with self.assertRaisesMessage(CommandError, "Demo activities are disabled"):
            call_command("seed_demo", stdout=StringIO())
        self.assertEqual(get_user_model().objects.count(), users)
        self.assertEqual(ActivityPost.objects.count(), posts)
        self.assertEqual(CampusLocation.objects.count(), locations)
