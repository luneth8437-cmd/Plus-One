import json
import re
import time
from datetime import timedelta

from django.utils import timezone

from plusone.ai_services.client import chat_completion as default_chat_completion
from plusone.ai_services.client import llm_client as default_llm_client
from plusone.ai_services.logging import save_log
from plusone.models import ActivityPost, CampusLocation, LLMLog


TIME_WITH_MERIDIEM_RE = re.compile(
    r"\b(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)(?![a-z0-9])",
    re.IGNORECASE,
)
TIME_24H_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", re.IGNORECASE)
AMBIGUOUS_HOUR_RE = re.compile(r"\b(?:at|around|about)\s+(1[0-2]|0?[1-9])\b(?!\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|[:.]\d))", re.IGNORECASE)


def _first_location_for_keywords(text):
    lowered = text.lower()
    candidates = [
        (["sports", "basketball", "game", "gym"], "Campus Sports Hall"),
        (["library", "study", "sprint"], "Main Library"),
        (["lunch", "dinner", "food", "mensa", "dining"], "North Dining Hall"),
        (["club", "fair", "booth"], "Student Center"),
        (["quad", "walk", "explore"], "Campus Quad"),
    ]
    for keywords, name in candidates:
        if any(keyword in lowered for keyword in keywords):
            location = CampusLocation.objects.filter(name__icontains=name).first()
            if location:
                return location
    return CampusLocation.objects.first()


def _activity_type(text):
    lowered = text.lower()
    if any(word in lowered for word in ["lunch", "dinner", "food", "mensa", "dining", "coffee"]):
        return ActivityPost.ActivityType.FOOD
    if any(word in lowered for word in ["basketball", "game", "sports", "gym"]):
        return ActivityPost.ActivityType.SPORTS
    if any(word in lowered for word in ["study", "library", "sprint", "homework"]):
        return ActivityPost.ActivityType.STUDY
    if any(word in lowered for word in ["club", "fair", "booth"]):
        return ActivityPost.ActivityType.CLUB
    if any(word in lowered for word in ["explore", "walk", "tour"]):
        return ActivityPost.ActivityType.EXPLORE
    return ActivityPost.ActivityType.OTHER


def _has_explicit_time(text):
    return bool(TIME_WITH_MERIDIEM_RE.search(text) or TIME_24H_RE.search(text))


def suggest_ambiguous_time_options(text):
    if _has_explicit_time(text):
        return []

    match = AMBIGUOUS_HOUR_RE.search(text)
    if not match:
        return []

    hour = int(match.group(1))
    now = timezone.localtime()
    base = now + timedelta(days=1) if "tomorrow" in text.lower() else now
    options = []
    for label, option_hour in [("Morning", hour % 12), ("Evening", (hour % 12) + 12)]:
        candidate = base.replace(hour=option_hour, minute=0, second=0, microsecond=0)
        if candidate < now - timedelta(minutes=15):
            continue
        options.append(
            {
                "label": label,
                "value": candidate.strftime("%Y-%m-%dT%H:%M"),
                "display": candidate.strftime("%b %-d, %-I:%M %p"),
            }
        )
    return options


def _parse_time(text):
    # Only clear clock times are accepted. "at 7" is intentionally ambiguous
    # and should stay empty so the user chooses AM/PM in the review form.
    if not _has_explicit_time(text):
        return None

    lowered = text.lower()
    now = timezone.localtime()
    base = now
    if "tomorrow" in lowered:
        base = now + timedelta(days=1)

    hour = minute = None
    match = TIME_WITH_MERIDIEM_RE.search(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower()
        hour = hour % 12
        if meridiem.startswith("p"):
            hour += 12
    else:
        match = TIME_24H_RE.search(text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))

    if hour is None:
        return None

    parsed = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if parsed < now - timedelta(minutes=15):
        parsed += timedelta(days=1)
    return parsed


def _title_for(text, activity_type):
    lowered = text.lower()
    if "basketball" in lowered:
        return "Basketball game tonight"
    if activity_type == ActivityPost.ActivityType.FOOD:
        return "Meal buddy on campus"
    if activity_type == ActivityPost.ActivityType.STUDY:
        return "Study sprint"
    if activity_type == ActivityPost.ActivityType.CLUB:
        return "Explore the club fair"
    if activity_type == ActivityPost.ActivityType.EXPLORE:
        return "Campus walk"
    return text.strip().split(".")[0][:80] or "Quick campus plan"


def rule_parse_activity(text):
    # The rule parser is both an offline fallback and a guardrail around model
    # output. Keep it conservative: it should never invent user intent.
    activity_type = _activity_type(text)
    location = _first_location_for_keywords(text)
    start_time = _parse_time(text)
    return {
        "title": _title_for(text, activity_type),
        "description": text.strip(),
        "activity_type": activity_type,
        "location_name": location.name if location else "",
        "start_time": start_time.isoformat() if start_time else "",
        "expire_minutes": 45,
    }


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return timezone.localtime(parsed)


def _merge_activity_parse(parsed, fallback):
    merged = {**fallback, **{k: v for k, v in parsed.items() if v}}
    if not fallback.get("start_time"):
        # If the user did not provide a precise time, discard any model guess.
        merged["start_time"] = ""
        return merged

    fallback_start_time = _parse_iso_datetime(fallback.get("start_time"))
    if fallback_start_time:
        # User-written explicit time wins over the model, even when the model
        # returns a plausible but different future timestamp.
        merged["start_time"] = fallback_start_time.isoformat()
        return merged

    merged_start_time = _parse_iso_datetime(merged.get("start_time"))
    now = timezone.localtime()

    if merged_start_time is None or merged_start_time < now - timedelta(minutes=15):
        if fallback_start_time:
            merged["start_time"] = fallback_start_time.isoformat()

    return merged


def parse_activity_text(user, text, llm_client=default_llm_client, chat_completion=default_chat_completion):
    started_at = time.perf_counter()
    llm = llm_client()
    now = timezone.localtime()
    if llm:
        client, llm_config = llm
        model = llm_config["model"]
        strategy = llm_config["strategy"]
        locations = list(CampusLocation.objects.values_list("name", flat=True))
        try:
            response = chat_completion(
                client,
                llm_config,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Parse a college activity request into JSON with keys: "
                            "title, description, activity_type, location_name, start_time, expire_minutes. "
                            f"Current local time is {now.isoformat()} ({timezone.get_current_timezone_name()}). "
                            "Treat relative dates like today, tomorrow, and tonight relative to that timestamp. "
                            "Never return a start_time in the past. "
                            "Only return start_time when the user gives a clear clock time with AM/PM or 24-hour notation, such as 7pm, 7am, or 19:00. "
                            "If the user gives only a date, vague period, or ambiguous hour such as tomorrow, tonight, at 7, or around 7, return an empty string for start_time. "
                            f"Use one activity_type from {[choice[0] for choice in ActivityPost.ActivityType.choices]}. "
                            f"Prefer one location from {locations}. Use ISO 8601 for start_time."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            fallback = rule_parse_activity(text)
            merged = _merge_activity_parse(parsed, fallback)
            save_log(user, LLMLog.TaskType.PARSE_POST, text, merged, raw, model, strategy, True, started_at)
            return merged
        except Exception as exc:
            parsed = rule_parse_activity(text)
            save_log(user, LLMLog.TaskType.PARSE_POST, text, parsed, str(exc), model, f"{strategy}_failed_rule_fallback", False, started_at)
            return parsed

    parsed = rule_parse_activity(text)
    save_log(user, LLMLog.TaskType.PARSE_POST, text, parsed, json.dumps(parsed), "", "rule_fallback", True, started_at)
    return parsed
