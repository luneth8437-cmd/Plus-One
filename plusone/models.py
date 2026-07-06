from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=80)
    avatar_initial = models.CharField(max_length=2, blank=True)
    major = models.CharField(max_length=100, blank=True)
    year = models.CharField(max_length=40, blank=True)
    campus_area = models.CharField(max_length=80, blank=True)
    interests = models.CharField(max_length=240, blank=True)

    def __str__(self):
        return self.display_name or self.user.username

    @property
    def initial(self):
        if self.avatar_initial:
            return self.avatar_initial[:2].upper()
        source = self.display_name or self.user.username
        return source[:1].upper()


class CampusLocation(models.Model):
    class LocationType(models.TextChoices):
        DINING = "dining", "Dining"
        SPORTS = "sports", "Sports"
        STUDY = "study", "Study"
        EVENT = "event", "Event"
        OUTDOOR = "outdoor", "Outdoor"
        OTHER = "other", "Other"

    name = models.CharField(max_length=120, unique=True)
    location_type = models.CharField(max_length=20, choices=LocationType.choices)
    area = models.CharField(max_length=80)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ["area", "name"]

    def __str__(self):
        return f"{self.name} ({self.area})"


class ActivityPostQuerySet(models.QuerySet):
    def active(self):
        return (
            self.filter(status=ActivityPost.Status.ACTIVE, expire_time__gt=timezone.now())
            .annotate(holding_matches=Count("matches", filter=Q(matches__status__in=Match.HOLDING_STATUSES)))
            .filter(holding_matches__lt=1)
        )


class ActivityPost(models.Model):
    """Temporary card shown in Discover until it expires, matches, or is cancelled."""

    class ActivityType(models.TextChoices):
        FOOD = "food", "Food"
        SPORTS = "sports", "Sports"
        STUDY = "study", "Study"
        CLUB = "club", "Club fair"
        EXPLORE = "explore", "Explore"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        MATCHED = "matched", "Matched"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activity_posts")
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    activity_type = models.CharField(max_length=20, choices=ActivityType.choices)
    location = models.ForeignKey(CampusLocation, on_delete=models.PROTECT, related_name="activity_posts")
    start_time = models.DateTimeField()
    expire_time = models.DateTimeField()
    capacity = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ActivityPostQuerySet.as_manager()

    class Meta:
        ordering = ["start_time", "expire_time"]
        indexes = [
            models.Index(fields=["activity_type", "status"]),
            models.Index(fields=["start_time", "expire_time"]),
            models.Index(fields=["status", "expire_time"]),
            models.Index(fields=["user", "status", "expire_time"], name="post_user_status_exp_idx"),
        ]

    def __str__(self):
        return self.title

    @property
    def is_expired(self):
        return self.expire_time <= timezone.now() or self.status == self.Status.EXPIRED

    def mark_expired_if_needed(self, save=True):
        if self.expire_time <= timezone.now() and self.status == self.Status.ACTIVE:
            self.status = self.Status.EXPIRED
            if save:
                self.save(update_fields=["status", "updated_at"])
        return self.status == self.Status.EXPIRED

    @property
    def held_spots(self):
        return self.matches.filter(status__in=Match.HOLDING_STATUSES).count()

    @property
    def spots_remaining(self):
        return max(0, 1 - self.held_spots)


class Swipe(models.Model):
    """One user's interest/pass decision for a post."""

    class Action(models.TextChoices):
        INTERESTED = "interested", "Interested"
        PASS = "pass", "Pass"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="swipes")
    post = models.ForeignKey(ActivityPost, on_delete=models.CASCADE, related_name="swipes")
    action = models.CharField(max_length=20, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "post"], name="unique_swipe_per_user_post"),
        ]

    def __str__(self):
        return f"{self.user} {self.action} {self.post}"


class Match(models.Model):
    """A five-minute anonymous chat created when someone swipes interested."""

    class Status(models.TextChoices):
        CHATTING = "chatting", "Chatting"
        AGREED = "agreed", "Agreed to meet"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    class CloseReason(models.TextChoices):
        DECLINED = "declined", "Declined by participant"
        REPORTED = "reported", "Reported safety issue"

    HOLDING_STATUSES = (Status.CHATTING, Status.AGREED)

    post = models.ForeignKey(ActivityPost, on_delete=models.CASCADE, related_name="matches")
    poster = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posted_matches")
    swiper = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="swiped_matches")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CHATTING)
    poster_agreed = models.BooleanField(default=False)
    swiper_agreed = models.BooleanField(default=False)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="closed_matches",
        null=True,
        blank=True,
    )
    close_reason = models.CharField(max_length=20, choices=CloseReason.choices, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    chat_expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "swiper"], name="unique_match_per_post_swiper"),
        ]
        indexes = [
            models.Index(fields=["status", "chat_expires_at"], name="match_status_exp_idx"),
            models.Index(fields=["poster", "status", "created_at"], name="match_poster_status_idx"),
            models.Index(fields=["swiper", "status", "created_at"], name="match_swiper_status_idx"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.post} match: {self.poster} + {self.swiper}"

    def participant_ids(self):
        return {self.poster_id, self.swiper_id}

    def is_participant(self, user):
        return user.is_authenticated and user.id in self.participant_ids()

    @property
    def chat_expired(self):
        return self.chat_expires_at <= timezone.now()

    def mark_chat_expired_if_needed(self, save=True):
        if self.chat_expired and self.status == self.Status.CHATTING:
            self.status = self.Status.EXPIRED
            if save:
                self.save(update_fields=["status"])
        return self.status == self.Status.EXPIRED

    def mark_agreed(self, user):
        if user.id == self.poster_id:
            self.poster_agreed = True
        if user.id == self.swiper_id:
            self.swiper_agreed = True
        if self.poster_agreed and self.swiper_agreed:
            self.status = self.Status.AGREED
        self.save(update_fields=["poster_agreed", "swiper_agreed", "status"])


class ChatMessage(models.Model):
    """Message inside a match; system messages are AI-generated icebreakers."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        null=True,
        blank=True,
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_flagged = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["match", "id"], name="chat_match_id_idx"),
        ]

    def __str__(self):
        return self.message[:60]


class LLMLog(models.Model):
    """Audit trail for AI calls and deterministic fallbacks."""

    class TaskType(models.TextChoices):
        PARSE_POST = "parse_post", "Parse post"
        ICEBREAKER = "icebreaker", "Icebreaker"
        MODERATION = "moderation", "Moderation"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    task_type = models.CharField(max_length=30, choices=TaskType.choices)
    input_text = models.TextField()
    output_json = models.JSONField(default=dict, blank=True)
    output_text = models.TextField(blank=True)
    model = models.CharField(max_length=80, blank=True)
    strategy = models.CharField(max_length=80, default="rule_fallback")
    success = models.BooleanField(default=True)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.task_type} via {self.strategy}"
