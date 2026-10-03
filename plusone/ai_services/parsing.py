import json
import re
import time
from datetime import timedelta

from django.utils import timezone

from plusone.ai_services.client import chat_completion as default_chat_completion
from plusone.ai_services.client import llm_client as default_llm_client
from plusone.ai_services.client import request_timeout_seconds
from plusone.ai_services.logging import save_log
from plusone.ai_services.validation import extract_explicit_date, validate_draft
from plusone.models import ActivityPost, CampusLocation, LLMLog


TIME_WITH_MERIDIEM_RE = re.compile(
    r"\b(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)(?![a-z0-9])",
    re.IGNORECASE,
)
TIME_24H_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", re.IGNORECASE)
AMBIGUOUS_HOUR_RE = re.compile(r"\b(?:at|around|about)\s+(1[0-2]|0?[1-9])\b(?!\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|[:.]\d))", re.IGNORECASE)
CHINESE_CLOCK_RE = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|晚上|晚间)?\s*(?P<hour>[01]?\d|2[0-3])(?:点|時|时)"
    r"(?:(?P<half>半)|(?P<minute>[0-5]?\d)分?)?"
)
END_MARKER_RE = re.compile(
    r"\b(?:until|till|through|ends?|ending|finish(?:es)?)\b|(?:到|至|结束|結束)",
    re.IGNORECASE,
)
RANGE_JOIN_RE = re.compile(r"^\s*(?:-|–|—|to|until|till|through|到|至)\s*$", re.IGNORECASE)


def _normalise_location(value):
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def _explicit_location(text):
    """Resolve a written place only when it identifies one catalog location.

    Activity categories (studying, dinner, basketball) do not identify places.
    Short aliases are allowed only for actual catalog names, and a shared alias
    deliberately stays unresolved instead of selecting the first database row.
    """
    normalised = f" {_normalise_location(text)} "
    locations = list(CampusLocation.objects.all())
    direct = [
        location for location in locations
        if f" {_normalise_location(location.name)} " in normalised
    ]
    if direct:
        # A longer full name can contain another location's full name.
        direct = [
            location for location in direct
            if not any(
                other.pk != location.pk
                and f" {_normalise_location(location.name)} " in f" {_normalise_location(other.name)} "
                for other in direct
            )
        ]
        return direct[0] if len(direct) == 1 else None

    aliases = {
        "main library": {"library"},
        "campus sports hall": {"sports hall", "sports center", "sports centre"},
        "north dining hall": {"north dining"},
        "student center": {"student centre"},
        "campus quad": {"quad"},
    }
    candidates = []
    for location in locations:
        name = _normalise_location(location.name)
        names = set(aliases.get(name, ()))
        # Generic building words can resolve only if the entire catalog agrees.
        if "library" in name.split():
            names.add("library")
        if "sports hall" in name or "sports center" in name or "sports centre" in name:
            names.update({"sports hall", "sports center", "sports centre"})
        if any(
            f" {alias} " in normalised
            and (
                alias not in {"library", "quad"}
                or re.search(rf"\b(?:at|in|inside|near|from|by|outside)\s+(?:the\s+)?{alias}\b", normalised)
            )
            for alias in names
        ):
            candidates.append(location)
    return candidates[0] if len(candidates) == 1 else None


def _first_location_for_keywords(text):
    # Retained for callers that imported the former helper.
    return _explicit_location(text)


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
    return bool(_clock_tokens(text))


def suggest_ambiguous_time_options(text):
    if _parse_time(text) is not None:
        return []

    match = AMBIGUOUS_HOUR_RE.search(text)
    if not match:
        return []

    hour = int(match.group(1))
    now = timezone.localtime()
    explicit_date = extract_explicit_date(text, now=now)
    base = now
    if explicit_date:
        base = now.replace(year=explicit_date.year, month=explicit_date.month, day=explicit_date.day)
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


def _clock_tokens(text):
    """Clock tokens in text order, preferring AM/PM over overlapping 24h matches."""
    tokens = []
    for match in TIME_WITH_MERIDIEM_RE.finditer(text):
        hour = int(match.group(1)) % 12
        if match.group(3).lower().startswith("p"):
            hour += 12
        tokens.append({"span": match.span(), "hour": hour, "minute": int(match.group(2) or 0)})
    for match in TIME_24H_RE.finditer(text):
        if any(start <= match.start() < end for start, end in (item["span"] for item in tokens)):
            continue
        tokens.append({"span": match.span(), "hour": int(match.group(1)), "minute": int(match.group(2))})
    for match in CHINESE_CLOCK_RE.finditer(text):
        hour = int(match.group("hour"))
        period = match.group("period")
        if not period and 1 <= hour <= 12:
            # Chinese "7点" has the same AM/PM ambiguity as English "at 7".
            continue
        if period in {"下午", "晚上", "晚间"} and hour < 12:
            hour += 12
        elif period in {"凌晨", "早上", "上午"} and hour == 12:
            hour = 0
        elif period == "中午" and hour < 11:
            hour += 12
        minute = 30 if match.group("half") else int(match.group("minute") or 0)
        tokens.append({"span": match.span(), "hour": hour, "minute": minute})
    # "7–9pm" shares the explicit AM/PM marker with the first range endpoint.
    for match in re.finditer(
        r"\b(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*[-–—]\s*"
        r"(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)",
        text, re.IGNORECASE,
    ):
        if any(start <= match.start() < end for start, end in (item["span"] for item in tokens)):
            continue
        hour = int(match.group(1)) % 12 + (12 if match.group(5).lower().startswith("p") else 0)
        tokens.append({"span": (match.start(), match.end(2) if match.group(2) else match.end(1)),
                       "hour": hour, "minute": int(match.group(2) or 0)})
    for token in list(tokens):
        tail = text[token["span"][1]:]
        match = re.match(
            r"\s*(?:-|–|—|to\b|until\b|till\b|到|至)\s*"
            r"(?P<hour>1[0-2]|0?[1-9])(?:[:.](?P<minute>[0-5]\d))?(?:点|时|時)?"
            r"(?!\d|\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|[:.]\d))", tail, re.IGNORECASE,
        )
        if not match:
            continue
        span = (token["span"][1] + match.start("hour"), token["span"][1] + match.end())
        if any(start <= span[0] < end for start, end in (item["span"] for item in tokens)):
            continue
        # A range can share its explicit period; an evening-to-early-hour range
        # conventionally crosses midnight. A standalone bare hour stays blank.
        hour = int(match.group("hour")) % 12
        if token["hour"] >= 12 and not (token["hour"] >= 18 and hour <= 8 and hour < token["hour"] % 12):
            hour += 12
        tokens.append({"span": span, "hour": hour, "minute": int(match.group("minute") or 0)})
    return sorted(tokens, key=lambda item: item["span"][0])


def _is_end_clock(text, token):
    prefix = text[:token["span"][0]]
    if re.search(
        r"(?:\b(?:until|till|through|(?:ends?|ending)(?:\s+time)?(?:\s*[:：]|\s+at)?|finish(?:es)?(?:\s+at)?)"
        r"|到|至|(?:结束|結束)(?:时间|時間)?[:：]?)\s*$", prefix, re.IGNORECASE,
    ):
        return True
    suffix = text[token["span"][1]:]
    return bool(re.match(r"\s*(?:结束|結束)", suffix))


def _parse_time(text):
    tokens = [token for token in _clock_tokens(text) if not _is_end_clock(text, token)]
    if not tokens:
        return None
    token = tokens[0]
    # A bare starting hour must not be replaced by a later explicit ending hour.
    if AMBIGUOUS_HOUR_RE.search(text[:token["span"][0]]):
        return None
    now = timezone.localtime()
    explicit_date = extract_explicit_date(text, now=now)
    base = now
    if explicit_date:
        base = base.replace(year=explicit_date.year, month=explicit_date.month, day=explicit_date.day)
    parsed = base.replace(hour=token["hour"], minute=token["minute"], second=0, microsecond=0)
    if explicit_date is None and parsed < now - timedelta(minutes=15):
        parsed += timedelta(days=1)
    return parsed


def _parse_duration(text):
    match = re.search(
        r"\bfor\s+(\d+(?:\.\d+)?|one|two|three|half|an?|a\s+half)\s*"
        r"(hours?|hrs?|minutes?|mins?)\b", text, re.IGNORECASE,
    )
    if match:
        value = match.group(1).lower()
        numbers = {"one": 1, "two": 2, "three": 3, "half": .5, "a": 1, "an": 1, "a half": .5}
        amount = numbers[value] if value in numbers else float(value)
        minutes = amount * (60 if match.group(2).lower().startswith(("hour", "hr")) else 1)
        return timedelta(minutes=minutes) if 0 < minutes <= 7 * 24 * 60 else timedelta(0)
    match = re.search(r"(\d+(?:\.\d+)?|一|两|二|三|半)(?:个)?(小时|小時|分钟|分鐘)", text)
    if match:
        value = match.group(1)
        numbers = {"一": 1, "两": 2, "二": 2, "三": 3, "半": .5}
        amount = numbers[value] if value in numbers else float(value)
        minutes = amount * (60 if match.group(2) in {"小时", "小時"} else 1)
        return timedelta(minutes=minutes) if 0 < minutes <= 7 * 24 * 60 else timedelta(0)
    return None


def _parse_end_time(text, start_time):
    """Return (end, source, warning). Unclear written ends never become defaults."""
    duration = _parse_duration(text)
    tokens = _clock_tokens(text)
    end_token = next((token for token in tokens if _is_end_clock(text, token)), None)
    if end_token is None and len(tokens) > 1:
        first, second = tokens[:2]
        if RANGE_JOIN_RE.fullmatch(text[first["span"][1]:second["span"][0]]):
            end_token = second
    if end_token is not None and start_time:
        end = start_time.replace(hour=end_token["hour"], minute=end_token["minute"])
        lower = text.lower()
        overnight = bool(re.search(r"\b(?:overnight|next\s+day|next\s+morning)\b|次日|第二天|跨夜", lower))
        if end <= start_time and (overnight or start_time.hour >= 18 and end.hour <= 8):
            end += timedelta(days=1)
        if end <= start_time:
            return None, "missing", "invalid_end_time"
        return end, "user_text", None
    if duration is not None and start_time:
        if duration <= timedelta(0):
            return None, "missing", "invalid_end_time"
        return start_time + duration, "user_text", None
    if END_MARKER_RE.search(text) or end_token or duration is not None:
        return None, "missing", "ambiguous_end_time"
    if start_time:
        return start_time + timedelta(hours=1), "suggested", "suggested_end_time"
    return None, "missing", None


def _title_for(text, activity_type):
    lowered = text.lower()
    if "basketball" in lowered:
        return "Basketball game"
    if activity_type == ActivityPost.ActivityType.FOOD:
        return "Meal buddy on campus"
    if activity_type == ActivityPost.ActivityType.STUDY:
        return "Study sprint"
    if activity_type == ActivityPost.ActivityType.CLUB:
        return "Campus club activity"
    if activity_type == ActivityPost.ActivityType.EXPLORE:
        return "Campus walk"
    return text.strip().split(".")[0][:80] or "Quick campus plan"


def rule_parse_activity(text):
    # The rule parser is both an offline fallback and a guardrail around model
    # output. Keep it conservative: it should never invent user intent.
    activity_type = _activity_type(text)
    location = _first_location_for_keywords(text)
    start_time = _parse_time(text)
    end_time, end_source, end_warning = _parse_end_time(text, start_time)
    return {
        "title": _title_for(text, activity_type),
        "description": text.strip(),
        "activity_type": activity_type,
        "location_name": location.name if location else "",
        "start_time": start_time.isoformat() if start_time else "",
        "expected_end_time": end_time.isoformat() if end_time else "",
        "expire_minutes": 45,
        "field_sources": {
            "title": "suggested", "description": "user_text", "activity_type": "suggested",
            "location": "user_text" if location else "missing",
            "start_time": "user_text" if start_time else "missing",
            "expected_end_time": end_source, "expire_minutes": "suggested",
        },
        "end_time_warning": end_warning or "",
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
    if not isinstance(parsed, dict):
        raise ValueError("The activity draft must be a JSON object.")
    merged = fallback.copy()
    # The model may help word the card and classify the activity. Places and
    # clock times remain tied to the request, including deliberate blanks.
    for field in ("title", "description", "activity_type", "expire_minutes"):
        value = parsed.get(field)
        if value and (field == "expire_minutes" or isinstance(value, str)):
            merged[field] = value
            if field == "description" and value != fallback["description"]:
                merged["field_sources"] = {**fallback.get("field_sources", {}), "description": "suggested"}
    if merged["activity_type"] not in ActivityPost.ActivityType.values:
        merged["activity_type"] = fallback["activity_type"]
    return merged


def _finalize_draft(text, draft):
    """Run deterministic guardrails on a draft before it reaches the review UI.

    Order matters: validate first (so a date conflict is reported), then align
    the drafted date to the explicit date in the user's text. The user still
    reviews and can change everything before publish.
    """
    draft["validation"] = validate_draft(text, draft)
    if draft["validation"]["date_mismatch"]:
        start_time = _parse_iso_datetime(draft.get("start_time"))
        expected = timezone.datetime.fromisoformat(draft["validation"]["expected_date"]).date()
        if start_time:
            aligned = start_time.replace(year=expected.year, month=expected.month, day=expected.day)
            draft["start_time"] = aligned.isoformat()
            end_time = _parse_iso_datetime(draft.get("expected_end_time"))
            if end_time:
                draft["expected_end_time"] = (end_time + (aligned - start_time)).isoformat()
    return draft


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
                timeout=request_timeout_seconds("parsing"),
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Parse a college activity request into JSON with keys: "
                            "title, description, activity_type, location_name, start_time, expected_end_time, expire_minutes. "
                            f"Current local time is {now.isoformat()} ({timezone.get_current_timezone_name()}). "
                            "Treat relative dates like today, tomorrow, and tonight relative to that timestamp. "
                            "Never return a start_time in the past. "
                            "Only return start_time when the user gives a clear clock time with AM/PM or 24-hour notation, such as 7pm, 7am, or 19:00. "
                            "If the user gives only a date, vague period, or ambiguous hour such as tomorrow, tonight, at 7, or around 7, return an empty string for start_time. "
                            f"Use one activity_type from {[choice[0] for choice in ActivityPost.ActivityType.choices]}. "
                            f"Only select a location from {locations} when the user explicitly identifies it; "
                            "leave location_name empty if absent, unknown, or ambiguous. Do not infer a location from an activity category. "
                            "Preserve an explicit ending time or duration as expected_end_time. "
                            "Leave expected_end_time empty when no ending time is supplied. "
                            "Use ISO 8601 for start_time and expected_end_time."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            fallback = rule_parse_activity(text)
            merged = _finalize_draft(text, _merge_activity_parse(parsed, fallback))
            save_log(user, LLMLog.TaskType.PARSE_POST, text, merged, raw, model, strategy, True, started_at)
            return merged
        except Exception as exc:
            parsed = _finalize_draft(text, rule_parse_activity(text))
            save_log(user, LLMLog.TaskType.PARSE_POST, text, parsed, str(exc), model, f"{strategy}_failed_rule_fallback", False, started_at)
            return parsed

    parsed = _finalize_draft(text, rule_parse_activity(text))
    save_log(user, LLMLog.TaskType.PARSE_POST, text, parsed, json.dumps(parsed), "", "rule_fallback", True, started_at)
    return parsed
