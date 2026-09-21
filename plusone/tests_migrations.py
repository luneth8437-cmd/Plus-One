from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ReliableLifecycleMigrationCompatibilityTests(TransactionTestCase):
    migrate_from = [("plusone", "0009_productevent_meetup_confirmed")]
    migrate_to = [("plusone", "0014_activitypost_expected_end_time")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.addCleanup(self._restore_latest_schema)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._create_legacy_rows(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def _restore_latest_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _create_legacy_rows(self, apps):
        app_label, model_name = settings.AUTH_USER_MODEL.split(".")
        User = apps.get_model(app_label, model_name)
        UserProfile = apps.get_model("plusone", "UserProfile")
        CampusLocation = apps.get_model("plusone", "CampusLocation")
        ActivityPost = apps.get_model("plusone", "ActivityPost")
        Match = apps.get_model("plusone", "Match")
        ChatMessage = apps.get_model("plusone", "ChatMessage")
        ProductEvent = apps.get_model("plusone", "ProductEvent")

        poster = User.objects.create(username="legacy-poster")
        swiper = User.objects.create(username="legacy-swiper")
        profile = UserProfile.objects.create(user=poster, display_name="Legacy Poster")
        location, _ = CampusLocation.objects.get_or_create(
            name="Legacy Sports Hall",
            defaults={"location_type": "sports", "area": "Legacy Campus"},
        )
        now = timezone.now()
        self.post_expiry = now + timedelta(minutes=47)
        self.chat_deadline = now + timedelta(minutes=5)
        post = ActivityPost.objects.create(
            user=poster,
            title="Legacy badminton",
            description="Created before reliable lifecycle fields existed.",
            activity_type="sports",
            location=location,
            start_time=now + timedelta(hours=1),
            expire_time=self.post_expiry,
            status="matched",
        )
        match = Match.objects.create(
            post=post,
            poster=poster,
            swiper=swiper,
            status="chatting",
            chat_expires_at=self.chat_deadline,
        )
        message = ChatMessage.objects.create(match=match, sender=swiper, message="Legacy hello")
        publish_event = ProductEvent.objects.create(
            name="publish_card",
            user=poster,
            post=post,
            properties={"legacy": True},
        )
        match_event = ProductEvent.objects.create(
            name="match_created",
            user=swiper,
            match=match,
            properties={"legacy": True},
        )
        unknown_event = ProductEvent.objects.create(
            name="message_sent",
            properties={"legacy": True, "attribution": "unknown"},
        )

        self.poster_id = poster.pk
        self.profile_id = profile.pk
        self.post_id = post.pk
        self.post_created_at = post.created_at
        self.match_id = match.pk
        self.match_created_at = match.created_at
        self.message_id = message.pk
        self.publish_event_id = publish_event.pk
        self.match_event_id = match_event.pk
        self.unknown_event_id = unknown_event.pk

    def test_existing_rows_and_known_references_survive_upgrade(self):
        UserProfile = self.apps.get_model("plusone", "UserProfile")
        ActivityPost = self.apps.get_model("plusone", "ActivityPost")
        Match = self.apps.get_model("plusone", "Match")
        ChatMessage = self.apps.get_model("plusone", "ChatMessage")
        ProductEvent = self.apps.get_model("plusone", "ProductEvent")

        profile = UserProfile.objects.get(pk=self.profile_id)
        post = ActivityPost.objects.get(pk=self.post_id)
        match = Match.objects.get(pk=self.match_id)
        message = ChatMessage.objects.get(pk=self.message_id)

        self.assertIsNone(profile.last_seen_at)
        self.assertEqual(post.expire_time, self.post_expiry)
        self.assertIsNone(post.expected_end_time)
        self.assertIsNone(post.request_id)
        self.assertEqual(post.request_fingerprint, "")
        self.assertEqual(match.status, "chatting")
        self.assertEqual(match.chat_expires_at, self.chat_deadline)
        self.assertIsNone(match.waiting_expires_at)
        self.assertIsNone(match.chat_started_at)
        self.assertIsNone(match.poster_last_present_at)
        self.assertIsNone(match.swiper_last_present_at)
        self.assertIsNone(message.request_id)
        self.assertEqual(message.request_fingerprint, "")

        publish_event = ProductEvent.objects.get(pk=self.publish_event_id)
        self.assertEqual(publish_event.post_reference, self.post_id)
        self.assertEqual(publish_event.post_created_at, self.post_created_at)
        self.assertIsNone(publish_event.match_reference)
        self.assertEqual(publish_event.event_key, f"publish_card:post:{self.post_id}")

        match_event = ProductEvent.objects.get(pk=self.match_event_id)
        self.assertEqual(match_event.post_reference, self.post_id)
        self.assertEqual(match_event.post_created_at, self.post_created_at)
        self.assertEqual(match_event.match_reference, self.match_id)
        self.assertEqual(match_event.match_created_at, self.match_created_at)
        self.assertEqual(match_event.event_key, f"match_created:match:{self.match_id}")

        unknown_event = ProductEvent.objects.get(pk=self.unknown_event_id)
        self.assertIsNone(unknown_event.post_reference)
        self.assertIsNone(unknown_event.post_created_at)
        self.assertIsNone(unknown_event.match_reference)
        self.assertIsNone(unknown_event.match_created_at)
        self.assertIsNone(unknown_event.event_key)
