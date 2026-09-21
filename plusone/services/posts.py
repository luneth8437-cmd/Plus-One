from plusone.ai import moderate_text
from plusone.models import ActivityPost, ProductEvent
from plusone.services.analytics import log_event
from django.db import transaction
from plusone.services.lifecycle import end_locked, locked_post, lock_users, identities_retired
from plusone.services.requests import check_replay, fingerprint, request_uuid, RequestError
from plusone.models import Match


def moderate_activity_form(user, form):
    return moderate_text(user, f"{form.cleaned_data['title']} {form.cleaned_data['description']}")


def moderate_activity_text(user, text):
    return moderate_text(user, text)


PUBLISH_FIELDS = (
    "title",
    "description",
    "activity_type",
    "location",
    "start_time",
    "expected_end_time",
    "expire_minutes",
    "raw_text",
)
LEGACY_PUBLISH_FIELDS = tuple(field for field in PUBLISH_FIELDS if field != "expected_end_time")


def publish_fingerprint(data):
    return fingerprint({key: data.get(key, "") for key in PUBLISH_FIELDS})


def legacy_publish_fingerprint(data):
    return fingerprint({key: data.get(key, "") for key in LEGACY_PUBLISH_FIELDS})


def published_replay(user, data):
    key = request_uuid(data.get("request_id"))
    record = ActivityPost.objects.filter(user=user, request_id=key).first()
    try:
        return check_replay(record, publish_fingerprint(data))
    except RequestError:
        # A request accepted before expected_end_time existed stored the old
        # fingerprint shape. Only an unchanged old page (field absent, not
        # merely blank) may reconcile against that historical digest.
        if record and "expected_end_time" not in data:
            return check_replay(record, legacy_publish_fingerprint(data))
        raise


def save_activity_post_for_user(user, form, request_id=None, request_fingerprint=None):
    if form.instance.pk:
        with locked_post(form.instance.pk, user.pk) as current:
            if identities_retired([user.pk]):
                raise RequestError("This identity has been reset. Refresh before creating or editing a card.", 409)
            if current.user_id != user.pk or current.status != ActivityPost.Status.ACTIVE or current.is_expired or current.held_spots:
                raise RequestError("This card changed while you were editing. Your changes were not saved. Review its current status before trying again.", 409)
            # Never save the stale ModelForm instance over a new match state.
            for field in ("title", "description", "activity_type", "location", "start_time", "expected_end_time"):
                setattr(current, field, form.cleaned_data[field])
            from datetime import timedelta
            from django.utils import timezone
            current.expire_time = timezone.now() + timedelta(minutes=form.cleaned_data["expire_minutes"])
            current.save(update_fields=["title", "description", "activity_type", "location", "start_time", "expected_end_time", "expire_time", "updated_at"])
            log_event(ProductEvent.Name.EDIT_CARD, user=user, post=current)
            return current
    key = request_uuid(request_id)
    if not request_fingerprint:
        raise RequestError("A request fingerprint is required.")
    with transaction.atomic():
        lock_users([user.pk])
        existing = check_replay(ActivityPost.objects.filter(user=user, request_id=key).first(), request_fingerprint)
        if existing:
            return existing
        if identities_retired([user.pk]):
            raise RequestError("This identity has been reset. Refresh before publishing.", 409)
        form.instance.request_id = key
        form.instance.request_fingerprint = request_fingerprint
        post = form.save_for_user(user)
        log_event(ProductEvent.Name.PUBLISH_CARD, user=user, post=post, properties={"activity_type": post.activity_type, "expire_minutes": form.cleaned_data["expire_minutes"]})
        return post


def cancel_activity_post(post):
    with locked_post(post.pk) as current:
        current.status = ActivityPost.Status.CANCELLED
        current.save(update_fields=["status", "updated_at"])
        for match in Match.objects.select_for_update().filter(post=current, status__in=Match.LIVE_STATUSES).order_by("pk"):
            match.post = current
            end_locked(match, reason=Match.CloseReason.CANCELLED)
        return current
