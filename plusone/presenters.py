from django.utils import timezone

from .models import ActivityPost, CampusLocation
from .utils import positive_int


def post_initial_from_ai(parsed):
    # Translate AI JSON into Django form initial data. Invalid or missing
    # start_time stays blank so the browser review form asks the user to decide.
    location = None
    if parsed.get("location_name"):
        location = CampusLocation.objects.filter(name__iexact=parsed["location_name"]).first()
        if not location:
            location = CampusLocation.objects.filter(name__icontains=parsed["location_name"]).first()
    if not location:
        location = CampusLocation.objects.first()

    start_time = None
    if parsed.get("start_time"):
        try:
            start_time = timezone.datetime.fromisoformat(parsed["start_time"])
            if timezone.is_naive(start_time):
                start_time = timezone.make_aware(start_time, timezone.get_current_timezone())
        except ValueError:
            start_time = None

    return {
        "title": parsed.get("title", ""),
        "description": parsed.get("description", ""),
        "activity_type": parsed.get("activity_type", ActivityPost.ActivityType.OTHER),
        "location": location.id if location else None,
        "start_time": timezone.localtime(start_time).strftime("%Y-%m-%dT%H:%M") if start_time else "",
        "expire_minutes": parsed.get("expire_minutes", 45),
    }


def post_form_preview(form):
    # The preview accepts both bound POST data and initial data from the AI
    # assist flow, so templates can render one preview path for both states.
    source = form.data if form.is_bound else form.initial
    location = None
    location_id = positive_int(source.get("location"))
    if location_id:
        location = CampusLocation.objects.filter(id=location_id).first()
    selected_activity_type = source.get("activity_type")
    activity_type = selected_activity_type or ActivityPost.ActivityType.OTHER
    activity_label = (
        dict(ActivityPost.ActivityType.choices).get(activity_type, "Activity")
        if selected_activity_type
        else "Activity"
    )
    return {
        "title": source.get("title") or "Your Plus One title",
        "description": source.get("description") or "The card preview updates as you edit the structured fields.",
        "activity_type": activity_type,
        "activity_label": activity_label,
        "location": location.name if location else "Campus location",
        "start_time": preview_start_time(source.get("start_time")),
        "expire_minutes": source.get("expire_minutes") or "45",
    }


def preview_start_time(value):
    if not value:
        return "Start time"
    if isinstance(value, str):
        try:
            start_time = timezone.datetime.fromisoformat(value)
        except ValueError:
            return "Start time"
    else:
        start_time = value
    if timezone.is_naive(start_time):
        start_time = timezone.make_aware(start_time, timezone.get_current_timezone())
    start_time = timezone.localtime(start_time)
    return f"{start_time:%b} {start_time.day}, {start_time:%H:%M}"


def post_edit_initial(post):
    remaining_minutes = int(max(5, round((post.expire_time - timezone.now()).total_seconds() / 60)))
    return {
        "title": post.title,
        "description": post.description,
        "activity_type": post.activity_type,
        "location": post.location_id,
        "start_time": timezone.localtime(post.start_time).strftime("%Y-%m-%dT%H:%M"),
        "expire_minutes": remaining_minutes,
    }


def chat_message_payload(message, viewer):
    if message.is_system:
        sender_label = "Icebreaker"
        bubble_class = "system"
    elif message.sender_id == viewer.id:
        sender_label = "You"
        bubble_class = "mine"
    else:
        sender_label = "Anonymous match"
        bubble_class = "theirs"
    return {
        "id": message.id,
        "sender_label": sender_label,
        "bubble_class": bubble_class,
        "message": message.message,
        "created_at": timezone.localtime(message.created_at).strftime("%H:%M"),
        "is_flagged": message.is_flagged,
        "is_system": message.is_system,
    }
