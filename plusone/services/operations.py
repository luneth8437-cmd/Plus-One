"""Read-only operational facts; no credentials, chat text, or remote probes."""
import os
import re
import subprocess
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q
from django.utils import timezone

from plusone.models import ActivityPost, ActivityReport, BrowserBudgetBucket, LLMLog, Match, PresenceLease, ProductEvent, PushDelivery, RateLimitBucket, SafetyReport
from plusone.services.cleanup import _inactive_push_subscriptions, stale_anonymous_users


def expired_match_filter(now):
    return (Q(status=Match.Status.CHATTING, chat_expires_at__lte=now)
            | Q(status=Match.Status.WAITING, waiting_expires_at__lte=now)
            | Q(status=Match.Status.WAITING, post__expire_time__lte=now)
            | Q(status=Match.Status.WAITING, post__expected_end_time__lte=now)
            | Q(status=Match.Status.WAITING, post__expected_end_time__isnull=True,
                post__start_time__lte=now - timedelta(hours=1)))


def operational_backlog(now=None):
    now = now or timezone.now()
    return {
        "expired_active_posts": ActivityPost.objects.filter(status=ActivityPost.Status.ACTIVE).filter(
            Q(expire_time__lte=now) | Q(expected_end_time__lte=now)
            | Q(expected_end_time__isnull=True, start_time__lte=now - timedelta(hours=1))).count(),
        "expired_live_matches": Match.objects.filter(expired_match_filter(now)).count(),
        "stale_identity_candidates": stale_anonymous_users(now=now).count(),
        "llm_logs_older_than_30_days": LLMLog.objects.filter(created_at__lt=now - timedelta(days=30)).count(),
        "events_older_than_90_days": ProductEvent.objects.filter(created_at__lt=now - timedelta(days=90)).count(),
        "reports_older_than_90_days": SafetyReport.objects.filter(created_at__lt=now - timedelta(days=90)).count(),
        "activity_reports_older_than_90_days": ActivityReport.objects.filter(created_at__lt=now - timedelta(days=90)).count(),
        "expired_sessions": Session.objects.filter(expire_date__lte=now).count(),
        "expired_rate_limit_buckets": RateLimitBucket.objects.filter(expires_at__lte=now).count(),
        "expired_browser_budget_buckets": BrowserBudgetBucket.objects.filter(expires_at__lte=now).count(),
        "expired_terminal_presence_leases": PresenceLease.objects.filter(expires_at__lte=now).exclude(match__status__in=Match.LIVE_STATUSES).count(),
        "expired_live_presence_tombstones_protected": PresenceLease.objects.filter(expires_at__lte=now, match__status__in=Match.LIVE_STATUSES).count(),
        "inactive_push_subscriptions_eligible": _inactive_push_subscriptions(now).count(),
        "push_deliveries_older_than_90_days": PushDelivery.objects.filter(created_at__lt=now - timedelta(days=90)).count(),
        "unresolved_reports_older_than_7_days": SafetyReport.objects.filter(
            status__in=[SafetyReport.Status.PENDING, SafetyReport.Status.IN_PROGRESS],
            created_at__lt=now - timedelta(days=7)).count(),
        "unresolved_activity_reports_older_than_7_days": ActivityReport.objects.filter(
            status__in=[ActivityReport.Status.PENDING, ActivityReport.Status.IN_PROGRESS],
            created_at__lt=now - timedelta(days=7)).count(),
    }


def last_maintenance_run():
    event = ProductEvent.objects.filter(name=ProductEvent.Name.MAINTENANCE_COMPLETED).first()
    if not event:
        return {"status": "unknown", "completed_at": None,
                "note": "No retained successful maintenance record; this does not prove external scheduling is absent."}
    age_seconds = max(0, int((timezone.now() - event.created_at).total_seconds()))
    return {"status": "recorded", "completed_at": event.created_at.isoformat(),
            "age_seconds": age_seconds, "older_than_24_hours": age_seconds > 24 * 60 * 60,
            "cleanup_counts": event.properties.get("cleanup_counts", {}),
            "expired_counts": event.properties.get("expired_counts", {})}


def _commit_identity():
    deployed = os.environ.get("RENDER_GIT_COMMIT", "")
    if re.fullmatch(r"[0-9a-fA-F]{40}", deployed):
        return deployed.lower(), "RENDER_GIT_COMMIT"
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=settings.BASE_DIR,
                                text=True, capture_output=True, timeout=2, check=False)
        commit = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            return commit.lower(), "local checkout; not proof of hosted deployment"
    except (OSError, subprocess.SubprocessError):
        pass
    return None, "unknown"


def production_audit(expected_commit=None):
    from plusone.services.push_notifications import push_status
    commit, source = _commit_identity()
    facts = {
        "checked_at": timezone.now().isoformat(), "commit_sha": commit, "commit_source": source,
        "expected_commit_matches": commit == expected_commit.lower() if expected_commit and commit else None,
        "debug": settings.DEBUG, "moderation_mode": settings.PLUSONE_MODERATION_MODE,
        "external_credentials_present": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()
                                             or os.environ.get("OPENAI_API_KEY", "").strip()),
        "new_matches_enabled": settings.PLUSONE_NEW_MATCHES_ENABLED,
        "web_push_enabled": getattr(settings, "PLUSONE_WEB_PUSH_ENABLED", False),
        "web_push_configuration_ready": bool(push_status().get("available")),
        "web_push_worker_schedule_verified": False,
        "database_vendor": connection.vendor, "database_ready": False, "pending_migrations": None,
        "maintenance": {"status": "unknown"}, "backlog": None,
        "unverified": ["actual hosted resource plan and database expiry", "backup restore drill",
                       "external moderation connectivity and quality", "real two-person mobile experience",
                       "external scheduler and alert routing"],
    }
    issues = []
    if facts["debug"]:
        issues.append("debug_enabled")
    if facts["moderation_mode"] != "external":
        issues.append("moderation_not_external")
    if not facts["external_credentials_present"]:
        issues.append("external_credentials_absent")
    if not facts["new_matches_enabled"]:
        issues.append("new_matching_disabled")
    if expected_commit and facts["expected_commit_matches"] is not True:
        issues.append("commit_not_verified")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        facts["database_ready"] = True
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        facts["pending_migrations"] = [f"{migration.app_label}.{migration.name}" for migration, _ in pending]
        if pending:
            issues.append("pending_migrations")
        else:
            facts["maintenance"] = last_maintenance_run()
            facts["backlog"] = operational_backlog()
    except DatabaseError:
        issues.append("database_or_schema_unavailable")
    facts["issues"] = issues
    facts["configuration_checks_passed"] = not issues
    facts["production_verified"] = False
    return facts
