from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from plusone.models import ActivityPost, CampusLocation, LLMLog, Match, ProductEvent, UserProfile


DEMO_USERNAMES = ("demo_alex", "demo_blair")


class Command(BaseCommand):
    help = "Seed demo users, campus locations, and activity posts for the Plus One MVP."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete only demo users and their data before seeding.")

    def handle(self, *args, **options):
        User = get_user_model()
        if options["reset"]:
            if not settings.DEBUG:
                raise CommandError("seed_demo --reset is disabled when DEBUG=False.")
            demo_users = User.objects.filter(username__in=DEMO_USERNAMES)
            demo_user_ids = list(demo_users.values_list("id", flat=True))
            demo_post_ids = list(ActivityPost.objects.filter(user_id__in=demo_user_ids).values_list("id", flat=True))
            demo_match_ids = list(
                Match.objects.filter(Q(poster_id__in=demo_user_ids) | Q(swiper_id__in=demo_user_ids)).values_list(
                    "id", flat=True
                )
            )
            ProductEvent.objects.filter(
                Q(user_id__in=demo_user_ids) | Q(post_id__in=demo_post_ids) | Q(match_id__in=demo_match_ids)
            ).delete()
            LLMLog.objects.filter(user_id__in=demo_user_ids).delete()
            demo_users.delete()

        locations = [
            ("North Dining Hall", CampusLocation.LocationType.DINING, "North Campus"),
            ("Campus Sports Hall", CampusLocation.LocationType.SPORTS, "Central Campus"),
            ("Main Library", CampusLocation.LocationType.STUDY, "Library Quad"),
            ("Student Center", CampusLocation.LocationType.EVENT, "Central Campus"),
            ("Campus Quad", CampusLocation.LocationType.OUTDOOR, "South Lawn"),
        ]
        location_map = {}
        for name, location_type, area in locations:
            location, _ = CampusLocation.objects.get_or_create(
                name=name,
                defaults={"location_type": location_type, "area": area},
            )
            location_map[name] = location

        users = {
            "demo_alex": {
                "display_name": "Alex Chen",
                "avatar_initial": "A",
                "major": "Computer Science",
                "year": "Sophomore",
                "campus_area": "North Campus",
                "interests": "basketball, lunch, study sprints",
            },
            "demo_blair": {
                "display_name": "Blair Morgan",
                "avatar_initial": "B",
                "major": "Design",
                "year": "Junior",
                "campus_area": "Central Campus",
                "interests": "club fairs, campus events, coffee",
            },
        }
        user_map = {}
        for username, profile in users.items():
            user, created = User.objects.get_or_create(username=username, defaults={"email": f"{username}@example.edu"})
            if created:
                user.set_password("plusone123")
                user.save()
            UserProfile.objects.update_or_create(user=user, defaults=profile)
            user_map[username] = user

        now = timezone.localtime()
        posts = [
            {
                "user": user_map["demo_alex"],
                "title": "Basketball game tonight",
                "description": "Heading to the campus game and want a quick vibe check first.",
                "activity_type": ActivityPost.ActivityType.SPORTS,
                "location": location_map["Campus Sports Hall"],
                "start_time": now.replace(hour=19, minute=30, second=0, microsecond=0),
                "expire_time": timezone.now() + timedelta(minutes=90),
                "status": ActivityPost.Status.ACTIVE,
            },
            {
                "user": user_map["demo_alex"],
                "title": "Lunch at Mensa",
                "description": "Quick lunch before class, prefer someone who also wants a short chat first.",
                "activity_type": ActivityPost.ActivityType.FOOD,
                "location": location_map["North Dining Hall"],
                "start_time": timezone.now() + timedelta(minutes=45),
                "expire_time": timezone.now() + timedelta(minutes=35),
                "status": ActivityPost.Status.ACTIVE,
            },
            {
                "user": user_map["demo_blair"],
                "title": "Study sprint",
                "description": "One-hour focused study sprint at the library. Low pressure, quiet table preferred.",
                "activity_type": ActivityPost.ActivityType.STUDY,
                "location": location_map["Main Library"],
                "start_time": timezone.now() + timedelta(hours=2),
                "expire_time": timezone.now() + timedelta(minutes=75),
                "status": ActivityPost.Status.ACTIVE,
            },
            {
                "user": user_map["demo_blair"],
                "title": "Club fair walkthrough",
                "description": "Want to explore booths together and compare what looks interesting.",
                "activity_type": ActivityPost.ActivityType.CLUB,
                "location": location_map["Student Center"],
                "start_time": timezone.now() + timedelta(hours=3),
                "expire_time": timezone.now() + timedelta(minutes=120),
                "status": ActivityPost.Status.ACTIVE,
            },
            {
                "user": user_map["demo_alex"],
                "title": "Old coffee run",
                "description": "Expired demo card for the dashboard state.",
                "activity_type": ActivityPost.ActivityType.FOOD,
                "location": location_map["North Dining Hall"],
                "start_time": timezone.now() - timedelta(hours=2),
                "expire_time": timezone.now() - timedelta(hours=1),
                "status": ActivityPost.Status.EXPIRED,
            },
            {
                "user": user_map["demo_alex"],
                "title": "Cancelled campus walk",
                "description": "Cancelled demo card for the dashboard state.",
                "activity_type": ActivityPost.ActivityType.EXPLORE,
                "location": location_map["Campus Quad"],
                "start_time": timezone.now() + timedelta(hours=2),
                "expire_time": timezone.now() + timedelta(minutes=80),
                "status": ActivityPost.Status.CANCELLED,
            },
        ]

        for data in posts:
            ActivityPost.objects.update_or_create(
                user=data["user"],
                title=data["title"],
                defaults=data,
            )

        self.stdout.write(self.style.SUCCESS("Seeded Plus One demo data. Demo users: demo_alex / demo_blair, password: plusone123"))
