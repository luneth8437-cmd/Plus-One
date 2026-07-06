import json
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .ai import moderate_text, parse_activity_text, suggest_ambiguous_time_options
from .models import ActivityPost, CampusLocation, ChatMessage, LLMLog, Match, Swipe, UserProfile
from .presenters import post_initial_from_ai
from .services.expiration import refresh_expired_records


class PlusOneTestCase(TestCase):
    def setUp(self):
        self.llm_env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
        self.llm_env.start()
        self.addCleanup(self.llm_env.stop)

        User = get_user_model()
        self.poster = User.objects.create_user(username="poster", password="pass")
        self.swiper = User.objects.create_user(username="swiper", password="pass")
        self.location, _ = CampusLocation.objects.update_or_create(
            name="Campus Sports Hall",
            defaults={
                "location_type": CampusLocation.LocationType.SPORTS,
                "area": "Central Campus",
            },
        )
        CampusLocation.objects.update_or_create(
            name="Main Library",
            defaults={
                "location_type": CampusLocation.LocationType.STUDY,
                "area": "Library Quad",
            },
        )
        self.post = ActivityPost.objects.create(
            user=self.poster,
            title="Basketball game tonight",
            description="Join for a campus game.",
            activity_type=ActivityPost.ActivityType.SPORTS,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=1),
            expire_time=timezone.now() + timedelta(minutes=45),
        )

    def test_test_client_host_is_allowed(self):
        self.assertIn("testserver", settings.ALLOWED_HOSTS)

    def test_create_activity_post_saves_fields(self):
        self.client.force_login(self.poster)
        response = self.client.post(
            reverse("create_post"),
            {
                "action": "publish",
                "title": "Study sprint",
                "description": "Focused session.",
                "activity_type": ActivityPost.ActivityType.STUDY,
                "location": self.location.id,
                "start_time": (timezone.localtime() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "expire_minutes": "30",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ActivityPost.objects.filter(title="Study sprint", user=self.poster).exists())

    def test_create_post_invalid_location_rerenders_preview(self):
        self.client.force_login(self.poster)
        response = self.client.post(
            reverse("create_post"),
            {
                "action": "publish",
                "title": "Study sprint",
                "description": "Focused session.",
                "activity_type": ActivityPost.ActivityType.STUDY,
                "location": "not-a-location-id",
                "start_time": (timezone.localtime() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "expire_minutes": "30",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Campus location")
        self.assertFalse(ActivityPost.objects.filter(title="Study sprint", user=self.poster).exists())

    def test_create_preview_formats_empty_location_and_datetime(self):
        self.client.force_login(self.poster)
        start_time = (timezone.localtime() + timedelta(hours=2)).replace(second=0, microsecond=0)
        start_value = start_time.strftime("%Y-%m-%dT%H:%M")
        expected_preview_time = f"{start_time:%b} {start_time.day}, {start_time:%H:%M}"

        response = self.client.post(
            reverse("create_post"),
            {
                "action": "publish",
                "title": "Preview card",
                "description": "Check preview formatting.",
                "activity_type": ActivityPost.ActivityType.STUDY,
                "location": "",
                "start_time": start_value,
                "expire_minutes": "30",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Campus location")
        self.assertContains(response, expected_preview_time)
        self.assertNotContains(response, ">---------<")
        self.assertNotContains(response, f">{start_value}<")

    def test_create_preview_uses_activity_placeholder_before_selection(self):
        self.client.force_login(self.poster)
        response = self.client.get(reverse("create_post"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-post-preview-activity>Activity</span>')
        self.assertContains(response, 'data-post-preview-time>Start time</span>')
        self.assertNotContains(response, "People needed")
        self.assertNotContains(response, "data-post-preview-capacity")
        self.assertNotContains(response, 'id="id_start_time" value=')
        self.assertNotContains(response, 'data-post-preview-activity>Other</span>')

    def test_unsafe_post_is_flagged_and_not_published(self):
        self.client.force_login(self.poster)
        response = self.client.post(
            reverse("create_post"),
            {
                "action": "publish",
                "title": "Bring a weapon to the game",
                "description": "unsafe product text",
                "activity_type": ActivityPost.ActivityType.SPORTS,
                "location": self.location.id,
                "start_time": (timezone.localtime() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "expire_minutes": "30",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ActivityPost.objects.filter(title="Bring a weapon to the game").exists())
        self.assertTrue(LLMLog.objects.filter(task_type=LLMLog.TaskType.MODERATION, output_json__flagged=True).exists())

    def test_unsafe_assist_text_is_flagged_before_structuring(self):
        self.client.force_login(self.poster)
        response = self.client.post(
            reverse("create_post"),
            {
                "action": "assist",
                "raw_text": "Bring a weapon to the game",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Safety check flagged this request")
        self.assertNotContains(response, "Draft ready. Review the details before publishing.")
        self.assertTrue(LLMLog.objects.filter(task_type=LLMLog.TaskType.MODERATION, output_json__flagged=True).exists())

    def test_assist_output_does_not_show_internal_llm_log_payload(self):
        self.client.force_login(self.poster)
        response = self.client.post(
            reverse("create_post"),
            {
                "action": "assist",
                "raw_text": "Tonight I wanna play basketball",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft ready. Review the details before publishing.")
        self.assertContains(response, "Review and publish")
        self.assertContains(response, "Tell Plus One your plan")
        self.assertContains(response, "Draft my card")
        self.assertNotContains(response, "LLM-assisted post creation")
        self.assertNotContains(response, "Ask AI to structure it")
        self.assertNotContains(response, "AI / fallback output saved to LLMLog")
        self.assertNotContains(response, "location_name")

    def test_assist_shows_time_confirmation_for_ambiguous_hour(self):
        self.client.force_login(self.poster)
        response = self.client.post(
            reverse("create_post"),
            {
                "action": "assist",
                "raw_text": "Tomorrow I wanna play basketball at sports center at 7",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Time needs confirmation")
        self.assertContains(response, "Morning")
        self.assertContains(response, "Evening")
        self.assertContains(response, "data-time-option")

    def test_discover_presents_decision_queue_actions(self):
        self.client.force_login(self.swiper)
        response = self.client.get(reverse("discover"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Decision queue")
        self.assertContains(response, "Pick one plan. Decide fast.")
        self.assertContains(response, 'aria-label="Pass"')
        self.assertContains(response, 'aria-label="Interested"')
        self.assertContains(response, "expires in")

    def test_discover_empty_state_gives_next_actions(self):
        self.post.status = ActivityPost.Status.CANCELLED
        self.post.save(update_fields=["status"])
        self.client.force_login(self.swiper)

        response = self.client.get(reverse("discover"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No cards are ready for this queue.")
        self.assertContains(response, "Start a Plus One")
        self.assertContains(response, "See latest cards")

    def test_filtered_empty_state_can_clear_filters(self):
        self.client.force_login(self.swiper)

        response = self.client.get(f"{reverse('discover')}?activity_type=study")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your filters may be hiding live plans.")
        self.assertContains(response, "Clear filters")

    def test_rule_guardrail_flags_when_llm_misses_unsafe_text(self):
        raw_response = json.dumps(
            {
                "flagged": False,
                "categories": [],
                "reason": "Model did not flag this.",
            }
        )

        class Message:
            content = raw_response

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        with (
            patch("plusone.ai._llm_client", return_value=(object(), {"model": "deepseek-v4-flash", "strategy": "deepseek"})),
            patch("plusone.ai._chat_completion", return_value=Response()),
        ):
            result = moderate_text(self.poster, "Bring a weapon to the game")

        self.assertTrue(result["flagged"])
        self.assertIn("weapon", result["categories"])
        self.assertTrue(LLMLog.objects.filter(task_type=LLMLog.TaskType.MODERATION, strategy="deepseek", output_json__flagged=True).exists())

    def test_owner_can_edit_active_post(self):
        self.client.force_login(self.poster)
        response = self.client.post(
            reverse("edit_post", args=[self.post.id]),
            {
                "action": "save",
                "title": "Updated basketball run",
                "description": "Updated safe description.",
                "activity_type": ActivityPost.ActivityType.SPORTS,
                "location": self.location.id,
                "start_time": (timezone.localtime() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
                "expire_minutes": "40",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.title, "Updated basketball run")

    def test_non_owner_cannot_edit_post(self):
        self.client.force_login(self.swiper)
        response = self.client.get(reverse("edit_post", args=[self.post.id]))

        self.assertEqual(response.status_code, 403)

    def test_owner_can_cancel_active_post(self):
        self.client.force_login(self.poster)
        response = self.client.post(reverse("edit_post", args=[self.post.id]), {"action": "cancel"})

        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.CANCELLED)

    def test_create_page_auto_starts_anonymous_session(self):
        response = self.client.get(reverse("create_post"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], reverse("create_post"))
        self.assertContains(response, "Create a temporary Plus One card.")
        self.assertTrue(get_user_model().objects.filter(username__startswith="anon_").exists())

    def test_anonymous_session_reuses_same_identity(self):
        User = get_user_model()

        self.client.get(reverse("home"))
        first_user_id = self.client.session["_auth_user_id"]
        self.client.get(reverse("discover"))
        second_user_id = self.client.session["_auth_user_id"]

        self.assertEqual(first_user_id, second_user_id)
        self.assertEqual(User.objects.filter(username__startswith="anon_").count(), 1)

    def test_home_redirects_to_discover_with_anonymous_session(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("discover"))
        self.assertTrue(get_user_model().objects.filter(username__startswith="anon_").exists())

    def test_reset_anonymous_identity_creates_fresh_session_user(self):
        User = get_user_model()

        self.client.get(reverse("home"))
        first_user_id = self.client.session["_auth_user_id"]
        response = self.client.post(reverse("reset_anonymous_identity"))
        second_user_id = self.client.session["_auth_user_id"]

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(first_user_id, second_user_id)
        self.assertEqual(User.objects.filter(username__startswith="anon_").count(), 2)

    def test_reset_anonymous_identity_closes_previous_live_state(self):
        User = get_user_model()

        self.client.get(reverse("home"))
        old_user = User.objects.get(id=self.client.session["_auth_user_id"])
        old_post = ActivityPost.objects.create(
            user=old_user,
            title="Old anonymous lunch",
            description="Will be closed when identity resets.",
            activity_type=ActivityPost.ActivityType.FOOD,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=1),
            expire_time=timezone.now() + timedelta(minutes=45),
        )
        match = Match.objects.create(
            post=old_post,
            poster=old_user,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        response = self.client.post(reverse("reset_anonymous_identity"))

        old_post.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(old_post.status, ActivityPost.Status.CANCELLED)
        self.assertEqual(match.status, Match.Status.EXPIRED)
        self.assertNotEqual(int(self.client.session["_auth_user_id"]), old_user.id)

    def test_start_anonymous_session_rejects_external_next(self):
        response = self.client.get(f"{reverse('login')}?next=https://evil.example/path")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("discover"))

    def test_profile_setup_rejects_external_next(self):
        response = self.client.post(f"{reverse('profile_setup')}?next=https://evil.example/path")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("discover"))

    def test_profile_setup_enters_discover_without_collecting_profile_fields(self):
        response = self.client.post(reverse("profile_setup"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("discover"))
        profile = UserProfile.objects.get(user_id=self.client.session["_auth_user_id"])
        self.assertTrue(profile.display_name.startswith("Campus Guest "))

    def test_session_page_explains_anonymous_visibility_without_profile_fields(self):
        response = self.client.get(reverse("session"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're anonymous here.")
        self.assertContains(response, "What others can see")
        self.assertContains(response, "What stays hidden")
        self.assertContains(response, "data-confirm-reset")
        self.assertNotContains(response, "Temporary name")
        self.assertTrue(get_user_model().objects.filter(username__startswith="anon_").exists())

    def test_anonymous_users_can_create_match_and_chat(self):
        User = get_user_model()
        creator = Client()
        swiper = Client()

        creator.get(reverse("home"))
        creator_user = User.objects.get(id=creator.session["_auth_user_id"])
        create_response = creator.post(
            reverse("create_post"),
            {
                "action": "publish",
                "title": "Anonymous coffee",
                "description": "Quick coffee before class.",
                "activity_type": ActivityPost.ActivityType.FOOD,
                "location": self.location.id,
                "start_time": (timezone.localtime() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "expire_minutes": "30",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        post = ActivityPost.objects.get(title="Anonymous coffee")
        self.assertEqual(post.user, creator_user)

        swiper.get(reverse("home"))
        swiper_user = User.objects.get(id=swiper.session["_auth_user_id"])
        swipe_response = swiper.post(reverse("swipe_post", args=[post.id]), {"action": Swipe.Action.INTERESTED})
        self.assertEqual(swipe_response.status_code, 302)

        match = Match.objects.get(post=post, swiper=swiper_user)
        chat_response = swiper.get(reverse("chat", args=[match.id]))
        self.assertEqual(chat_response.status_code, 200)
        self.assertContains(chat_response, "Anonymous vibe chat")

    def test_expired_posts_do_not_appear_in_discovery(self):
        self.post.expire_time = timezone.now() - timedelta(minutes=1)
        self.post.save()
        self.client.force_login(self.swiper)
        response = self.client.get(reverse("discover"))
        self.assertNotContains(response, self.post.title)

    def test_discover_ignores_invalid_numeric_filters(self):
        self.client.force_login(self.swiper)
        response = self.client.get(f"{reverse('discover')}?location=not-a-location-id&matched=not-a-match-id")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)

    def test_inactive_post_detail_does_not_show_swipe_controls(self):
        self.post.status = ActivityPost.Status.CANCELLED
        self.post.save(update_fields=["status"])

        self.client.force_login(self.swiper)
        response = self.client.get(reverse("post_detail", args=[self.post.id]))

        self.assertContains(response, "This Plus One is no longer active.")
        self.assertNotContains(response, 'value="interested"')
        self.assertNotContains(response, 'value="pass"')

    def test_owner_inactive_post_detail_does_not_claim_live(self):
        self.post.status = ActivityPost.Status.CANCELLED
        self.post.save(update_fields=["status"])

        self.client.force_login(self.poster)
        response = self.client.get(reverse("post_detail", args=[self.post.id]))

        self.assertContains(response, "This card was cancelled.")
        self.assertNotContains(response, "Your card is live.")

    def test_discover_shows_current_users_live_post_without_swipe_controls(self):
        self.client.force_login(self.poster)
        response = self.client.get(reverse("discover"))

        self.assertContains(response, self.post.title)
        self.assertContains(response, "Your card is live")
        self.assertContains(response, reverse("edit_post", args=[self.post.id]))
        self.assertNotContains(response, 'aria-label="Interested"')

    def test_user_cannot_swipe_own_post(self):
        self.client.force_login(self.poster)
        response = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Swipe.objects.exists())
        self.assertFalse(Match.objects.exists())

    def test_pass_does_not_create_match(self):
        self.client.force_login(self.swiper)
        response = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.PASS})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Swipe.objects.filter(action=Swipe.Action.PASS).exists())
        self.assertFalse(Match.objects.exists())

    def test_pass_can_be_undone_from_discover(self):
        self.client.force_login(self.swiper)
        self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.PASS})

        response = self.client.get(reverse("discover"))
        self.assertContains(response, 'role="status"')
        self.assertContains(response, "Undo pass")
        self.assertContains(response, "Undo if that was a mis-tap.")

        response = self.client.post(reverse("undo_pass", args=[self.post.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Swipe.objects.filter(user=self.swiper, post=self.post).exists())
        self.assertContains(response, "Pass undone. The card is back in your queue.")
        self.assertContains(response, self.post.title)
        self.assertNotContains(response, "Undo pass")

    def test_pass_on_full_post_still_allows_undo(self):
        other = get_user_model().objects.create_user(username="full-pass-other", password="pass")
        Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=other,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.post.status = ActivityPost.Status.MATCHED
        self.post.save(update_fields=["status"])

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.PASS}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Swipe.objects.filter(user=self.swiper, post=self.post, action=Swipe.Action.PASS).exists())
        self.assertContains(response, "Skipped. Undo is available while you keep browsing.")
        self.assertContains(response, "Undo pass")
        self.assertContains(response, self.post.title)

        response = self.client.post(reverse("undo_pass", args=[self.post.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Swipe.objects.filter(user=self.swiper, post=self.post).exists())

    def test_interested_creates_match_when_post_active(self):
        self.client.force_login(self.swiper)
        response = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED})
        self.assertEqual(response.status_code, 302)
        self.assertIn("?matched=", response["Location"])
        self.assertTrue(Match.objects.filter(post=self.post, swiper=self.swiper).exists())
        self.assertTrue(ChatMessage.objects.filter(is_system=True).exists())

    def test_matched_post_does_not_create_second_match(self):
        other = get_user_model().objects.create_user(username="other", password="pass")

        self.client.force_login(self.swiper)
        self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED})

        self.client.force_login(other)
        response = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("discover"))
        self.assertEqual(Match.objects.filter(post=self.post).count(), 1)

    def test_full_post_redirects_with_clear_message(self):
        other = get_user_model().objects.create_user(username="full-post-other", password="pass")
        Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=other,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.post.status = ActivityPost.Status.MATCHED
        self.post.save(update_fields=["status"])
        self.client.force_login(self.swiper)

        response = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That Plus One just filled up.")

    def test_legacy_multi_capacity_post_still_allows_only_one_match(self):
        self.post.capacity = 2
        self.post.save(update_fields=["capacity"])
        other = get_user_model().objects.create_user(username="other-capacity", password="pass")

        self.client.force_login(self.swiper)
        first = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED})
        self.post.refresh_from_db()

        self.client.force_login(other)
        second = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED}, follow=True)

        self.assertIn("?matched=", first["Location"])
        self.assertEqual(self.post.status, ActivityPost.Status.MATCHED)
        self.assertContains(second, "That Plus One just filled up.")
        self.assertEqual(Match.objects.filter(post=self.post).count(), 1)

    def test_swipe_lock_conflict_redirects_without_server_error(self):
        other = get_user_model().objects.create_user(username="other-lock", password="pass")
        self.post.status = ActivityPost.Status.MATCHED
        self.post.save(update_fields=["status"])
        Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=other,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        with (
            patch("plusone.services.matching.SQLITE_LOCK_RETRY_DELAYS", ()),
            patch("plusone.services.matching._record_swipe", side_effect=OperationalError("database table is locked")),
        ):
            response = self.client.post(reverse("swipe_post", args=[self.post.id]), {"action": Swipe.Action.INTERESTED}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That Plus One just filled up.")
        self.assertFalse(Swipe.objects.filter(user=self.swiper, post=self.post).exists())

    def test_refresh_expired_records_defers_sqlite_lock(self):
        with patch("plusone.services.expiration.ActivityPost.objects.filter", side_effect=OperationalError("database table is locked")):
            result = refresh_expired_records()

        self.assertTrue(result["deferred"])

    def test_discover_shows_match_modal_for_participant(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_login(self.swiper)
        response = self.client.get(f"{reverse('discover')}?matched={match.id}")

        self.assertContains(response, "It's a vibe.")
        self.assertContains(response, reverse("chat", args=[match.id]))

    def test_chat_accessible_only_to_participants(self):
        outsider = get_user_model().objects.create_user(username="outsider", password="pass")
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_login(outsider)
        response = self.client.get(reverse("chat", args=[match.id]))
        self.assertEqual(response.status_code, 403)

    def test_chat_expires_after_configured_time(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.client.force_login(self.swiper)
        self.client.get(reverse("chat", args=[match.id]))
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.EXPIRED)

    def test_agree_on_expired_chat_does_not_record_agreement(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "agree"})

        match.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(match.status, Match.Status.EXPIRED)
        self.assertFalse(match.swiper_agreed)

    def test_expired_chat_does_not_accept_new_message(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "send", "message": "Still there?"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ChatMessage.objects.filter(match=match, sender=self.swiper).exists())

    def test_chat_message_moderation_blocks_unsafe_message(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "send", "message": "Here is my phone number"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ChatMessage.objects.filter(match=match, sender=self.swiper).exists())
        self.assertTrue(LLMLog.objects.filter(task_type=LLMLog.TaskType.MODERATION, output_json__flagged=True).exists())

    def test_chat_page_sets_last_message_id_from_loaded_messages(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        ChatMessage.objects.create(match=match, sender=self.poster, message="First")
        second = ChatMessage.objects.create(match=match, sender=self.swiper, message="Second")

        self.client.force_login(self.poster)
        response = self.client.get(reverse("chat", args=[match.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-last-message-id="{second.id}"')

    def test_single_agree_shows_waiting_state(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "agree"}, follow=True)

        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.CHATTING)
        self.assertTrue(match.swiper_agreed)
        self.assertContains(response, "You agreed. Waiting for the other person.")
        self.assertNotContains(response, "You both agreed to meet.")

    def test_chat_page_shows_decision_guidance_and_quick_replies(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        response = self.client.get(reverse("chat", args=[match.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You have five minutes to decide if you both want to meet.")
        self.assertContains(response, "Where exactly?")
        self.assertContains(response, "There in 5")
        self.assertContains(response, "data-chat-warning")
        self.assertContains(response, "Decline closes this match without a report.")
        self.assertContains(response, "Decline match")
        self.assertContains(response, "Report safety issue")
        self.assertContains(response, "data-confirm-submit")

    def test_both_agree_shows_meet_handoff_and_closes_input(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        match.mark_agreed(self.swiper)

        self.client.force_login(self.poster)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "agree"}, follow=True)

        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.AGREED)
        self.assertContains(response, "Meet handoff ready.")
        self.assertContains(response, "You both agreed to meet.")
        self.assertContains(response, "Safety check")
        self.assertContains(response, self.post.location.name)
        self.assertContains(response, "Meet in a public place.")
        self.assertContains(response, "Leave or report if anything feels off.")
        self.assertContains(response, "Back to Dashboard")
        self.assertNotContains(response, "Type fast... this chat expires soon.")

    def test_agreed_match_does_not_accept_new_message(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            status=Match.Status.AGREED,
            poster_agreed=True,
            swiper_agreed=True,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "send", "message": "After agree"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ChatMessage.objects.filter(match=match, message="After agree").exists())

    def test_decline_closes_chat_and_reopens_capacity(self):
        self.post.status = ActivityPost.Status.MATCHED
        self.post.save(update_fields=["status"])
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "decline"}, follow=True)

        match.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(match.status, Match.Status.DECLINED)
        self.assertEqual(match.close_reason, Match.CloseReason.DECLINED)
        self.assertEqual(self.post.status, ActivityPost.Status.ACTIVE)
        self.assertContains(response, "This chat was closed by a participant.")
        self.assertTrue(ChatMessage.objects.filter(match=match, is_system=True, message__icontains="declined").exists())

    def test_report_closes_chat_and_records_reason(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat", args=[match.id]), {"action": "report"}, follow=True)

        match.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(match.status, Match.Status.DECLINED)
        self.assertEqual(match.close_reason, Match.CloseReason.REPORTED)
        self.assertEqual(match.closed_by, self.swiper)
        self.assertTrue(ChatMessage.objects.filter(match=match, is_system=True, message__icontains="Safety report").exists())

    def test_chat_messages_endpoint_returns_messages_after_id(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        first = ChatMessage.objects.create(match=match, sender=self.poster, message="First")
        second = ChatMessage.objects.create(match=match, sender=self.swiper, message="Second")

        self.client.force_login(self.poster)
        response = self.client.get(f"{reverse('chat_messages', args=[match.id])}?after={first.id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual([message["id"] for message in data["messages"]], [second.id])
        self.assertEqual(data["messages"][0]["sender_label"], "Anonymous match")

    def test_chat_messages_endpoint_expires_current_chat_without_global_refresh(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.client.force_login(self.poster)
        with patch("plusone.views.refresh_expired_records") as mocked_refresh:
            response = self.client.get(reverse("chat_messages", args=[match.id]))

        mocked_refresh.assert_not_called()
        match.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(match.status, Match.Status.EXPIRED)
        self.assertFalse(response.json()["chat_active"])

    def test_chat_messages_endpoint_posts_message_json(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat_messages", args=[match.id]), {"message": "See you there"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["message"]["message"], "See you there")
        self.assertEqual(data["message"]["sender_label"], "You")
        self.assertTrue(ChatMessage.objects.filter(match=match, sender=self.swiper, message="See you there").exists())

    def test_chat_messages_endpoint_blocks_unsafe_message_json(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.swiper)
        response = self.client.post(reverse("chat_messages", args=[match.id]), {"message": "Bring a weapon to the game"})

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertTrue(data["flagged"])
        self.assertFalse(ChatMessage.objects.filter(match=match, sender=self.swiper).exists())

    def test_chat_messages_endpoint_reports_inactive_after_agreement(self):
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            status=Match.Status.AGREED,
            poster_agreed=True,
            swiper_agreed=True,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.poster)
        response = self.client.get(reverse("chat_messages", args=[match.id]))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["chat_status"], Match.Status.AGREED)
        self.assertFalse(data["chat_active"])

    def test_llm_parser_logs_output_with_fallback(self):
        result = parse_activity_text(self.poster, "Tonight 7pm basketball game at the sports hall")
        self.assertEqual(result["activity_type"], ActivityPost.ActivityType.SPORTS)
        self.assertTrue(LLMLog.objects.filter(task_type=LLMLog.TaskType.PARSE_POST).exists())

    def test_parser_leaves_start_time_empty_without_explicit_time(self):
        result = parse_activity_text(self.poster, "Tomorrow I wanna play basketball at sports center")

        self.assertEqual(result["activity_type"], ActivityPost.ActivityType.SPORTS)
        self.assertEqual(result["start_time"], "")
        self.assertEqual(post_initial_from_ai(result)["start_time"], "")

    def test_parser_leaves_start_time_empty_for_ambiguous_hour(self):
        result = parse_activity_text(self.poster, "Tomorrow I wanna play basketball at sports center at 7")

        self.assertEqual(result["activity_type"], ActivityPost.ActivityType.SPORTS)
        self.assertEqual(result["start_time"], "")
        self.assertEqual(post_initial_from_ai(result)["start_time"], "")

    def test_parser_suggests_options_for_ambiguous_hour(self):
        options = suggest_ambiguous_time_options("Tomorrow I wanna play basketball at sports center at 7")

        self.assertEqual([option["label"] for option in options], ["Morning", "Evening"])
        self.assertTrue(all(option["value"].endswith(("07:00", "19:00")) for option in options))

    def test_parser_accepts_dotted_am_time(self):
        result = parse_activity_text(self.poster, "Tomorrow I wanna play basketball at sports center at 7 a.m.")

        parsed_start = timezone.localtime(timezone.datetime.fromisoformat(result["start_time"]))
        expected_day = (timezone.localtime() + timedelta(days=1)).date()
        self.assertEqual(parsed_start.date(), expected_day)
        self.assertEqual(parsed_start.hour, 7)
        self.assertEqual(parsed_start.minute, 0)
        self.assertEqual(post_initial_from_ai(result)["start_time"], parsed_start.strftime("%Y-%m-%dT%H:%M"))

    def test_llm_parser_preserves_explicit_user_time(self):
        wrong_future_time = (timezone.localtime() + timedelta(days=1)).replace(hour=12, minute=30, second=0, microsecond=0)
        raw_response = json.dumps(
            {
                "title": "Basketball",
                "description": "I wanna play basketball at sports center",
                "activity_type": ActivityPost.ActivityType.SPORTS,
                "location_name": "Campus Sports Hall",
                "start_time": wrong_future_time.isoformat(),
                "expire_minutes": 60,
            }
        )

        class Message:
            content = raw_response

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        with (
            patch("plusone.ai._llm_client", return_value=(object(), {"model": "deepseek-v4-flash", "strategy": "deepseek"})),
            patch("plusone.ai._chat_completion", return_value=Response()),
        ):
            result = parse_activity_text(self.poster, "Tomorrow I wanna play basketball at sports center at 7 a.m.")

        parsed_start = timezone.localtime(timezone.datetime.fromisoformat(result["start_time"]))
        self.assertEqual(parsed_start.hour, 7)
        self.assertEqual(parsed_start.minute, 0)
        self.assertEqual(result["expire_minutes"], 60)

    def test_parser_prefers_meridiem_over_clock_pattern(self):
        result = parse_activity_text(self.poster, "Tomorrow 7:30 pm basketball game at the sports hall")

        parsed_start = timezone.localtime(timezone.datetime.fromisoformat(result["start_time"]))
        self.assertEqual(parsed_start.hour, 19)
        self.assertEqual(parsed_start.minute, 30)

    def test_parser_clears_llm_guessed_start_time_without_explicit_time(self):
        guessed_start_time = (timezone.localtime() + timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0)
        raw_response = json.dumps(
            {
                "title": "Basketball Game",
                "description": "Play basketball at the sports center",
                "activity_type": ActivityPost.ActivityType.SPORTS,
                "location_name": "Campus Sports Hall",
                "start_time": guessed_start_time.isoformat(),
                "expire_minutes": 120,
            }
        )

        class Message:
            content = raw_response

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        with (
            patch("plusone.ai._llm_client", return_value=(object(), {"model": "deepseek-v4-flash", "strategy": "deepseek"})),
            patch("plusone.ai._chat_completion", return_value=Response()),
        ):
            result = parse_activity_text(self.poster, "Tomorrow I wanna play basketball at sports center")

        self.assertEqual(result["start_time"], "")
        self.assertEqual(result["expire_minutes"], 120)

    def test_llm_parser_rejects_past_llm_start_time(self):
        raw_response = json.dumps(
            {
                "title": "Basketball Game",
                "description": "Tomorrow 7pm basketball game at the sports hall",
                "activity_type": ActivityPost.ActivityType.SPORTS,
                "location_name": "Campus Sports Hall",
                "start_time": "2025-04-08T19:00:00",
                "expire_minutes": 120,
            }
        )

        class Message:
            content = raw_response

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        with (
            patch("plusone.ai._llm_client", return_value=(object(), {"model": "deepseek-v4-flash", "strategy": "deepseek"})),
            patch("plusone.ai._chat_completion", return_value=Response()),
        ):
            result = parse_activity_text(self.poster, "Tomorrow 7pm basketball game at the sports hall")

        parsed_start = timezone.datetime.fromisoformat(result["start_time"])
        self.assertGreater(parsed_start, timezone.localtime())
        self.assertFalse(result["start_time"].startswith("2025-04-08"))
        self.assertEqual(result["expire_minutes"], 120)

    def test_dashboard_separates_active_matches_expired(self):
        expired = ActivityPost.objects.create(
            user=self.poster,
            title="Old lunch",
            description="Expired",
            activity_type=ActivityPost.ActivityType.FOOD,
            location=self.location,
            start_time=timezone.now() - timedelta(hours=2),
            expire_time=timezone.now() - timedelta(hours=1),
            status=ActivityPost.Status.EXPIRED,
        )
        Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        agreed_post = ActivityPost.objects.create(
            user=self.poster,
            title="Agreed handoff",
            description="Ready to meet",
            activity_type=ActivityPost.ActivityType.STUDY,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=2),
            expire_time=timezone.now() + timedelta(minutes=45),
            status=ActivityPost.Status.MATCHED,
        )
        Match.objects.create(
            post=agreed_post,
            poster=self.poster,
            swiper=self.swiper,
            status=Match.Status.AGREED,
            poster_agreed=True,
            swiper_agreed=True,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        cancelled = ActivityPost.objects.create(
            user=self.poster,
            title="Cancelled coffee",
            description="Cancelled",
            activity_type=ActivityPost.ActivityType.FOOD,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=2),
            expire_time=timezone.now() + timedelta(minutes=30),
            status=ActivityPost.Status.CANCELLED,
        )
        self.client.force_login(self.poster)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, self.post.title)
        self.assertContains(response, expired.title)
        self.assertContains(response, cancelled.title)
        self.assertContains(response, "Open chats need a decision")
        self.assertContains(response, "Meet handoffs")
        self.assertNotContains(response, "students swiped right")

    def test_dashboard_uses_state_panel_instead_of_decorative_visual(self):
        self.client.force_login(self.swiper)
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Nothing live right now.")
        self.assertContains(response, 'class="dashboard-state-panel state-empty"')
        self.assertNotContains(response, "dashboard-hero-visual")
        self.assertNotContains(response, "hero-summary-card")

    def test_dashboard_match_actions_reflect_status(self):
        Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        agreed_post = ActivityPost.objects.create(
            user=self.poster,
            title="Agreed handoff",
            description="Ready to meet",
            activity_type=ActivityPost.ActivityType.STUDY,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=2),
            expire_time=timezone.now() + timedelta(minutes=45),
            status=ActivityPost.Status.MATCHED,
        )
        Match.objects.create(
            post=agreed_post,
            poster=self.poster,
            swiper=self.swiper,
            status=Match.Status.AGREED,
            poster_agreed=True,
            swiper_agreed=True,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )
        expired_post = ActivityPost.objects.create(
            user=self.poster,
            title="Expired chat",
            description="History only",
            activity_type=ActivityPost.ActivityType.FOOD,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=2),
            expire_time=timezone.now() + timedelta(minutes=45),
            status=ActivityPost.Status.MATCHED,
        )
        Match.objects.create(
            post=expired_post,
            poster=self.poster,
            swiper=self.swiper,
            status=Match.Status.EXPIRED,
            chat_expires_at=timezone.now() - timedelta(minutes=1),
        )
        declined_post = ActivityPost.objects.create(
            user=self.poster,
            title="Declined chat",
            description="No longer meeting",
            activity_type=ActivityPost.ActivityType.EXPLORE,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=2),
            expire_time=timezone.now() + timedelta(minutes=45),
            status=ActivityPost.Status.MATCHED,
        )
        Match.objects.create(
            post=declined_post,
            poster=self.poster,
            swiper=self.swiper,
            status=Match.Status.DECLINED,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.poster)
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "You have a chat waiting.")
        self.assertContains(response, "Open waiting chat")
        self.assertContains(response, "Open chats need a decision")
        self.assertContains(response, "Handle these before creating or browsing more cards.")
        self.assertContains(response, '<span class="teal-pill">Open chat</span>', count=1)
        self.assertContains(response, "View handoff")
        self.assertContains(response, "Chat ended")
        self.assertContains(response, "Declined")

    def test_nav_shows_open_chat_badge(self):
        Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.client.force_login(self.poster)
        response = self.client.get(reverse("discover"))

        self.assertContains(response, '<span class="nav-badge">1</span>')

    def test_about_page_describes_product_flow_and_privacy(self):
        response = self.client.get(reverse("about"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From plan to handoff in one short flow.")
        self.assertContains(response, "What stays hidden")
        self.assertContains(response, "Built for campus timing")
        self.assertContains(response, "Open Discover")

    def test_expire_records_command_marks_old_posts_and_chats(self):
        self.post.expire_time = timezone.now() - timedelta(minutes=1)
        self.post.save(update_fields=["expire_time"])
        match = Match.objects.create(
            post=self.post,
            poster=self.poster,
            swiper=self.swiper,
            chat_expires_at=timezone.now() - timedelta(seconds=1),
        )

        output = StringIO()
        call_command("expire_records", stdout=output)

        self.post.refresh_from_db()
        match.refresh_from_db()
        self.assertEqual(self.post.status, ActivityPost.Status.EXPIRED)
        self.assertEqual(match.status, Match.Status.EXPIRED)
        self.assertIn("Expired", output.getvalue())

    def test_cleanup_anonymous_sessions_dry_run_does_not_delete(self):
        User = get_user_model()
        old_user = User.objects.create_user(username="anon_old")
        old_user.date_joined = timezone.now() - timedelta(days=10)
        old_user.last_login = timezone.now() - timedelta(days=10)
        old_user.save(update_fields=["date_joined", "last_login"])

        output = StringIO()
        call_command("cleanup_anonymous_sessions", "--days", "7", stdout=output)

        self.assertTrue(User.objects.filter(username="anon_old").exists())
        self.assertIn("Dry run", output.getvalue())

    def test_cleanup_anonymous_sessions_commit_keeps_live_identity(self):
        User = get_user_model()
        stale_user = User.objects.create_user(username="anon_stale")
        stale_user.date_joined = timezone.now() - timedelta(days=10)
        stale_user.last_login = timezone.now() - timedelta(days=10)
        stale_user.save(update_fields=["date_joined", "last_login"])
        live_user = User.objects.create_user(username="anon_live")
        live_user.date_joined = timezone.now() - timedelta(days=10)
        live_user.last_login = timezone.now() - timedelta(days=10)
        live_user.save(update_fields=["date_joined", "last_login"])
        ActivityPost.objects.create(
            user=live_user,
            title="Live anonymous post",
            description="Still active",
            activity_type=ActivityPost.ActivityType.STUDY,
            location=self.location,
            start_time=timezone.now() + timedelta(hours=1),
            expire_time=timezone.now() + timedelta(minutes=30),
        )

        call_command("cleanup_anonymous_sessions", "--days", "7", "--commit", stdout=StringIO())

        self.assertFalse(User.objects.filter(username="anon_stale").exists())
        self.assertTrue(User.objects.filter(username="anon_live").exists())
