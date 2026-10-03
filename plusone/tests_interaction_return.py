"""Return journeys use current shared plans and actionable, private tasks."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from plusone.models import ActivityPost, CampusLocation, Match, ProductEvent, SafetyReport, UserProfile
from plusone.selectors import dashboard_context_for_user
from plusone.services.meetups import plan_payload
from plusone.services.notifications import notification_rows
from plusone.services.safety import own_report_statuses


class InteractionReturnTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=datetime_timezone.utc)
        clock = patch("django.utils.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        users = get_user_model()
        self.poster = users.objects.create_user("return_poster")
        self.swiper = users.objects.create_user("return_swiper")
        self.outsider = users.objects.create_user("return_outsider")
        self.location = CampusLocation.objects.create(name="Return test library", area="Test", location_type="study")
        self.post = ActivityPost.objects.create(
            user=self.poster, title="Return to the agreed plan", activity_type="study", location=self.location,
            start_time=self.now + timedelta(minutes=20), expected_end_time=self.now + timedelta(minutes=80),
            expire_time=self.now + timedelta(hours=2), status=ActivityPost.Status.MATCHED,
        )
        self.match = Match.objects.create(
            post=self.post, poster=self.poster, swiper=self.swiper, status=Match.Status.AGREED,
            meeting_point="Library public entrance", plan_meeting_at=self.post.start_time,
            plan_expected_end_at=self.post.expected_end_time, plan_confirmed_at=self.now,
            poster_agreed=True, swiper_agreed=True, poster_agreed_revision=1, swiper_agreed_revision=1,
        )

    def change_match(self, **changes):
        Match.objects.filter(pk=self.match.pk).update(**changes)
        self.match.refresh_from_db()

    def feedback_rows(self, user=None, **options):
        return [row for row in notification_rows(user or self.poster, **options) if row["task_type"] == "feedback"]

    def event(self, name, **properties):
        return ProductEvent.objects.create(name=name, user=self.swiper, match=self.match, properties=properties)

    def test_open_chat_returns_the_shared_plan_instead_of_original_card_details(self):
        changed_time = self.now + timedelta(minutes=45)
        changed_end = self.now + timedelta(minutes=95)
        self.change_match(status=Match.Status.CHATTING, meeting_point="Library west entrance",
                          plan_meeting_at=changed_time, plan_expected_end_at=changed_end,
                          chat_expires_at=self.now + timedelta(minutes=5))
        context = dashboard_context_for_user(self.poster)
        row = context["open_matches"][0]
        self.assertEqual(row.dashboard_plan_point, "Library west entrance")
        self.assertEqual(row.dashboard_meeting_at, changed_time)
        self.assertEqual(row.dashboard_expected_end_at, changed_end)
        self.assertNotEqual(row.dashboard_meeting_at, row.post.start_time)
        self.assertEqual(context["feedback_matches"], [])
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.CHATTING)

    def test_feedback_appears_only_when_an_outcome_is_allowed(self):
        self.assertEqual(dashboard_context_for_user(self.poster)["feedback_matches"], [])
        self.assertEqual(self.feedback_rows(), [])
        with patch("django.utils.timezone.now", return_value=self.match.plan_meeting_at):
            context = dashboard_context_for_user(self.poster)
            row = context["feedback_matches"][0]
            self.assertTrue(row.feedback_can_met)
            self.assertFalse(row.feedback_can_not_met)
            self.assertTrue(row.feedback_pending)
            notice = self.feedback_rows()[0]
            self.assertEqual((notice["can_met"], notice["can_not_met"]), (True, False))
            self.assertEqual(context["dashboard_state"]["eyebrow"], "Feedback needed")

    def test_finished_meetup_has_a_return_task_and_is_not_all_clear(self):
        with patch("django.utils.timezone.now", return_value=self.match.plan_expected_end_at):
            context = dashboard_context_for_user(self.poster)
            self.assertEqual(context["handoff_count"], 0)
            self.assertEqual(context["feedback_count"], 1)
            self.assertEqual(context["feedback_matches"][0].feedback_deadline,
                             self.match.plan_expected_end_at + timedelta(hours=24))
            self.assertEqual(context["dashboard_state"]["eyebrow"], "Feedback needed")
            self.assertEqual(context["dashboard_state"]["deadline"], context["feedback_matches"][0].feedback_deadline)
            self.assertTrue(self.feedback_rows()[0]["can_not_met"])

    def test_future_cancelled_meetup_allows_negative_feedback_without_inviting_travel(self):
        self.change_match(meetup_cancelled_at=self.now)
        context = dashboard_context_for_user(self.poster)
        row = context["feedback_matches"][0]
        self.assertFalse(row.feedback_can_met)
        self.assertTrue(row.feedback_can_not_met)
        notice = self.feedback_rows()[0]
        self.assertIn("Do not travel", notice["body"])
        self.assertFalse(any(item["id"].startswith(("plan:", "reminder:")) for item in notification_rows(self.poster)))

    def test_feedback_closes_at_the_actual_snapshot_end_plus_twenty_four_hours(self):
        deadline = self.match.plan_expected_end_at + timedelta(hours=24)
        ActivityPost.objects.filter(pk=self.post.pk).update(expected_end_time=deadline + timedelta(days=1))
        with patch("django.utils.timezone.now", return_value=deadline):
            self.assertEqual(dashboard_context_for_user(self.poster)["feedback_count"], 1)
            self.assertEqual(len(self.feedback_rows()), 1)
        with patch("django.utils.timezone.now", return_value=deadline + timedelta(microseconds=1)):
            context = dashboard_context_for_user(self.poster)
            self.assertEqual(context["feedback_count"], 0)
            self.assertEqual(context["dashboard_state"]["eyebrow"], "All clear")
            self.assertEqual(self.feedback_rows(), [])

    def test_completed_feedback_is_individual_and_removes_only_that_persons_task(self):
        self.change_match(plan_meeting_at=self.now - timedelta(hours=2), plan_expected_end_at=self.now - timedelta(hours=1),
                          poster_meetup_outcome="met")
        self.assertEqual(dashboard_context_for_user(self.poster)["feedback_count"], 0)
        self.assertEqual(self.feedback_rows(), [])
        self.assertEqual(dashboard_context_for_user(self.swiper)["feedback_count"], 1)
        self.assertEqual(len(self.feedback_rows(self.swiper)), 1)

    def test_legacy_confirmation_evidence_also_completes_the_return_task(self):
        self.change_match(plan_meeting_at=self.now - timedelta(hours=2), plan_expected_end_at=self.now - timedelta(hours=1))
        ProductEvent.objects.create(name=ProductEvent.Name.MEETUP_CONFIRMED, user=self.poster, match=self.match)
        self.assertEqual(self.match.poster_meetup_outcome, "")
        self.assertEqual(dashboard_context_for_user(self.poster)["feedback_count"], 0)
        self.assertEqual(self.feedback_rows(), [])
        self.assertEqual(len(self.feedback_rows(self.swiper)), 1)

    def test_legacy_unrecorded_end_is_not_fabricated_in_the_visible_plan(self):
        self.change_match(plan_meeting_at=self.now - timedelta(hours=2), plan_expected_end_at=None, plan_legacy=True)
        ActivityPost.objects.filter(pk=self.post.pk).update(expected_end_time=None)
        row = dashboard_context_for_user(self.poster)["feedback_matches"][0]
        self.assertIsNone(row.dashboard_expected_end_at)
        self.assertEqual(row.feedback_deadline, self.match.plan_meeting_at + timedelta(hours=25))

    def test_retired_viewer_is_not_asked_to_act_but_the_other_person_can_report(self):
        self.change_match(meetup_cancelled_at=self.now)
        UserProfile.objects.create(user=self.poster, display_name="Retired return guest", retired_at=self.now)
        self.assertEqual(dashboard_context_for_user(self.poster)["feedback_count"], 0)
        self.assertEqual(self.feedback_rows(), [])
        self.assertEqual(dashboard_context_for_user(self.swiper)["feedback_count"], 1)
        self.assertEqual(len(self.feedback_rows(self.swiper)), 1)

    def test_reading_feedback_does_not_complete_it(self):
        self.change_match(meetup_cancelled_at=self.now)
        notice = self.feedback_rows()[0]
        rows = self.feedback_rows(read_ids=[notice["id"]], seen_before=self.now.isoformat())
        self.assertTrue(rows[0]["is_read"])
        self.assertTrue(rows[0]["attention_required"])
        self.assertEqual(rows[0]["kind"], "task")
        self.assertEqual(rows[0]["expires_at"], (self.match.plan_expected_end_at + timedelta(hours=24)).isoformat())
        self.assertEqual(dashboard_context_for_user(self.poster)["feedback_count"], 1)

    def test_plan_reminder_and_feedback_replace_each_other_instead_of_accumulating(self):
        self.change_match(plan_meeting_at=self.now + timedelta(hours=2), plan_expected_end_at=self.now + timedelta(hours=3))
        self.assertEqual([row["id"].split(":")[0] for row in notification_rows(self.poster)], ["plan"])
        with patch("django.utils.timezone.now", return_value=self.match.plan_meeting_at - timedelta(minutes=10)):
            rows = notification_rows(self.poster)
            self.assertEqual([row["id"].split(":")[0] for row in rows], ["reminder"])
            self.assertEqual(rows[0]["kind"], "task")
        with patch("django.utils.timezone.now", return_value=self.match.plan_expected_end_at):
            self.assertEqual([row["id"].split(":")[0] for row in notification_rows(self.poster)], ["feedback"])

    def test_elapsed_wait_and_chat_do_not_leave_actionable_tasks(self):
        for status, deadline_field in ((Match.Status.WAITING, "waiting_expires_at"), (Match.Status.CHATTING, "chat_expires_at")):
            with self.subTest(status=status):
                self.change_match(status=status, **{deadline_field: self.now})
                self.assertEqual(notification_rows(self.poster), [])
                self.match.refresh_from_db()
                self.assertEqual(self.match.status, status)

    def test_wait_notifications_end_with_the_activity_window_including_legacy_waits(self):
        for waiting_deadline, legacy_end in ((self.now + timedelta(minutes=10), False), (None, False), (None, True)):
            with self.subTest(waiting_deadline=waiting_deadline, legacy_end=legacy_end):
                activity_end = self.now + timedelta(minutes=2)
                ActivityPost.objects.filter(pk=self.post.pk).update(
                    start_time=activity_end - timedelta(hours=1),
                    expected_end_time=None if legacy_end else activity_end,
                )
                self.change_match(status=Match.Status.WAITING, waiting_expires_at=waiting_deadline)
                rows = notification_rows(self.poster)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["expires_at"], activity_end.isoformat())
                with patch("django.utils.timezone.now", return_value=activity_end):
                    self.assertEqual(notification_rows(self.poster), [])

    def test_dashboard_live_deadline_ends_with_the_activity_before_recruiting_expiry(self):
        self.change_match(status=Match.Status.DECLINED)
        activity_end = self.now + timedelta(minutes=30)
        ActivityPost.objects.filter(pk=self.post.pk).update(status=ActivityPost.Status.ACTIVE, expected_end_time=activity_end)
        context = dashboard_context_for_user(self.poster)
        self.assertEqual(context["dashboard_state"]["eyebrow"], "Live now")
        self.assertEqual(context["dashboard_state"]["deadline"], activity_end)
        with patch("django.utils.timezone.now", return_value=activity_end):
            self.assertEqual(dashboard_context_for_user(self.poster)["active_posts_count"], 0)

    @override_settings(TIME_ZONE="America/New_York")
    def test_current_task_deadlines_use_campus_local_time_and_specific_action_labels(self):
        self.change_match(status=Match.Status.WAITING, waiting_expires_at=self.now + timedelta(minutes=10))
        row = notification_rows(self.poster)[0]
        self.assertEqual((row["deadline_label"], row["deadline_display"]), ("Join by", "Oct 03, 08:10"))
        self.change_match(status=Match.Status.CHATTING, chat_expires_at=self.now + timedelta(minutes=5))
        row = notification_rows(self.poster)[0]
        self.assertEqual((row["deadline_label"], row["deadline_display"]), ("Decide by", "Oct 03, 08:05"))
        self.change_match(status=Match.Status.AGREED)
        row = notification_rows(self.poster)[0]
        self.assertEqual((row["deadline_label"], row["deadline_display"]), ("Meeting time", "Oct 03, 08:20"))
        self.change_match(meetup_cancelled_at=self.now)
        row = self.feedback_rows()[0]
        self.assertEqual((row["deadline_label"], row["deadline_display"]), ("Feedback closes", "Oct 04, 09:20"))

    def test_events_are_coalesced_and_ended_arrival_or_plan_edits_are_history(self):
        first_arrival = self.event(ProductEvent.Name.MEETUP_STATUS_UPDATED, status="delayed")
        latest_arrival = self.event(ProductEvent.Name.MEETUP_STATUS_UPDATED, status="arrived")
        plan_update = self.event(ProductEvent.Name.PLAN_UPDATED, plan_revision=1)
        with patch("django.utils.timezone.now", return_value=self.match.plan_expected_end_at):
            rows = notification_rows(self.poster)
        events = {row["id"]: row for row in rows if row["id"].startswith("event:")}
        self.assertNotIn(f"event:{first_arrival.pk}", events)
        self.assertIn(f"event:{latest_arrival.pk}", events)
        self.assertIn(f"event:{plan_update.pk}", events)
        self.assertTrue(all(not row["is_current"] and not row["attention_required"] for row in events.values()))
        self.assertEqual(len([row for row in rows if row["task_type"] == "feedback"]), 1)

    def test_pending_tasks_preserve_private_safety_report_status(self):
        self.change_match(meetup_cancelled_at=self.now)
        report = SafetyReport.objects.create(match=self.match, reporter=self.poster, reason="Review the public meeting",
                                            status=SafetyReport.Status.IN_PROGRESS, handling_notes="Private operator notes")
        self.assertEqual(len(self.feedback_rows()), 1)
        own = own_report_statuses(self.poster)
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["status"], report.get_status_display())
        self.assertNotIn("Private operator notes", str(own))
        self.assertEqual(own_report_statuses(self.swiper), [])
        self.assertEqual(notification_rows(self.outsider), [])
        self.assertEqual(dashboard_context_for_user(self.outsider)["feedback_matches"], [])

    def test_feedback_notice_flags_match_the_action_permissions(self):
        scenarios = (
            (self.match.plan_meeting_at, False),
            (self.match.plan_expected_end_at, False),
            (self.now, True),
        )
        for instant, cancelled in scenarios:
            with self.subTest(instant=instant, cancelled=cancelled):
                self.change_match(meetup_cancelled_at=self.now if cancelled else None)
                with patch("django.utils.timezone.now", return_value=instant):
                    plan = plan_payload(self.match, self.poster)
                    notice = self.feedback_rows()[0]
                self.assertEqual(notice["can_met"], plan["can_met"])
                self.assertEqual(notice["can_not_met"], plan["can_not_met"])
