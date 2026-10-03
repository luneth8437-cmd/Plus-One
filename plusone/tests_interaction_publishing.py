"""Publishing recovery boundaries use only the isolated test database and fake AI."""
import json
from datetime import timedelta
from html.parser import HTMLParser
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from plusone.context_processors import session_scope
from plusone.models import ActivityPost, BrowserBudgetBucket, CampusLocation


class OwnedSubmitControls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_id = None
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.form_id = attributes.get("id")
        if tag == "button" and attributes.get("type", "submit") == "submit":
            if attributes.get("form", self.form_id) == "create-reviewed-form":
                self.buttons.append(attributes)

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_id = None


@override_settings(DEBUG=True, PLUSONE_MODERATION_MODE="rules", PLUSONE_NEW_MATCHES_ENABLED=True)
class PublishingInteractionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("anon_publishing_interaction")
        self.location = CampusLocation.objects.first()
        self.start = timezone.localtime().replace(second=0, microsecond=0) + timedelta(hours=2)
        self.client.force_login(self.user)
        self.proposal = {"title": "Suggested C", "description": "Suggested study plan", "activity_type": "study",
            "location_name": self.location.name, "start_time": self.start.isoformat(),
            "expected_end_time": (self.start + timedelta(hours=1)).isoformat(), "expire_minutes": 30,
            "validation": {"warnings": [], "missing_fields": []}, "field_sources": {"expected_end_time": "user_text"}}

    def manual(self, **changes):
        return {"action": "assist", "session_scope": session_scope(self.user), "request_id": str(uuid4()),
            "draft_revision": "5", "title": "Reviewed A", "description": "Reviewed description", "activity_type": "study",
            "location": str(self.location.pk), "start_time": self.start.strftime("%Y-%m-%dT%H:%M"),
            "expected_end_time": (self.start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "expire_minutes": "45", "assist_text": "A different actual study plan", **changes}

    def assist(self, **changes):
        with patch("plusone.views.parse_activity_text", return_value=self.proposal):
            return self.client.post(reverse("create_post"), self.manual(**changes))

    def test_assist_is_prg_for_browser_and_api_accept_headers(self):
        for accept in (None, "text/html", "application/json"):
            with self.subTest(accept=accept):
                headers = {"HTTP_ACCEPT": accept} if accept else {}
                with patch("plusone.views.parse_activity_text", return_value=self.proposal) as parse:
                    response = self.client.post(reverse("create_post"), self.manual(), **headers)
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.url.startswith(reverse("create_post") + "?draft="))
                    review = self.client.get(response.url)
                    refreshed = self.client.get(response.url)
                self.assertEqual(parse.call_count, 1)
                self.assertEqual(review.context["post_form"]["title"].value(), "Reviewed A")
                self.assertEqual(refreshed.context["draft_proposal"]["fields"]["title"], "Suggested C")
                self.assertEqual(review.context["create_draft_revision"], 5)
                self.assertFalse(ActivityPost.objects.filter(user=self.user).exists())
        self.assertEqual(BrowserBudgetBucket.objects.get(scope="ai").count, 3)

    def test_review_get_uses_fresh_publish_ids_without_repeating_ai(self):
        response = self.assist()
        with patch("plusone.views.parse_activity_text") as parse:
            first = self.client.get(response.url)
            second = self.client.get(response.url)
        UUID(first.context["request_id"])
        UUID(second.context["request_id"])
        self.assertNotEqual(first.context["request_id"], second.context["request_id"])
        parse.assert_not_called()

    def test_default_enter_submit_is_publish_even_when_suggestion_controls_precede_form(self):
        response = self.client.get(self.assist().url)
        controls = OwnedSubmitControls()
        controls.feed(response.content.decode())
        self.assertGreater(len(controls.buttons), 3)
        first = controls.buttons[0]
        self.assertEqual(first["name"], "action")
        self.assertEqual(first["value"], "publish")
        self.assertEqual(first["tabindex"], "-1")
        self.assertNotIn("formnovalidate", first)
        self.assertIn("data-default-publish-submit", first)

    def test_keep_is_prg_and_retains_current_manual_edits_over_the_old_snapshot(self):
        review = self.client.get(self.assist().url)
        response = self.client.post(reverse("create_post"), self.manual(action="keep_reviewed_draft", title="Later B",
            review_token=review.context["create_review_token"], reviewed_payload=json.dumps({"title": "Reviewed A"})))
        self.assertEqual(response.status_code, 302)
        refreshed = self.client.get(response.url)
        self.assertEqual(refreshed.context["post_form"]["title"].value(), "Later B")
        self.assertEqual(refreshed.context["create_draft_revision"], 6)
        self.assertIsNone(refreshed.context["draft_proposal"])
        self.assertFalse(ActivityPost.objects.filter(user=self.user).exists())

    def test_explicit_apply_is_prg_and_unknown_location_stays_blank(self):
        proposal = {"fields": {"title": "Suggested C", "location": None}}
        response = self.client.post(reverse("create_post"), self.manual(action="apply_draft", proposal_payload=json.dumps(proposal)))
        self.assertEqual(response.status_code, 302)
        review = self.client.get(response.url)
        self.assertEqual(review.context["post_form"]["title"].value(), "Suggested C")
        self.assertEqual(review.context["post_form"]["location"].value(), "")
        self.assertEqual(review.context["create_draft_revision"], 6)
        self.assertFalse(ActivityPost.objects.filter(user=self.user).exists())

    def test_discard_removes_the_saved_review_and_returns_a_blank_form_without_js(self):
        response = self.assist()
        review = self.client.get(response.url)
        discarded = self.client.post(reverse("create_post"), self.manual(action="discard_draft",
            review_token=review.context["create_review_token"]))
        self.assertRedirects(discarded, reverse("create_post"), fetch_redirect_response=False)
        blank = self.client.get(discarded.url)
        self.assertIsNone(blank.context["post_form"]["title"].value())
        self.assertIsNone(blank.context["draft_proposal"])
        stale = self.client.get(response.url)
        self.assertIsNone(stale.context["post_form"]["title"].value())
        self.assertEqual(stale.context["create_review_token"], "")

    def test_review_tokens_cannot_restore_a_different_identitys_private_draft(self):
        response = self.assist(description="Private planning details")
        other = get_user_model().objects.create_user("anon_different_publishing_guest")
        self.client.force_login(other)
        review = self.client.get(response.url)
        self.assertIsNone(review.context["post_form"]["title"].value())
        self.assertNotContains(review, "Private planning details")
        self.assertIsNone(review.context["draft_proposal"])

    def test_assist_service_failure_retains_http_status_and_manual_fields(self):
        with patch("plusone.views.moderate_activity_text", return_value={"service_unavailable": True}) as moderation, \
                patch("plusone.views.parse_activity_text") as parse:
            response = self.client.post(reverse("create_post"), self.manual(title="Later B"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.context["post_form"]["title"].value(), "Later B")
        moderation.assert_called_once()
        parse.assert_not_called()

    def test_invalid_assist_redirects_with_errors_without_discarding_manual_fields(self):
        with patch("plusone.views.parse_activity_text") as parse:
            response = self.client.post(reverse("create_post"), self.manual(assist_text=""))
            self.assertEqual(response.status_code, 302)
            review = self.client.get(response.url)
        self.assertTrue(review.context["assist_form"].errors)
        self.assertEqual(review.context["post_form"]["title"].value(), "Reviewed A")
        parse.assert_not_called()
