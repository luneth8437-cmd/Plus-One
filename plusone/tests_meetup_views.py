from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from plusone.models import ActivityPost, CampusLocation, Match
from plusone.services.lifecycle import set_presence
from plusone.services.matching import handle_swipe


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules")
class MeetupEndpointTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.poster = User.objects.create_user("plan-view-poster")
        self.guest = User.objects.create_user("plan-view-guest")
        self.outsider = User.objects.create_user("plan-view-outsider")
        location, _ = CampusLocation.objects.get_or_create(
            name="Meetup view library",
            defaults={"location_type": "study", "area": "Campus"},
        )
        start = (timezone.now() + timedelta(minutes=15)).replace(second=0, microsecond=0)
        self.post = ActivityPost.objects.create(
            user=self.poster, title="A real library plan", activity_type="study",
            location=location, start_time=start, expected_end_time=start + timedelta(hours=1),
            expire_time=timezone.now() + timedelta(minutes=45),
        )
        result = handle_swipe(self.guest, self.post.pk, "interested")
        self.match = Match.objects.get(pk=result.match_id)
        set_presence(self.match.pk, self.poster, True)
        set_presence(self.match.pk, self.guest, True)
        self.url = reverse("chat_plan", args=[self.match.pk])
        self.poll_url = reverse("chat_messages", args=[self.match.pk])
        self.client.force_login(self.poster)

    def state(self, client=None):
        return (client or self.client).get(self.poll_url).json()

    def action(self, action, *, client=None, **fields):
        body = {
            "action": action,
            "request_id": str(uuid4()),
            "revision": self.state(client)["plan"]["revision"],
            **fields,
        }
        return (client or self.client).post(self.url, body, HTTP_ACCEPT="application/json")

    def update(self, **overrides):
        plan = self.state()["plan"]
        fields = {
            "meeting_point": "Library main entrance",
            "meeting_at": plan["meeting_at_input"],
            "expected_end_at": plan["expected_end_at_input"],
            **overrides,
        }
        return self.action("update_plan", **fields)

    def confirm_both(self):
        self.assertEqual(self.update().status_code, 200)
        self.assertEqual(self.action("confirm_plan").status_code, 200)
        guest = Client()
        guest.force_login(self.guest)
        self.assertEqual(self.action("confirm_plan", client=guest).status_code, 200)
        return guest

    def test_endpoint_requires_participant_and_csrf(self):
        self.client.force_login(self.outsider)
        response = self.client.post(self.url, {"action": "arrived"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.poster)
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.poster)
        response = secured.post(self.url, {"action": "confirm_plan"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_shared_plan_edit_returns_synced_state_without_extending_clock(self):
        self.match.refresh_from_db()
        deadline = self.match.chat_expires_at
        before = self.state()["plan"]["revision"]
        response = self.update()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["plan"]["meeting_point"], "Library main entrance")
        self.assertGreater(data["plan"]["revision"], before)
        guest = Client()
        guest.force_login(self.guest)
        self.assertEqual(self.state(guest)["plan"]["meeting_point"], "Library main entrance")
        self.match.refresh_from_db()
        self.assertEqual(self.match.chat_expires_at, deadline)

    def test_stale_confirmation_returns_current_plan_and_cannot_agree(self):
        old_revision = self.state()["plan"]["revision"]
        self.assertEqual(self.update().status_code, 200)
        response = self.action("confirm_plan", revision=old_revision)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["plan"]["meeting_point"], "Library main entrance")
        self.assertFalse(response.json()["plan"]["viewer_confirmed"])

    def test_same_request_replays_but_changed_body_conflicts(self):
        plan = self.state()["plan"]
        body = {
            "action": "update_plan", "request_id": str(uuid4()), "revision": plan["revision"],
            "meeting_point": "Library main entrance", "meeting_at": plan["meeting_at_input"],
            "expected_end_at": plan["expected_end_at_input"],
        }
        first = self.client.post(self.url, body, HTTP_ACCEPT="application/json")
        self.assertEqual(first.status_code, 200)
        retry = self.client.post(self.url, body, HTTP_ACCEPT="application/json")
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json()["replayed"])
        self.assertEqual(first.json()["plan"]["revision"], retry.json()["plan"]["revision"])
        body["meeting_point"] = "A different entrance"
        self.assertEqual(self.client.post(self.url, body, HTTP_ACCEPT="application/json").status_code, 409)

    def test_arrival_cancel_and_explicit_publisher_recruiting(self):
        guest = self.confirm_both()
        arrived = self.action("arrived", client=guest)
        self.assertEqual(arrived.status_code, 200)
        self.assertEqual(self.state()["plan"]["other_status"], "arrived")
        cancelled = self.action("cancel_meetup", client=guest)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.state()["plan"]["meetup_status"], "cancelled")
        self.assertFalse(ActivityPost.objects.active().filter(pk=self.post.pk).exists())
        self.assertEqual(self.action("arrived").status_code, 409)
        self.assertEqual(self.action("reopen_card", client=guest).status_code, 403)
        self.assertEqual(self.action("reopen_card").status_code, 200)
        self.assertTrue(ActivityPost.objects.active().filter(pk=self.post.pk).exists())

    def test_html_fallback_redirects_to_plan_and_shows_saved_point(self):
        plan = self.state()["plan"]
        response = self.client.post(self.url, {
            "action": "update_plan", "request_id": str(uuid4()), "revision": plan["revision"],
            "meeting_point": "Library west entrance", "meeting_at": plan["meeting_at_input"],
            "expected_end_at": plan["expected_end_at_input"],
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Library west entrance")
        self.assertIn("meetup_payload", response.context)

    def test_unsafe_point_is_not_saved(self):
        plan = self.state()["plan"]
        response = self.action(
            "update_plan", meeting_point="Call me at 13800138000",
            meeting_at=plan["meeting_at_input"], expected_end_at=plan["expected_end_at_input"],
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state()["plan"]["meeting_point"], plan["meeting_point"])

    def test_dashboard_uses_agreed_point_and_labels_cancelled_meetup(self):
        guest = self.confirm_both()
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["handoff_count"], 1)
        self.assertContains(dashboard, "Library main entrance")
        self.assertEqual(self.action("cancel_meetup", client=guest).status_code, 200)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["handoff_count"], 0)
        self.assertContains(dashboard, "Meetup cancelled")
        self.assertContains(dashboard, reverse("chat", args=[self.match.pk]))

    def test_paused_original_card_explains_recruiting_and_links_to_meetup(self):
        guest = self.confirm_both()
        self.assertEqual(self.action("cancel_meetup", client=guest).status_code, 200)
        response = self.client.get(reverse("post_detail", args=[self.post.pk]))
        self.assertContains(response, "Recruiting is paused")
        self.assertNotContains(response, "This card has expired.")
        self.assertContains(response, reverse("chat", args=[self.match.pk]))

    def test_finished_meetup_is_history_and_has_clear_server_rendered_state(self):
        self.confirm_both()
        self.match.refresh_from_db()
        self.match.plan_meeting_at = timezone.now() - timedelta(minutes=31)
        self.match.plan_expected_end_at = timezone.now() - timedelta(minutes=1)
        self.match.save(update_fields=["plan_meeting_at", "plan_expected_end_at"])
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["handoff_count"], 0)
        self.assertContains(dashboard, "Meeting window ended")
        self.assertContains(dashboard, "View plan and feedback")
        chat = self.client.get(reverse("chat", args=[self.match.pk]))
        self.assertContains(chat, "Meeting window ended.")
        self.assertTrue(chat.context["plan"]["can_outcome"])
        self.assertFalse(chat.context["plan"]["can_arrive"])

    def test_stale_exit_does_not_claim_confirmed_meetup_is_closed(self):
        self.confirm_both()
        response = self.client.post(reverse("chat", args=[self.match.pk]), {"action": "decline"}, follow=True)
        self.assertContains(response, "Both people already confirmed a meetup.")
        self.assertNotContains(response, "This match is closed.")
        self.match.refresh_from_db()
        self.assertIsNone(self.match.meetup_cancelled_at)

    def test_card_cancel_racing_with_a_match_keeps_the_new_match_intact(self):
        # The edit view reads ACTIVE before a competing interested request
        # reserves the card. Its locked mutation must recheck that state.
        self.post.status = ActivityPost.Status.MATCHED
        self.post.save(update_fields=["status"])
        stale = ActivityPost.objects.get(pk=self.post.pk)
        stale.status = ActivityPost.Status.ACTIVE
        with patch("plusone.views.get_object_or_404", return_value=stale):
            response = self.client.post(reverse("edit_post", args=[self.post.pk]), {"action": "cancel"})
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.post.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.MATCHED)
        self.assertEqual(self.match.status, Match.Status.CHATTING)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "This card changed while you were editing.")
        self.assertNotContains(dashboard, "Your Plus One card was cancelled.")
