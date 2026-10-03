from datetime import timedelta

from django.contrib import admin
from django.utils import timezone

from .models import (
    ActivityPost,
    ActivityReport,
    CampusLocation,
    ChatMessage,
    LLMLog,
    Match,
    SafetyReport,
    Swipe,
    UserBlock,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "major", "year", "campus_area")
    search_fields = ("display_name", "user__username", "major", "campus_area")


@admin.register(CampusLocation)
class CampusLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "location_type", "area")
    list_filter = ("location_type", "area")
    search_fields = ("name", "area")


@admin.register(ActivityPost)
class ActivityPostAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "activity_type",
        "location",
        "start_time",
        "expected_end_time",
        "expire_time",
        "status",
    )
    list_filter = ("activity_type", "status", "location")
    search_fields = ("title", "description", "user__username")


@admin.register(Swipe)
class SwipeAdmin(admin.ModelAdmin):
    list_display = ("user", "post", "action", "created_at")
    list_filter = ("action",)


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("post", "poster", "swiper", "status", "created_at", "chat_expires_at")
    list_filter = ("status",)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("match", "sender", "is_system", "is_flagged", "created_at")
    list_filter = ("is_flagged", "is_system")
    search_fields = ("message",)


@admin.register(LLMLog)
class LLMLogAdmin(admin.ModelAdmin):
    list_display = ("task_type", "strategy", "model", "success", "latency_ms", "created_at")
    list_filter = ("task_type", "strategy", "success")
    search_fields = ("input_text", "output_text")


class OverdueSafetyReportFilter(admin.SimpleListFilter):
    title = "overdue (more than 7 days)"
    parameter_name = "overdue"

    def lookups(self, request, model_admin):
        return (("yes", "Overdue"), ("no", "Not overdue"))

    def queryset(self, request, queryset):
        cutoff = timezone.now() - timedelta(days=7)
        overdue = queryset.exclude(status=SafetyReport.Status.RESOLVED).filter(created_at__lt=cutoff)
        if self.value() == "yes":
            return overdue
        if self.value() == "no":
            return queryset.exclude(pk__in=overdue.values("pk"))
        return queryset


@admin.register(SafetyReport)
class SafetyReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "category",
        "status",
        "reporter_evidence",
        "participant_evidence",
        "created_at",
        "is_overdue",
    )
    list_filter = ("status", "category", OverdueSafetyReportFilter)
    search_fields = (
        "reason",
        "handling_notes",
        "reporter__username",
        "match__poster__username",
        "match__swiper__username",
    )
    fields = (
        "status",
        "handling_notes",
        "match_evidence",
        "reporter_evidence",
        "participant_evidence",
        "category",
        "reason",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "match_evidence",
        "reporter_evidence",
        "participant_evidence",
        "category",
        "reason",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "match__post",
            "match__poster",
            "match__swiper",
            "reporter",
        )

    @admin.display(description="Match evidence")
    def match_evidence(self, report):
        return f"Match {report.match_id}: {report.match.post.title}"

    @admin.display(description="Reporter evidence")
    def reporter_evidence(self, report):
        return f"User {report.reporter_id}: {report.reporter.username}"

    @admin.display(description="Participants")
    def participant_evidence(self, report):
        match = report.match
        return (
            f"Poster {match.poster_id}: {match.poster.username}; "
            f"Swiper {match.swiper_id}: {match.swiper.username}"
        )

    @admin.display(boolean=True, description="Overdue")
    def is_overdue(self, report):
        return report.overdue

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ActivityReport)
class ActivityReportAdmin(admin.ModelAdmin):
    list_display = ("id", "category", "status", "post_evidence", "reporter_evidence", "created_at", "is_overdue")
    list_filter = ("status", "category", OverdueSafetyReportFilter)
    search_fields = ("reason", "handling_notes", "post__title", "post__user__username", "reporter__username")
    fields = ("status", "handling_notes", "post_evidence", "reporter_evidence", "title_snapshot", "description_snapshot", "location_snapshot", "start_time_snapshot", "expected_end_time_snapshot", "category", "reason", "created_at", "updated_at")
    readonly_fields = ("post_evidence", "reporter_evidence", "title_snapshot", "description_snapshot", "location_snapshot", "start_time_snapshot", "expected_end_time_snapshot", "category", "reason", "created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("post__user", "reporter")

    @admin.display(description="Card evidence")
    def post_evidence(self, report):
        title = report.title_snapshot or report.post.title
        return f"Card {report.post_id}: {title}; publisher {report.post.user_id}: {report.post.user.username}"

    @admin.display(description="Reporter evidence")
    def reporter_evidence(self, report):
        return f"User {report.reporter_id}: {report.reporter.username}"

    @admin.display(boolean=True, description="Overdue")
    def is_overdue(self, report):
        return report.status != ActivityReport.Status.RESOLVED and report.created_at < timezone.now() - timedelta(days=7)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ("blocker", "target", "created_at")
    search_fields = ("blocker__username", "target__username")
    readonly_fields = ("blocker", "target", "created_at")

    def has_add_permission(self, request):
        return False
