import argparse
from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import resolve
from django.utils import timezone

from plusone.admin import SafetyReportAdmin
from plusone.management.commands.cleanup_anonymous_sessions import report_days
from plusone.middleware import LastSeenMiddleware, record_last_seen
from plusone.models import (
    ActivityPost,
    CampusLocation,
    ChatMessage,
    LLMLog,
    Match,
    ProductEvent,
    RateLimitBucket,
    SafetyReport,
    UserProfile,
)
from plusone.services.cleanup import cleanup_anonymous_users, cleanup_stale_records


class RetentionFixtureMixin:
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.location = CampusLocation.objects.create(
            name=f"Retention Hall {self.__class__.__name__}",
            location_type=CampusLocation.LocationType.OTHER,
            area="Test Campus",
        )

    def make_user(self, username, *, stale=True):
        User = get_user_model()
        user = User.objects.create_user(username=username)
        profile = UserProfile.objects.create(
            user=user,
            display_name=username,
            last_seen_at=self.now - timedelta(days=8) if stale else self.now,
        )
        joined = self.now - timedelta(days=8) if stale else self.now
        User.objects.filter(pk=user.pk).update(date_joined=joined, last_login=joined)
        user.refresh_from_db()
        return user, profile

    def make_post(
        self,
        user,
        *,
        start_time=None,
        expected_end_time=None,
        expire_time=None,
        status=ActivityPost.Status.CANCELLED,
    ):
        return ActivityPost.objects.create(
            user=user,
            title=f"Retention post for {user.username}",
            activity_type=ActivityPost.ActivityType.OTHER,
            location=self.location,
            start_time=start_time or self.now - timedelta(days=2),
            expected_end_time=expected_end_time,
            expire_time=expire_time or self.now - timedelta(days=1),
            status=status,
        )


class LastSeenMiddlewareTests(RetentionFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.user, self.profile = self.make_user("anon_seen")

    def request_for(self, path, method="get"):
        request = getattr(self.factory, method)(path)
        request.user = self.user
        request.resolver_match = resolve(path)
        return request

    def test_last_seen_is_written_after_response_and_throttled(self):
        request = self.request_for("/discover/")
        old_value = self.profile.last_seen_at

        def view(req):
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.last_seen_at, old_value)
            return HttpResponse("ok")

        response = LastSeenMiddleware(view)(request)
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        first_value = self.profile.last_seen_at
        self.assertGreater(first_value, old_value)

        self.assertFalse(record_last_seen(request, now=first_value + timedelta(minutes=4)))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_seen_at, first_value)
        self.assertTrue(record_last_seen(request, now=first_value + timedelta(minutes=6)))

    def test_polling_and_analytics_requests_do_not_refresh_last_seen(self):
        old_value = self.profile.last_seen_at
        requests = [
            self.request_for("/chat/123/messages/"),
            self.request_for("/chat/123/opener-click/", method="post"),
            self.request_for("/healthz/"),
            self.request_for("/readyz/"),
            self.request_for("/session/updates/"),
            self.request_for("/chat/123/presence/", method="post"),
        ]

        for request in requests:
            self.assertFalse(record_last_seen(request))

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_seen_at, old_value)

    def test_existing_identity_is_protected_before_slow_view_starts(self):
        request = self.request_for("/discover/")
        middleware = LastSeenMiddleware(lambda req: HttpResponse("ok"))
        middleware.process_view(request, None, (), {})
        self.profile.refresh_from_db()
        self.assertGreater(self.profile.last_seen_at, self.now - timedelta(minutes=1))
        self.assertEqual(cleanup_anonymous_users(dry_run=False), 0)
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_middleware_records_user_created_by_view_without_creating_accounts_itself(self):
        request = self.factory.get("/discover/")
        request.resolver_match = resolve("/discover/")
        user_count = get_user_model().objects.count()

        def view(req):
            req.user = self.user
            return HttpResponse("ok")

        LastSeenMiddleware(view)(request)
        self.assertEqual(get_user_model().objects.count(), user_count)
        self.profile.refresh_from_db()
        self.assertGreater(self.profile.last_seen_at, self.now - timedelta(days=1))


class AnonymousIdentityRetentionTests(RetentionFixtureMixin, TestCase):
    def test_last_seen_is_primary_and_cleanup_rechecks_under_lock(self):
        user, profile = self.make_user("anon_race")
        profile.last_seen_at = self.now
        profile.save(update_fields=["last_seen_at"])

        with patch("plusone.services.cleanup._candidate_user_ids", return_value=[user.pk]):
            count = cleanup_anonymous_users(days=7, dry_run=False)

        self.assertEqual(count, 0)
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())

    def test_waiting_match_protects_both_participants(self):
        poster, _ = self.make_user("anon_wait_poster")
        swiper, _ = self.make_user("anon_wait_swiper")
        post = self.make_post(poster)
        Match.objects.create(
            post=post,
            poster=poster,
            swiper=swiper,
            status=Match.Status.WAITING,
            waiting_expires_at=self.now - timedelta(days=1),
        )

        self.assertEqual(cleanup_anonymous_users(dry_run=False), 0)
        self.assertEqual(get_user_model().objects.filter(pk__in=[poster.pk, swiper.pk]).count(), 2)

    def test_recent_unresolved_report_protects_both_participants(self):
        poster, _ = self.make_user("anon_report_poster")
        swiper, _ = self.make_user("anon_report_swiper")
        post = self.make_post(poster)
        match = Match.objects.create(
            post=post,
            poster=poster,
            swiper=swiper,
            status=Match.Status.DECLINED,
        )
        SafetyReport.objects.create(
            match=match,
            reporter=poster,
            category=SafetyReport.Category.HARASSMENT,
            reason="Evidence",
        )

        self.assertEqual(cleanup_anonymous_users(dry_run=False), 0)
        self.assertEqual(get_user_model().objects.filter(pk__in=[poster.pk, swiper.pk]).count(), 2)

    def test_old_report_is_removed_but_future_agreement_still_protects_users(self):
        poster, _ = self.make_user("anon_future_poster")
        swiper, _ = self.make_user("anon_future_swiper")
        post = self.make_post(
            poster,
            start_time=self.now + timedelta(hours=3),
            expire_time=self.now - timedelta(minutes=1),
        )
        match = Match.objects.create(
            post=post,
            poster=poster,
            swiper=swiper,
            status=Match.Status.AGREED,
        )
        report = SafetyReport.objects.create(match=match, reporter=poster, reason="Old evidence")
        SafetyReport.objects.filter(pk=report.pk).update(created_at=self.now - timedelta(days=91))
        message = ChatMessage.objects.create(match=match, sender=poster, message="Historical evidence")
        ChatMessage.objects.filter(pk=message.pk).update(created_at=self.now - timedelta(days=91))

        counts = cleanup_stale_records(dry_run=False)

        self.assertEqual(counts["safety_reports"], 1)
        self.assertEqual(counts["report_messages"], 0)
        self.assertFalse(SafetyReport.objects.filter(pk=report.pk).exists())
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())
        self.assertEqual(get_user_model().objects.filter(pk__in=[poster.pk, swiper.pk]).count(), 2)

    def test_expected_end_protects_both_participants_until_twenty_four_hours_later(self):
        poster, _ = self.make_user("anon_long_meet_poster")
        swiper, _ = self.make_user("anon_long_meet_swiper")
        post = self.make_post(
            poster,
            start_time=self.now - timedelta(days=2),
            expected_end_time=self.now - timedelta(hours=23),
            expire_time=self.now - timedelta(days=2),
            status=ActivityPost.Status.MATCHED,
        )
        Match.objects.create(post=post, poster=poster, swiper=swiper, status=Match.Status.AGREED)

        self.assertEqual(cleanup_anonymous_users(dry_run=False), 0)
        self.assertEqual(get_user_model().objects.filter(pk__in=[poster.pk, swiper.pk]).count(), 2)

    def test_expected_end_outside_twenty_four_hours_allows_cleanup(self):
        poster, _ = self.make_user("anon_ended_poster")
        swiper, _ = self.make_user("anon_ended_swiper")
        post = self.make_post(
            poster,
            start_time=self.now - timedelta(days=3),
            expected_end_time=self.now - timedelta(hours=25),
            expire_time=self.now - timedelta(days=3),
            status=ActivityPost.Status.MATCHED,
        )
        Match.objects.create(post=post, poster=poster, swiper=swiper, status=Match.Status.AGREED)

        self.assertEqual(cleanup_anonymous_users(dry_run=False), 2)
        self.assertEqual(get_user_model().objects.filter(pk__in=[poster.pk, swiper.pk]).count(), 0)

    def test_user_cleanup_is_bounded_and_repeatable(self):
        users = [self.make_user(f"anon_batch_{index}")[0] for index in range(3)]

        self.assertEqual(cleanup_anonymous_users(dry_run=True, batch_size=2), 2)
        self.assertEqual(get_user_model().objects.filter(pk__in=[user.pk for user in users]).count(), 3)
        self.assertEqual(cleanup_anonymous_users(dry_run=False, batch_size=2), 2)
        self.assertEqual(cleanup_anonymous_users(dry_run=False, batch_size=2), 1)
        self.assertEqual(cleanup_anonymous_users(dry_run=False, batch_size=2), 0)


class IndependentRecordRetentionTests(RetentionFixtureMixin, TestCase):
    def test_new_report_does_not_extend_old_terminal_evidence_retention(self):
        reporter, _ = self.make_user("anon_new_reporter")
        participant, _ = self.make_user("anon_new_participant")
        post = self.make_post(reporter)
        match = Match.objects.create(
            post=post, poster=reporter, swiper=participant, status=Match.Status.DECLINED,
        )
        report = SafetyReport.objects.create(match=match, reporter=reporter, reason="New report")
        message = ChatMessage.objects.create(match=match, sender=participant, message="Old evidence")
        ChatMessage.objects.filter(pk=message.pk).update(created_at=self.now - timedelta(days=91))

        self.assertEqual(cleanup_stale_records(dry_run=True)["report_messages"], 1)
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())
        counts = cleanup_stale_records(dry_run=False)
        self.assertEqual(counts["report_messages"], 1)
        self.assertFalse(ChatMessage.objects.filter(pk=message.pk).exists())
        self.assertTrue(SafetyReport.objects.filter(pk=report.pk).exists())
        self.assertEqual(get_user_model().objects.filter(pk__in=[reporter.pk, participant.pk]).count(), 2)
        self.assertEqual(cleanup_stale_records(dry_run=False)["report_messages"], 0)

    def test_terminal_match_messages_are_purged_after_evidence_window(self):
        reporter, _ = self.make_user("terminal_reporter", stale=False)
        participant, _ = self.make_user("terminal_participant", stale=False)
        post = self.make_post(reporter)
        match = Match.objects.create(
            post=post,
            poster=reporter,
            swiper=participant,
            status=Match.Status.DECLINED,
        )
        report = SafetyReport.objects.create(match=match, reporter=reporter, reason="Old")
        message = ChatMessage.objects.create(match=match, sender=participant, message="Old evidence")
        old_time = self.now - timedelta(days=91)
        SafetyReport.objects.filter(pk=report.pk).update(created_at=old_time)
        ChatMessage.objects.filter(pk=message.pk).update(created_at=old_time)

        dry_counts = cleanup_stale_records(dry_run=True)
        self.assertEqual(dry_counts["safety_reports"], 1)
        self.assertEqual(dry_counts["report_messages"], 1)
        self.assertTrue(SafetyReport.objects.filter(pk=report.pk).exists())
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())

        counts = cleanup_stale_records(dry_run=False)
        self.assertEqual(counts["safety_reports"], 1)
        self.assertEqual(counts["report_messages"], 1)
        self.assertFalse(SafetyReport.objects.filter(pk=report.pk).exists())
        self.assertFalse(ChatMessage.objects.filter(pk=message.pk).exists())

    def test_dry_run_and_commit_cover_all_independent_retention_classes(self):
        reporter, _ = self.make_user("retention_reporter", stale=False)
        participant, _ = self.make_user("retention_participant", stale=False)
        post = self.make_post(reporter)
        match = Match.objects.create(
            post=post,
            poster=reporter,
            swiper=participant,
            status=Match.Status.DECLINED,
        )
        old_report = SafetyReport.objects.create(match=match, reporter=reporter, reason="Old")
        fresh_report = SafetyReport.objects.create(match=match, reporter=participant, reason="Fresh")
        SafetyReport.objects.filter(pk=old_report.pk).update(created_at=self.now - timedelta(days=91))

        old_log = LLMLog.objects.create(
            user=reporter,
            task_type=LLMLog.TaskType.MODERATION,
            input_text="Old",
        )
        fresh_log = LLMLog.objects.create(
            user=reporter,
            task_type=LLMLog.TaskType.MODERATION,
            input_text="Fresh",
        )
        LLMLog.objects.filter(pk=old_log.pk).update(created_at=self.now - timedelta(days=31))
        old_event = ProductEvent.objects.create(name=ProductEvent.Name.PUBLISH_CARD, user=reporter)
        fresh_event = ProductEvent.objects.create(name=ProductEvent.Name.PUBLISH_CARD, user=reporter)
        ProductEvent.objects.filter(pk=old_event.pk).update(created_at=self.now - timedelta(days=91))

        old_session = Session.objects.create(
            session_key="retention-old-session",
            session_data="",
            expire_date=self.now - timedelta(seconds=1),
        )
        fresh_session = Session.objects.create(
            session_key="retention-fresh-session",
            session_data="",
            expire_date=self.now + timedelta(days=1),
        )
        old_bucket = RateLimitBucket.objects.create(
            user=reporter,
            scope="old",
            window_start=self.now - timedelta(hours=1),
            count=1,
            expires_at=self.now - timedelta(seconds=1),
        )
        fresh_bucket = RateLimitBucket.objects.create(
            user=reporter,
            scope="fresh",
            window_start=self.now,
            count=1,
            expires_at=self.now + timedelta(hours=1),
        )

        dry_counts = cleanup_stale_records(dry_run=True)
        self.assertEqual(
            {key: dry_counts[key] for key in ("llm_logs", "events", "safety_reports", "sessions", "rate_limit_buckets")},
            {"llm_logs": 1, "events": 1, "safety_reports": 1, "sessions": 1, "rate_limit_buckets": 1},
        )
        for model, pk in (
            (LLMLog, old_log.pk),
            (ProductEvent, old_event.pk),
            (SafetyReport, old_report.pk),
            (Session, old_session.pk),
            (RateLimitBucket, old_bucket.pk),
        ):
            self.assertTrue(model.objects.filter(pk=pk).exists())

        cleanup_stale_records(dry_run=False)
        for model, old_pk, fresh_pk in (
            (LLMLog, old_log.pk, fresh_log.pk),
            (ProductEvent, old_event.pk, fresh_event.pk),
            (SafetyReport, old_report.pk, fresh_report.pk),
            (Session, old_session.pk, fresh_session.pk),
            (RateLimitBucket, old_bucket.pk, fresh_bucket.pk),
        ):
            self.assertFalse(model.objects.filter(pk=old_pk).exists())
            self.assertTrue(model.objects.filter(pk=fresh_pk).exists())

    def test_report_retention_cannot_exceed_ninety_days(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            report_days("91")


class SafetyReportAdminTests(RetentionFixtureMixin, TestCase):
    def test_only_status_and_handling_notes_are_editable(self):
        model_admin = admin.site._registry[SafetyReport]

        self.assertIsInstance(model_admin, SafetyReportAdmin)
        self.assertEqual(set(model_admin.fields) - set(model_admin.readonly_fields), {"status", "handling_notes"})
        self.assertNotIn("match", model_admin.fields)
        self.assertNotIn("reporter", model_admin.fields)
