"""Exercise the product's HTTP boundaries using an isolated test database."""
import json
from datetime import timedelta, timezone as datetime_timezone
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone

from plusone.context_processors import session_scope
from plusone.models import ActivityPost, ActivityReport, BrowserBudgetBucket, CampusLocation, Match, PushSubscription, Swipe, UserBlock
from plusone.services.lifecycle import set_presence
from plusone.services.matching import handle_swipe
from plusone.services.safety import block_user
from plusone.services.browser_budget import consume_browser_limit


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules", PLUSONE_NEW_MATCHES_ENABLED=True)
class ProductExperienceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("anon_experience_guest")
        self.owner = get_user_model().objects.create_user("anon_experience_owner")
        self.location = CampusLocation.objects.first()
        self.start = timezone.localtime().replace(second=0, microsecond=0) + timedelta(hours=2)
        self.post = ActivityPost.objects.create(user=self.owner, title="A real participant's plan", description="Study together",
            activity_type="study", location=self.location, start_time=self.start, expected_end_time=self.start + timedelta(hours=1),
            expire_time=timezone.now() + timedelta(minutes=45))
        self.client.force_login(self.user)

    def manual(self, **changes):
        return {"action": "publish", "session_scope": session_scope(self.user), "request_id": str(uuid4()),
            "title": "My reviewed title", "description": "My reviewed description", "activity_type": "study",
            "location": str(self.location.pk), "start_time": self.start.strftime("%Y-%m-%dT%H:%M"),
            "expected_end_time": (self.start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"), "expire_minutes": "45", **changes}

    def agreed(self):
        return Match.objects.create(post=self.post, poster=self.owner, swiper=self.user, status="agreed",
            plan_meeting_at=self.post.start_time, plan_expected_end_at=self.post.expected_end_time,
            meeting_point="Library; east, entrance\nCheck the sign", plan_confirmed_at=timezone.now())

    def test_ai_proposal_preserves_current_manual_fields_and_requires_explicit_application(self):
        proposal = {"title": "Suggested title", "description": "Suggested description", "activity_type": "sports",
            "location_name": self.location.name, "start_time": self.start.isoformat(),
            "expected_end_time": (self.start + timedelta(hours=1)).isoformat(), "expire_minutes": 30,
            "validation": {"warnings": [], "missing_fields": []}, "field_sources": {"expected_end_time": "user_text"}}
        data = self.manual(action="assist", assist_text="A different suggested plan", reviewed_payload=json.dumps({"title": "An obsolete snapshot"}))
        with patch("plusone.views.parse_activity_text", return_value=proposal):
            response = self.client.post(reverse("create_post"), data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["post_form"].initial["title"], "My reviewed title")
        self.assertEqual(response.context["draft_proposal"]["fields"]["title"], "Suggested title")
        self.assertEqual(ActivityPost.objects.filter(user=self.user).count(), 0)
        applied = self.client.post(reverse("create_post"), self.manual(action="apply_draft", proposal_payload=json.dumps(response.context["draft_proposal"])), follow=True)
        self.assertEqual(applied.context["post_form"].initial["title"], "Suggested title")
        kept = self.client.post(reverse("create_post"), self.manual(action="keep_reviewed_draft", title="Edited after suggestions",
            reviewed_payload=json.dumps({"title": "Old value"})), follow=True)
        self.assertEqual(kept.context["post_form"].initial["title"], "Edited after suggestions")
        self.assertFalse(ActivityPost.objects.filter(user=self.user).exists())

    def test_new_publication_requires_reviewed_end_time(self):
        response = self.client.post(reverse("create_post"), self.manual(expected_end_time=""))
        self.assertIn("expected_end_time", response.context["post_form"].errors)
        self.assertFalse(ActivityPost.objects.filter(user=self.user).exists())

    def test_filtered_pass_and_undo_preserve_the_decision_queue(self):
        queue = f'/discover/?activity_type=study&location={self.location.pk}&time_window=today'
        passed = self.client.post(reverse("swipe_post", args=[self.post.pk]), {"action": "pass", "return_to": queue})
        self.assertRedirects(passed, queue, fetch_redirect_response=False)
        undone = self.client.post(reverse("undo_pass", args=[self.post.pk]), {"return_to": queue})
        self.assertRedirects(undone, queue, fetch_redirect_response=False)
        self.assertFalse(Swipe.objects.filter(user=self.user, post=self.post).exists())
        unsafe = self.client.post(reverse("swipe_post", args=[self.post.pk]), {"action": "pass", "return_to": "https://example.org/discover/?activity_type=study"})
        self.assertRedirects(unsafe, "/discover/", fetch_redirect_response=False)

    def test_report_before_matching_is_private_and_can_block_the_author(self):
        response = self.client.post(reverse("post_safety", args=[self.post.pk]), {"action": "report", "category": "unsafe_meeting",
            "reason": "Please review the proposed meeting", "block_user": "yes", "session_scope": session_scope(self.user)})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ActivityReport.objects.filter(post=self.post, reporter=self.user).exists())
        self.assertTrue(UserBlock.objects.filter(blocker=self.user, target=self.owner).exists())
        self.assertFalse(Match.objects.exists())
        self.assertFalse(Swipe.objects.exists())
        report = ActivityReport.objects.get()
        report.handling_notes = "Private moderation evidence"
        report.save()
        own = self.client.get(reverse("notifications"))
        self.assertContains(own, escape(self.post.title))
        self.assertNotContains(own, "Private moderation evidence")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("notifications")).context["reports"], [])

    def test_only_the_blocking_user_can_unblock(self):
        block = block_user(self.user, self.owner.pk)
        self.client.force_login(self.owner)
        self.client.post(reverse("notifications"), {"action": "unblock", "block_id": block.pk})
        self.assertTrue(UserBlock.objects.filter(pk=block.pk).exists())
        self.client.force_login(self.user)
        self.client.post(reverse("notifications"), {"action": "unblock", "block_id": block.pk})
        self.assertFalse(UserBlock.objects.filter(pk=block.pk).exists())

    def test_reset_is_two_step_and_stale_tabs_cannot_publish_as_the_new_identity(self):
        old_scope = session_scope(self.user)
        subscription = PushSubscription.objects.create(user=self.user, endpoint="https://fcm.googleapis.com/fcm/send/test",
            endpoint_digest="a" * 64, p256dh="test", auth="test")
        preview = self.client.post(reverse("reset_anonymous_identity"), {"session_scope": old_scope})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        reset = self.client.post(reverse("reset_anonymous_identity"), {"session_scope": old_scope, "confirm_reset": "yes"})
        self.assertEqual(reset.status_code, 302)
        self.assertNotEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        subscription.refresh_from_db()
        self.assertFalse(subscription.is_active)
        stale = self.client.post(reverse("create_post"), self.manual(session_scope=old_scope))
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(ActivityPost.objects.filter(title="My reviewed title").exists())

    def test_browser_ai_budget_survives_identity_reset(self):
        fixed = timezone.now().replace(second=5, microsecond=0)
        with patch("django.utils.timezone.now", return_value=fixed), patch("plusone.views.parse_activity_text", return_value={"title": "Suggested"}):
            for _ in range(5):
                self.assertEqual(self.client.post(reverse("create_post"), {"action": "assist", "assist_text": "study tomorrow at 19:00"}, follow=True).status_code, 200)
            self.client.post(reverse("reset_anonymous_identity"), {"session_scope": session_scope(self.user), "confirm_reset": "yes"})
            self.assertEqual(self.client.post(reverse("create_post"), {"action": "assist", "assist_text": "study tomorrow at 19:00"}, follow=True).status_code, 200)
            limited = self.client.post(reverse("create_post"), {"action": "assist", "assist_text": "study tomorrow at 19:00"})
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited)
        self.assertEqual(BrowserBudgetBucket.objects.get(scope="ai").count, 6)

    def test_failed_publish_retries_cannot_reset_user_budget_by_removing_usage_cookie(self):
        fixed = timezone.now().replace(second=5, microsecond=0)
        data = self.manual()
        with patch("django.utils.timezone.now", return_value=fixed), patch("plusone.views.moderate_activity_form", return_value={"service_unavailable": True}) as moderate:
            for _ in range(10):
                self.client.cookies.pop("plusone_usage", None)
                self.assertEqual(self.client.post(reverse("create_post"), data).status_code, 503)
            self.client.cookies.pop("plusone_usage", None)
            limited = self.client.post(reverse("create_post"), data)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(moderate.call_count, 10)
        self.assertFalse(ActivityPost.objects.filter(user=self.user).exists())

    def test_failed_message_retries_cannot_reset_user_budget_by_removing_usage_cookie(self):
        fixed = timezone.now().replace(second=5, microsecond=0)
        with patch("django.utils.timezone.now", return_value=fixed):
            match = Match.objects.get(pk=handle_swipe(self.user, self.post.pk, "interested").match_id)
            set_presence(match.pk, self.owner, True)
            set_presence(match.pk, self.user, True)
            data = {"message": "A repeated failed attempt", "request_id": str(uuid4())}
            with patch("plusone.services.chat.moderate_text", return_value={"service_unavailable": True}) as moderate:
                for _ in range(30):
                    self.client.cookies.pop("plusone_usage", None)
                    self.assertEqual(self.client.post(reverse("chat_messages", args=[match.pk]), data).status_code, 503)
                self.client.cookies.pop("plusone_usage", None)
                response = self.client.post(reverse("chat_messages", args=[match.pk]), data)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(moderate.call_count, 30)
            self.assertFalse(match.messages.filter(sender=self.user).exists())

    def test_accepted_message_replay_remains_free_after_user_budget_is_exhausted(self):
        from plusone.services.requests import consume_limit
        fixed = timezone.now().replace(second=5, microsecond=0)
        with patch("django.utils.timezone.now", return_value=fixed):
            match = Match.objects.get(pk=handle_swipe(self.user, self.post.pk, "interested").match_id)
            set_presence(match.pk, self.owner, True)
            set_presence(match.pk, self.user, True)
            data = {"message": "An accepted message", "request_id": str(uuid4())}
            response = self.client.post(reverse("chat_messages", args=[match.pk]), data)
            self.assertEqual(response.status_code, 200)
            for _ in range(29):
                consume_limit(self.user, "message")
            with patch("plusone.services.chat.moderate_text") as moderate:
                replay = self.client.post(reverse("chat_messages", args=[match.pk]), data)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["message"]["id"], response.json()["message"]["id"])
        moderate.assert_not_called()
        self.assertEqual(BrowserBudgetBucket.objects.get(scope="message").count, 1)

    def test_edit_uses_the_browser_budget_before_external_moderation(self):
        self.post.user = self.user
        self.post.save(update_fields=["user"])
        self.client.get(reverse("edit_post", args=[self.post.pk]))
        from django.test import RequestFactory
        request = RequestFactory().get("/", HTTP_COOKIE=self.client.cookies.output(header="", sep=";").strip())
        request.user = self.user
        fixed = timezone.now().replace(second=5, microsecond=0)
        with patch("django.utils.timezone.now", return_value=fixed):
            for _ in range(10):
                consume_browser_limit(request, "publish")
            with patch("plusone.views.moderate_activity_form") as moderate:
                response = self.client.post(reverse("edit_post", args=[self.post.pk]), self.manual(action="save"))
        self.assertEqual(response.status_code, 429)
        moderate.assert_not_called()

    def test_cancelled_and_ended_plans_are_excluded_from_active_inbox(self):
        match = self.agreed()
        updates = self.client.get(reverse("session_updates")).json()
        self.assertEqual(updates["session_scope"], session_scope(self.user))
        self.assertTrue(any(row["id"] == match.pk for row in updates["matches"]))
        self.assertEqual(updates["notifications"][0]["activity_title"], self.post.title)
        self.client.post(reverse("notifications"), {"action": "mark_all_read"})
        self.assertEqual(self.client.get(reverse("session_updates")).json()["unread_count"], 0)
        Match.objects.filter(pk=match.pk).update(meetup_cancelled_at=timezone.now())
        self.assertEqual(self.client.get(reverse("session_updates")).json()["matches"], [])

    def test_calendar_is_private_valid_utc_and_denies_blocked_or_cancelled_travel(self):
        match = self.agreed()
        response = self.client.get(reverse("meetup_calendar", args=[match.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BEGIN:VCALENDAR")
        self.assertContains(response, "Library\\; east\\, entrance\\nCheck the sign")
        self.assertIn("DTSTART:" + self.post.start_time.astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%SZ"), response.content.decode())
        outsider = get_user_model().objects.create_user("anon_calendar_outsider")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("meetup_calendar", args=[match.pk])).status_code, 403)
        self.client.force_login(self.user)
        block_user(self.user, self.owner.pk)
        self.assertEqual(self.client.get(reverse("meetup_calendar", args=[match.pk])).status_code, 409)

    def test_presence_http_keeps_another_visible_tab_and_ignores_late_sequence(self):
        match = Match.objects.get(pk=handle_swipe(self.user, self.post.pk, "interested").match_id)
        tab = str(uuid4())
        endpoint = reverse("chat_presence", args=[match.pk])
        first = self.client.post(endpoint, {"visible": "true", "tab_id": tab, "sequence": "1"})
        self.assertEqual(first.status_code, 200)
        self.client.post(endpoint, {"visible": "false", "tab_id": tab, "sequence": "3"})
        self.client.post(endpoint, {"visible": "true", "tab_id": tab, "sequence": "2"})
        current = set_presence(match.pk, self.owner, True)
        self.assertEqual(current.status, Match.Status.WAITING)

    def test_push_config_never_exposes_private_keys_and_json_posts_require_current_scope(self):
        with override_settings(PLUSONE_WEB_PUSH_ENABLED=False, PLUSONE_VAPID_PRIVATE_KEY="private-test-secret"):
            response = self.client.get(reverse("push_config"))
        self.assertFalse(response.json()["enabled"])
        self.assertNotContains(response, "private-test-secret")
        response = self.client.post(reverse("push_subscribe"), json.dumps({"subscription": {}}), content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_service_worker_has_root_scope_and_no_offline_post_cache(self):
        response = self.client.get(reverse("service_worker"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(response["Cache-Control"], "no-cache")
