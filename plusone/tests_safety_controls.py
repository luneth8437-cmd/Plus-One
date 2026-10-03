"""Safety controls work before matching and keep report evidence private."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from plusone.admin import ActivityReportAdmin
from plusone.models import ActivityPost, ActivityReport, Match, ProductEvent, SafetyReport, Swipe, UserBlock, UserProfile
from plusone.selectors import discover_context_for_user
from plusone.services.chat import report_match
from plusone.services.matching import handle_swipe, SwipeOutcome
from plusone.services.meetups import plan_payload
from plusone.services.requests import RequestError
from plusone.services.safety import block_post_owner, block_user, blocked_between, blocked_user_ids, own_report_statuses, report_post, unblock_user
from plusone.tests_meetups import MeetupFixtures


class SafetyFixtures(MeetupFixtures):
    def public_post(self, owner=None, title="Public study invitation"):
        return ActivityPost.objects.create(
            user=owner or self.poster, title=title, description="A public campus plan",
            activity_type="study", location=self.location,
            start_time=self.now + timedelta(minutes=30),
            expected_end_time=self.now + timedelta(minutes=90),
            expire_time=self.now + timedelta(hours=2), status=ActivityPost.Status.ACTIVE,
        )

    def rejected(self, callback, status):
        with self.assertRaises(RequestError) as error:
            callback()
        self.assertEqual(error.exception.status, status)


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class PublicCardReportTests(SafetyFixtures, TestCase):
    def test_unmatched_visitor_can_report_and_block_without_expressing_interest(self):
        post = self.public_post()
        match_count, swipe_count = Match.objects.count(), Swipe.objects.count()
        report = report_post(post.pk, self.outsider, "harassment", "This invitation targets me.")
        self.assertEqual(report.post_id, post.pk)
        self.assertEqual(report.reporter_id, self.outsider.pk)
        self.assertEqual(report.status, ActivityReport.Status.PENDING)
        self.assertTrue(blocked_between(self.outsider.pk, self.poster.pk))
        self.assertEqual(Match.objects.count(), match_count)
        self.assertEqual(Swipe.objects.count(), swipe_count)
        post.refresh_from_db()
        self.assertEqual(post.status, ActivityPost.Status.ACTIVE)

    def test_report_without_block_preserves_the_explicit_choice(self):
        post = self.public_post()
        report_post(post.pk, self.outsider, "other", "Please review this card.", block=False)
        self.assertTrue(ActivityReport.objects.filter(post=post, reporter=self.outsider).exists())
        self.assertFalse(UserBlock.objects.exists())

    def test_editing_card_and_supplementing_report_preserves_original_card_evidence(self):
        post = self.public_post(title="Original public invitation")
        original = (post.title, post.description, post.location.name, post.start_time, post.expected_end_time)
        report = report_post(post.pk, self.outsider, reason="Initial card concern.", block=False)
        ActivityPost.objects.filter(pk=post.pk).update(
            title="Rewritten invitation", description="Different description.",
            start_time=self.now + timedelta(minutes=40), expected_end_time=self.now + timedelta(minutes=100),
        )
        type(self.location).objects.filter(pk=self.location.pk).update(name="Renamed campus place")
        supplemented = report_post(post.pk, self.outsider, "contact", "Additional reporter context.", block=False)
        self.assertEqual(supplemented.pk, report.pk)
        self.assertEqual((supplemented.title_snapshot, supplemented.description_snapshot,
                          supplemented.location_snapshot, supplemented.start_time_snapshot,
                          supplemented.expected_end_time_snapshot), original)
        self.assertEqual(own_report_statuses(self.outsider)[0]["title"], original[0])
        evidence = ActivityReportAdmin(ActivityReport, admin.site).post_evidence(supplemented)
        self.assertIn(original[0], evidence)
        self.assertNotIn("Rewritten invitation", evidence)

    def test_supplementing_resolved_report_reopens_the_same_evidence_record(self):
        post = self.public_post()
        report = report_post(post.pk, self.outsider, reason="Initial concern.")
        created_at = report.created_at
        ActivityReport.objects.filter(pk=report.pk).update(
            status=ActivityReport.Status.RESOLVED, handling_notes="Private moderator assessment.",
        )
        supplemented = report_post(post.pk, self.outsider, "contact", "Unwanted contact continued.")
        self.assertEqual(supplemented.pk, report.pk)
        self.assertEqual(supplemented.created_at, created_at)
        self.assertEqual(supplemented.status, ActivityReport.Status.PENDING)
        self.assertEqual(supplemented.category, "contact")
        self.assertEqual(supplemented.reason, "Unwanted contact continued.")
        self.assertEqual(supplemented.handling_notes, "Private moderator assessment.")
        self.assertEqual(ActivityReport.objects.count(), 1)
        self.assertEqual(UserBlock.objects.count(), 1)

    def test_retention_deadline_cannot_reopen_or_reblock_an_old_report(self):
        post = self.public_post()
        report = report_post(post.pk, self.outsider, reason="Original evidence.", block=False)
        cutoff = self.now - timedelta(days=90)
        ActivityReport.objects.filter(pk=report.pk).update(created_at=cutoff, status=ActivityReport.Status.RESOLVED)
        with patch("plusone.services.safety.timezone.now", return_value=self.now):
            self.rejected(lambda: report_post(post.pk, self.outsider, "contact", "New text."), 410)
        report.refresh_from_db()
        self.assertEqual(report.reason, "Original evidence.")
        self.assertEqual(report.status, ActivityReport.Status.RESOLVED)
        self.assertFalse(UserBlock.objects.exists())

    def test_invalid_fields_and_self_reporting_make_no_report_or_block(self):
        post = self.public_post()
        for category, reason in (("unsupported", "Concern"), ("other", "x" * 501)):
            with self.subTest(category=category, length=len(reason)):
                self.rejected(lambda: report_post(post.pk, self.outsider, category, reason), 400)
        self.rejected(lambda: report_post(post.pk, self.poster), 400)
        self.assertFalse(ActivityReport.objects.exists())
        self.assertFalse(UserBlock.objects.exists())

    def test_retired_actor_cannot_report_or_block_a_card(self):
        post = self.public_post()
        UserProfile.objects.create(user=self.outsider, display_name="Retired", retired_at=self.now)
        self.rejected(lambda: report_post(post.pk, self.outsider), 409)
        self.rejected(lambda: block_post_owner(post.pk, self.outsider), 409)
        self.rejected(lambda: block_user(self.outsider, self.poster.pk), 409)
        self.assertFalse(ActivityReport.objects.exists())
        self.assertFalse(UserBlock.objects.exists())

    def test_own_statuses_include_only_reporter_owned_rows_and_hide_moderator_notes(self):
        own_post = self.public_post(title="My reported public card")
        other_post = self.public_post(title="Another person's report")
        own = report_post(own_post.pk, self.outsider, "contact", "Own reporter text.", block=False)
        ActivityReport.objects.filter(pk=own.pk).update(
            status=ActivityReport.Status.IN_PROGRESS, handling_notes="Never disclose moderator notes.",
        )
        ActivityReport.objects.create(post=other_post, reporter=self.swiper, reason="Other reporter's private reason.")
        SafetyReport.objects.create(match=self.match, reporter=self.poster, reason="Other participant evidence.")
        rows = own_report_statuses(self.outsider)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], own_post.title)
        self.assertEqual(rows[0]["status"], "In progress")
        self.assertEqual(rows[0]["category"], "Unwanted contact details")
        self.assertEqual(rows[0]["url"], reverse("post_detail", args=[own_post.pk]))
        self.assertEqual(set(rows[0]), {"title", "category", "status", "created_at", "url"})
        self.assertNotIn("private", str(rows).lower())
        self.assertNotIn("moderator", str(rows).lower())

    def test_own_statuses_link_a_participants_match_report_to_its_history(self):
        report = SafetyReport.objects.create(match=self.match, reporter=self.poster, category="unsafe_meeting")
        rows = own_report_statuses(self.poster)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], self.post.title)
        self.assertEqual(rows[0]["url"], reverse("chat", args=[self.match.pk]))
        self.assertEqual(rows[0]["created_at"], report.created_at)
        self.assertEqual(rows[0]["category"], "Unsafe meeting")


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class IdentityBlockTests(SafetyFixtures, TestCase):
    def test_block_is_idempotent_and_query_helpers_are_bidirectional(self):
        first = block_user(self.poster, self.outsider.pk)
        second = block_user(self.poster, self.outsider.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(UserBlock.objects.count(), 1)
        self.assertTrue(blocked_between(self.poster.pk, self.outsider.pk))
        self.assertTrue(blocked_between(self.outsider.pk, self.poster.pk))
        self.assertEqual(list(blocked_user_ids(self.poster)), [self.outsider.pk])
        self.assertEqual(list(blocked_user_ids(self.outsider)), [self.poster.pk])
        self.assertFalse(blocked_between(self.poster.pk, self.swiper.pk))

    def test_self_block_and_unauthenticated_mutations_are_rejected(self):
        post = self.public_post()
        self.rejected(lambda: block_user(self.poster, self.poster.pk), 400)
        self.rejected(lambda: block_post_owner(post.pk, self.poster), 400)
        self.rejected(lambda: block_user(AnonymousUser(), self.poster.pk), 403)
        self.rejected(lambda: report_post(post.pk, AnonymousUser()), 403)
        self.assertFalse(UserBlock.objects.exists())
        self.assertFalse(ActivityReport.objects.exists())

    def test_only_creator_can_unblock_and_reciprocal_block_remains_effective(self):
        outgoing = block_user(self.poster, self.outsider.pk)
        incoming = block_user(self.outsider, self.poster.pk)
        self.rejected(lambda: unblock_user(self.swiper, outgoing.pk), 403)
        self.assertTrue(UserBlock.objects.filter(pk=outgoing.pk).exists())
        self.assertTrue(unblock_user(self.poster, outgoing.pk))
        self.assertFalse(UserBlock.objects.filter(pk=outgoing.pk).exists())
        self.assertTrue(blocked_between(self.poster.pk, self.outsider.pk))
        self.assertTrue(unblock_user(self.outsider, incoming.pk))
        self.assertFalse(blocked_between(self.poster.pk, self.outsider.pk))

    def test_retired_creator_cannot_remove_a_protective_block(self):
        block = block_user(self.poster, self.outsider.pk)
        UserProfile.objects.create(user=self.poster, display_name="Retired", retired_at=self.now)
        self.rejected(lambda: unblock_user(self.poster, block.pk), 409)
        self.assertTrue(UserBlock.objects.filter(pk=block.pk).exists())

    def test_block_hides_new_cards_and_rejects_new_matches_in_both_directions(self):
        poster_card = self.public_post(self.poster, "Publisher's next card")
        outsider_card = self.public_post(self.outsider, "Blocked person's next card")
        block_user(self.poster, self.outsider.pk)
        before_matches, before_swipes = Match.objects.count(), Swipe.objects.count()
        for viewer, card in ((self.poster, outsider_card), (self.outsider, poster_card)):
            with self.subTest(viewer=viewer.username):
                discovered = discover_context_for_user(viewer, {})["posts"]
                self.assertNotIn(card.pk, [post.pk for post in discovered])
                result = handle_swipe(viewer, card.pk, "interested")
                self.assertIsNone(result.match_id)
        self.assertEqual(Match.objects.count(), before_matches)
        self.assertEqual(Swipe.objects.count(), before_swipes)

    def test_matching_disabled_does_not_consume_an_interested_swipe(self):
        post = self.public_post()
        with override_settings(PLUSONE_NEW_MATCHES_ENABLED=False):
            result = handle_swipe(self.outsider, post.pk, "interested")
        self.assertEqual(result.outcome, SwipeOutcome.TRY_AGAIN)
        self.assertFalse(Swipe.objects.filter(post=post, user=self.outsider).exists())
        self.assertFalse(Match.objects.filter(post=post).exists())
        post.refresh_from_db()
        self.assertEqual(post.status, ActivityPost.Status.ACTIVE)

    def test_finished_meetup_report_blocks_contact_without_fabricating_cancellation(self):
        self.agree()
        before_messages = self.match.messages.count()
        with patch("plusone.services.meetups.timezone.now", return_value=self.match.plan_expected_end_at + timedelta(seconds=1)):
            report = report_match(self.match.pk, self.poster, "unsafe_meeting", "Concern after our meeting.")
            self.match.refresh_from_db()
            payload = plan_payload(self.match, self.poster)
        self.assertEqual(report.status, SafetyReport.Status.PENDING)
        self.assertTrue(blocked_between(self.poster.pk, self.swiper.pk))
        self.assertEqual(self.match.status, Match.Status.AGREED)
        self.assertIsNone(self.match.meetup_cancelled_at)
        self.assertEqual(payload["meetup_status"], "finished")
        self.assertEqual(self.match.messages.count(), before_messages)
        self.assertFalse(ProductEvent.objects.filter(name=ProductEvent.Name.MEETUP_CANCELLED, match=self.match).exists())


class ActivityReportAdminTests(SafetyFixtures, TestCase):
    def test_admin_can_review_status_and_notes_without_rewriting_report_evidence(self):
        post = self.public_post()
        report = report_post(post.pk, self.outsider, "harassment", "Original reporter evidence.", block=False)
        model_admin = ActivityReportAdmin(ActivityReport, admin.site)
        request = RequestFactory().get("/admin/plusone/activityreport/")
        request.user = self.poster
        request.user.is_staff = request.user.is_superuser = True
        form_class = model_admin.get_form(request, report)
        self.assertEqual(set(form_class.base_fields), {"status", "handling_notes"})
        form = form_class({
            "status": "in_progress", "handling_notes": "Moderator review.",
            "post": self.post.pk, "reporter": self.poster.pk,
            "category": "other", "reason": "Overwritten evidence.",
            "title_snapshot": "Overwritten original card",
            "description_snapshot": "Overwritten original description",
        }, instance=report)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        report.refresh_from_db()
        self.assertEqual(report.post_id, post.pk)
        self.assertEqual(report.reporter_id, self.outsider.pk)
        self.assertEqual(report.category, "harassment")
        self.assertEqual(report.reason, "Original reporter evidence.")
        self.assertEqual(report.title_snapshot, post.title)
        self.assertEqual(report.description_snapshot, post.description)
        self.assertEqual(report.status, ActivityReport.Status.IN_PROGRESS)
        self.assertEqual(report.handling_notes, "Moderator review.")
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request, report))
