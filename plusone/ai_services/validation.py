"""Defensive validation for AI-drafted activity cards.

This module implements the guardrails required by
docs/product_validation/05_ai_evaluation_results.md:

1. Highlight missing required fields immediately after AI draft.
2. Block publish when start_time is missing or invalid.
3. Compare parsed date/time with the source text when an explicit date is present.
4. Show a date-confirmation warning when the date appears inconsistent.
5. Cap expire_minutes to the product range and explain the correction inline.

All checks are deterministic so they keep working when the LLM call fails,
and so they can run in CI without network access.
"""

import re
from datetime import date, timedelta

from django.utils import timezone


EXPIRE_MIN_MINUTES = 5
EXPIRE_MAX_MINUTES = 180
EXPIRE_DEFAULT_MINUTES = 45

MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_MONTH_ALTERNATION = "|".join(sorted(MONTHS, key=len, reverse=True))

# "July 16", "July 16th", "16 July"
MONTH_DAY_RE = re.compile(
    rf"\b(?:(?P<month_a>{_MONTH_ALTERNATION})\.?\s+(?P<day_a>[0-3]?\d)(?:st|nd|rd|th)?"
    rf"|(?P<day_b>[0-3]?\d)(?:st|nd|rd|th)?\s+(?P<month_b>{_MONTH_ALTERNATION})\.?)\b",
    re.IGNORECASE,
)
# "2026-07-16", "2026/07/16"
ISO_DATE_RE = re.compile(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b")
# "7/16" or "16.7." (day.month, common in Europe)
SLASH_DATE_RE = re.compile(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])\b")
DOT_DATE_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.(?!\d)")
WEEKDAY_RE = re.compile(rf"\b({'|'.join(WEEKDAYS)})\b", re.IGNORECASE)


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_explicit_date(text, now=None):
    """Return the date the user explicitly wrote in ``text``, or None.

    Handles month-name dates, ISO dates, numeric dates, today/tonight/tomorrow,
    and weekday names. Years are inferred as the next occurrence when omitted.
    """
    now = now or timezone.localtime()
    today = now.date()
    lowered = text.lower()

    match = ISO_DATE_RE.search(text)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = MONTH_DAY_RE.search(text)
    if match:
        month_name = (match.group("month_a") or match.group("month_b")).lower().rstrip(".")
        day = int(match.group("day_a") or match.group("day_b"))
        month = MONTHS.get(month_name)
        if month:
            candidate = _safe_date(today.year, month, day)
            if candidate and candidate < today - timedelta(days=1):
                candidate = _safe_date(today.year + 1, month, day)
            return candidate

    match = DOT_DATE_RE.search(text)
    if match:
        candidate = _safe_date(today.year, int(match.group(2)), int(match.group(1)))
        if candidate and candidate < today - timedelta(days=1):
            candidate = _safe_date(today.year + 1, int(match.group(2)), int(match.group(1)))
        return candidate

    match = SLASH_DATE_RE.search(text)
    if match:
        candidate = _safe_date(today.year, int(match.group(1)), int(match.group(2)))
        if candidate and candidate < today - timedelta(days=1):
            candidate = _safe_date(today.year + 1, int(match.group(1)), int(match.group(2)))
        return candidate

    if "tomorrow" in lowered:
        return today + timedelta(days=1)
    if "today" in lowered or "tonight" in lowered:
        return today

    match = WEEKDAY_RE.search(lowered)
    if match:
        target = WEEKDAYS[match.group(1)]
        offset = (target - today.weekday()) % 7
        if offset == 0 and ("next" in lowered or now.hour >= 22):
            offset = 7
        return today + timedelta(days=offset)

    return None


def clamp_expire_minutes(value):
    """Return (clamped_value, was_corrected, original_value)."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return EXPIRE_DEFAULT_MINUTES, value not in ("", None), value
    if minutes < EXPIRE_MIN_MINUTES:
        return EXPIRE_MIN_MINUTES, True, minutes
    if minutes > EXPIRE_MAX_MINUTES:
        return EXPIRE_MAX_MINUTES, True, minutes
    return minutes, False, minutes


def _parse_iso_local(value):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return timezone.localtime(parsed)


def validate_draft(raw_text, parsed, now=None):
    """Cross-check an AI draft against the user's original text.

    Returns a dict with:
      - missing_fields: required fields the draft left blank
      - warnings: list of {code, message} for the review UI
      - corrections: list of {field, from, to, message}
      - expected_date: ISO date string when the text contains an explicit date
      - date_mismatch: True when the drafted date conflicts with the text
    Mutates ``parsed`` only to clamp expire_minutes (correction is reported).
    """
    now = now or timezone.localtime()
    warnings = []
    corrections = []
    missing_fields = []

    clamped, corrected, original = clamp_expire_minutes(parsed.get("expire_minutes"))
    if corrected:
        parsed["expire_minutes"] = clamped
        corrections.append(
            {
                "field": "expire_minutes",
                "from": original,
                "to": clamped,
                "message": (
                    f"Expiry was adjusted from {original} to {clamped} minutes "
                    f"(allowed range {EXPIRE_MIN_MINUTES}-{EXPIRE_MAX_MINUTES})."
                ),
            }
        )

    start_time = _parse_iso_local(parsed.get("start_time"))
    if start_time is None:
        missing_fields.append("start_time")
        warnings.append(
            {
                "code": "missing_start_time",
                "message": (
                    "No clear start time was found in your text. "
                    "Pick a start time below before publishing."
                ),
            }
        )

    expected_date = extract_explicit_date(raw_text, now=now)
    date_mismatch = False
    if expected_date and start_time and start_time.date() != expected_date:
        date_mismatch = True
        warnings.append(
            {
                "code": "date_mismatch",
                "message": (
                    f"Your text mentions {expected_date.strftime('%b')} {expected_date.day}, but the draft "
                    f"was set to {start_time.strftime('%b')} {start_time.day}. The date has been aligned "
                    "to your text - please double-check it."
                ),
            }
        )

    return {
        "missing_fields": missing_fields,
        "warnings": warnings,
        "corrections": corrections,
        "expected_date": expected_date.isoformat() if expected_date else "",
        "date_mismatch": date_mismatch,
    }


def check_publish_date(raw_text, start_time, now=None):
    """Return a warning message when the submitted start_time conflicts with an
    explicit date in the original text, else None. Used as a publish blocker
    until the user confirms the date."""
    if not raw_text or not start_time:
        return None
    expected_date = extract_explicit_date(raw_text, now=now)
    if not expected_date:
        return None
    local = timezone.localtime(start_time)
    if local.date() == expected_date:
        return None
    return (
        f"Your original text mentions {expected_date.strftime('%b')} {expected_date.day}, but this card "
        f"is set to {local.strftime('%b')} {local.day}, {local.strftime('%H:%M')}. "
        "Confirm the date to publish anyway."
    )
