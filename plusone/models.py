from datetime import timedelta

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
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name or self.user.username

    @property
    def initial(self):
        if self.avatar_initial:
            return self.avatar_initial[:2].upper()
        source = self.display_name or self.user.username
        return source[:1].upper()


class UserBlock(models.Model):
    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_created")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_received")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["blocker", "target"], name="unique_user_block")]


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
        now = timezone.now()
        return (
            self.filter(status=ActivityPost.Status.ACTIVE, expire_time__gt=now)
            .filter(Q(expected_end_time__gt=now) | Q(expected_end_time__isnull=True, start_time__gt=now - timedelta(hours=1)))
            .annotate(holding_matches=Count("matches", filter=Q(matches__status__in=Match.HOLDING_STATUSES, matches__meetup_cancelled_at__isnull=True)))
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
        PAUSED = "paused", "Recruiting paused"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activity_posts")
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True, max_length=2000)
    activity_type = models.CharField(max_length=20, choices=ActivityType.choices)
    location = models.ForeignKey(CampusLocation, on_delete=models.PROTECT, related_name="activity_posts")
    start_time = models.DateTimeField()
    expected_end_time = models.DateTimeField(null=True, blank=True, db_index=True)
    expire_time = models.DateTimeField()
    capacity = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    request_id = models.UUIDField(null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True)

    objects = ActivityPostQuerySet.as_manager()

    class Meta:
        ordering = ["start_time", "expire_time"]
        constraints = [
            models.UniqueConstraint(fields=["user", "request_id"], name="unique_post_request"),
        ]
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

    @property
    def activity_window_end(self):
        return self.expected_end_time or self.start_time + timedelta(hours=1)

    @property
    def matching_deadline(self):
        return min(self.expire_time, self.activity_window_end)

    def mark_expired_if_needed(self, save=True):
        if self.expire_time <= timezone.now() and self.status == self.Status.ACTIVE:
            self.status = self.Status.EXPIRED
            if save:
                self.save(update_fields=["status", "updated_at"])
        return self.status == self.Status.EXPIRED

    @property
    def held_spots(self):
        return self.matches.filter(status__in=Match.HOLDING_STATUSES, meetup_cancelled_at__isnull=True).count()

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
        WAITING = "waiting", "Waiting for both participants"
        CHATTING = "chatting", "Chatting"
        AGREED = "agreed", "Agreed to meet"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    class CloseReason(models.TextChoices):
        DECLINED = "declined", "Declined by participant"
        REPORTED = "reported", "Reported safety issue"
        CANCELLED = "cancelled", "Activity cancelled"
        RESET = "reset", "Identity reset"
        BLOCKED = "blocked", "Blocked by participant"
        TIMEOUT = "timeout", "Timed out"

    LIVE_STATUSES = (Status.WAITING, Status.CHATTING)
    HOLDING_STATUSES = (*LIVE_STATUSES, Status.AGREED)

    class ArrivalStatus(models.TextChoices):
        PENDING = "pending", "Not reported"
        ARRIVED = "arrived", "Arrived (self-reported)"
        DELAYED = "delayed", "Running late (self-reported)"

    class CoordinationSignal(models.TextChoices):
        AT_POINT = "at_point", "At the agreed meeting point"
        AT_ENTRANCE = "at_entrance", "At the entrance of the agreed place"
        CANT_FIND = "cant_find", "At the agreed point but cannot find you"

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
    chat_expires_at = models.DateTimeField(null=True, blank=True)
    waiting_expires_at = models.DateTimeField(null=True, blank=True)
    retry_of = models.OneToOneField("self", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="waiting_retry")
    poster_last_present_at = models.DateTimeField(null=True, blank=True)
    swiper_last_present_at = models.DateTimeField(null=True, blank=True)
    chat_started_at = models.DateTimeField(null=True, blank=True)
    # This arrangement belongs to the pair, independently of the public card.
    meeting_point = models.CharField(max_length=160, blank=True)
    plan_meeting_at = models.DateTimeField(null=True, blank=True)
    plan_expected_end_at = models.DateTimeField(null=True, blank=True, db_index=True)
    plan_revision = models.PositiveIntegerField(default=1)
    poster_agreed_revision = models.PositiveIntegerField(null=True, blank=True)
    swiper_agreed_revision = models.PositiveIntegerField(null=True, blank=True)
    plan_confirmed_at = models.DateTimeField(null=True, blank=True)
    plan_legacy = models.BooleanField(default=False)
    meetup_cancelled_at = models.DateTimeField(null=True, blank=True)
    meetup_cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="cancelled_meetups")
    poster_arrival_status = models.CharField(max_length=12, choices=ArrivalStatus.choices, default=ArrivalStatus.PENDING)
    swiper_arrival_status = models.CharField(max_length=12, choices=ArrivalStatus.choices, default=ArrivalStatus.PENDING)
    poster_delay_minutes = models.PositiveSmallIntegerField(default=0)
    swiper_delay_minutes = models.PositiveSmallIntegerField(default=0)
    poster_arrival_eta = models.DateTimeField(null=True, blank=True)
    swiper_arrival_eta = models.DateTimeField(null=True, blank=True)
    poster_arrival_updated_at = models.DateTimeField(null=True, blank=True)
    swiper_arrival_updated_at = models.DateTimeField(null=True, blank=True)
    poster_coordination_signal = models.CharField(max_length=20, choices=CoordinationSignal.choices, blank=True, default="")
    swiper_coordination_signal = models.CharField(max_length=20, choices=CoordinationSignal.choices, blank=True, default="")
    poster_meetup_outcome = models.CharField(max_length=12, blank=True)
    swiper_meetup_outcome = models.CharField(max_length=12, blank=True)
    poster_outcome_reason = models.CharField(max_length=20, blank=True)
    swiper_outcome_reason = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "swiper"], condition=Q(status__in=["waiting", "chatting"]),
                                    name="unique_live_match_post_swiper"),
        ]
        indexes = [
            models.Index(fields=["status", "chat_expires_at"], name="match_status_exp_idx"),
            models.Index(fields=["status", "waiting_expires_at"], name="match_wait_exp_idx"),
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
        return bool(self.chat_expires_at and self.chat_expires_at <= timezone.now())

    @property
    def phase_deadline(self):
        if self.status == self.Status.WAITING:
            return min(self.waiting_expires_at, self.post.matching_deadline) if self.waiting_expires_at else self.post.matching_deadline
        return self.chat_expires_at

    def mark_chat_expired_if_needed(self, save=True):
        if save:
            from plusone.services.lifecycle import refresh_match
            refreshed = refresh_match(self.pk)
            self.status = refreshed.status
            self.closed_at = refreshed.closed_at
        elif self.phase_deadline and self.phase_deadline <= timezone.now() and self.status in self.LIVE_STATUSES:
            self.status = self.Status.EXPIRED
        return self.status == self.Status.EXPIRED

    def mark_agreed(self, user):
        from plusone.services.chat import record_agreement
        record_agreement(self.pk, user)
        self.refresh_from_db()


class MeetupAction(models.Model):
    """Durable acknowledgement for an individual plan/logistics action."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="meetup_actions")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    request_id = models.UUIDField()
    request_fingerprint = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["match", "actor", "request_id"], name="unique_meetup_action_request")]


class PresenceLease(models.Model):
    """An ordered foreground signal from one tab, with a short expiry."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="presence_leases")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="presence_leases")
    tab_id = models.UUIDField()
    sequence = models.PositiveBigIntegerField(default=0)
    visible = models.BooleanField(default=False)
    last_visible_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["match", "user", "tab_id"], name="unique_presence_tab")]


class ChatMessage(models.Model):
    """Message inside a match, including deterministic lifecycle notices."""

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
    request_id = models.UUIDField(null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["match", "sender", "request_id"], name="unique_message_request"),
        ]
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
        OPENING_ASSISTANT = "opening_assistant", "Opening assistant"

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


class ProductEvent(models.Model):
    """Server-side product analytics events (docs/product_validation/04).

    Privacy rules (see the analytics event plan): no chat text, no names, no
    contact details. ``properties`` may contain AI-generated suggestion texts
    (needed to measure opener adoption) but never user-written messages.
    """

    class Name(models.TextChoices):
        PUBLISH_CARD = "publish_card", "Card published"
        EDIT_CARD = "edit_card", "Card edited"
        MATCH_CREATED = "match_created", "Match created"
        CHAT_STARTED = "chat_started", "Chat started"
        BOTH_AGREED = "both_agreed", "Both agreed"
        OPENER_SUGGESTED = "opener_suggested", "Openers suggested"
        OPENER_CLICKED = "opener_clicked", "Opener suggestion clicked"
        FIRST_MESSAGE_SENT = "first_message_sent", "First message sent"
        MESSAGE_SENT = "message_sent", "Message sent"
        FIRST_REPLY_RECEIVED = "first_reply_received", "First reply received"
        AGREE_CLICKED = "agree_clicked", "Agree clicked"
        MEETUP_CONFIRMED = "meetup_confirmed", "Meetup outcome confirmed"
        PLAN_UPDATED = "plan_updated", "Meetup plan updated"
        MEETUP_STATUS_UPDATED = "meetup_status_updated", "Meetup status self-reported"
        MEETUP_CANCELLED = "meetup_cancelled", "Meetup cancelled"
        MEETUP_OUTCOME = "meetup_outcome", "Meetup outcome self-reported"
        DISCOVER_VISITED = "discover_visited", "Discover visited"
        DISCOVER_EMPTY = "discover_empty", "Discover empty"
        CARD_IMPRESSION = "card_impression", "Card shown"
        CREATE_STARTED = "create_started", "Create started"
        INTERESTED_RESULT = "interested_result", "Interest result"
        WAIT_CLOSED = "wait_closed", "Waiting closed"
        MAINTENANCE_COMPLETED = "maintenance_completed", "Maintenance completed"

    name = models.CharField(max_length=40, choices=Name.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    post = models.ForeignKey("ActivityPost", on_delete=models.SET_NULL, null=True, blank=True)
    match = models.ForeignKey("Match", on_delete=models.SET_NULL, null=True, blank=True)
    properties = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    post_reference = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    match_reference = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    post_created_at = models.DateTimeField(null=True, blank=True)
    match_created_at = models.DateTimeField(null=True, blank=True)
    event_key = models.CharField(max_length=100, null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["name", "created_at"], name="event_name_created_idx"),
            models.Index(fields=["match", "name"], name="event_match_name_idx"),
        ]

    def __str__(self):
        return f"{self.name} @ {self.created_at:%Y-%m-%d %H:%M}"


class SafetyReport(models.Model):
    class Category(models.TextChoices):
        OTHER = "other", "Other safety concern"
        CONTACT = "contact", "Unwanted contact details"
        HARASSMENT = "harassment", "Harassment or threats"
        UNSAFE_MEETING = "unsafe_meeting", "Unsafe meeting"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="safety_reports")
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER)
    reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    handling_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["match", "reporter"], name="unique_participant_report")]
        indexes = [models.Index(fields=["status", "created_at"], name="report_status_created_idx")]

    @property
    def overdue(self):
        from datetime import timedelta
        return self.status != self.Status.RESOLVED and self.created_at < timezone.now() - timedelta(days=7)


class ActivityReport(models.Model):
    Category = SafetyReport.Category
    Status = SafetyReport.Status

    post = models.ForeignKey(ActivityPost, on_delete=models.CASCADE, related_name="activity_reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activity_reports_created")
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER)
    reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    handling_notes = models.TextField(blank=True)
    title_snapshot = models.CharField(max_length=120, blank=True)
    description_snapshot = models.TextField(max_length=2000, blank=True)
    location_snapshot = models.CharField(max_length=200, blank=True)
    start_time_snapshot = models.DateTimeField(null=True, blank=True)
    expected_end_time_snapshot = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["post", "reporter"], name="unique_activity_report")]
        indexes = [models.Index(fields=["status", "created_at"], name="activity_report_status_idx")]


class RateLimitBucket(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scope = models.CharField(max_length=30)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
    request_keys = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "scope", "window_start"], name="unique_rate_limit_window")]


class BrowserBudgetBucket(models.Model):
    browser_key = models.CharField(max_length=64)
    scope = models.CharField(max_length=24)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
    request_keys = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["browser_key", "scope", "window_start"], name="unique_browser_budget_window")]


class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=2048)
    endpoint_digest = models.CharField(max_length=64, unique=True)
    p256dh = models.CharField(max_length=256)
    auth = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)


class PushDelivery(models.Model):
    subscription = models.ForeignKey(PushSubscription, on_delete=models.CASCADE, related_name="push_deliveries")
    notification_key = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=32, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["subscription", "notification_key"], name="unique_push_delivery")]
