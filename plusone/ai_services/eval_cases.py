"""Benchmark cases for the Plus One drafting pipeline and safety moderation.

Two suites:

- PARSING_CASES: realistic student inputs across activity types, plus the
  regression cases observed in the 10-session usability test
  (docs/product_validation/03_usability_test_report.md):
    * U1-U5  - casual text without a clear clock time must NOT invent one,
               and the draft must flag the missing start time.
    * U6-U10 - an explicit date (e.g. "July 8") must survive parsing.
    * U3     - out-of-range expiry values must be clamped before review.
- MODERATION_CASES: risky inputs plus benign controls so precision and recall
  can both be measured.

Expected ``start`` values:
    None                      -> start_time must stay empty (no invented time)
    {"date": {...}, "time"}   -> date is either {"month": m, "day": d} for an
                                 explicit calendar date or {"days": n} meaning
                                 n days after "today" at evaluation time.
"""

PARSING_CASES = [
    # --- U6-U10 regression: explicit dates must not shift ---
    {
        "id": "u6_explicit_month_day",
        "tags": ["regression", "date"],
        "text": "Anyone up for badminton on July 16 at 19:00 at the sports hall? Intermediate level.",
        "expected": {
            "activity_type": "sports",
            "location_name": "Campus Sports Hall",
            "start": {"date": {"month": 7, "day": 16}, "time": "19:00"},
        },
    },
    {
        "id": "u7_explicit_date_pm",
        "tags": ["regression", "date"],
        "text": "Study session on July 8 at 3pm in the main library, bring flashcards.",
        "expected": {
            "activity_type": "study",
            "location_name": "Main Library",
            "start": {"date": {"month": 7, "day": 8}, "time": "15:00"},
        },
    },
    {
        "id": "u8_day_before_month",
        "tags": ["regression", "date"],
        "text": "Dinner at the dining hall on 16 July at 18:30?",
        "expected": {
            "activity_type": "food",
            "location_name": "North Dining Hall",
            "start": {"date": {"month": 7, "day": 16}, "time": "18:30"},
        },
    },
    {
        "id": "u9_numeric_date_european",
        "tags": ["regression", "date"],
        "text": "Basketball at the gym on 16.7. at 20:00, casual game.",
        "expected": {
            "activity_type": "sports",
            "location_name": "Campus Sports Hall",
            "start": {"date": {"month": 7, "day": 16}, "time": "20:00"},
        },
    },
    {
        "id": "u10_iso_date",
        "tags": ["regression", "date"],
        "text": "Club fair meetup 2026-07-20 at 14:00 at the student center.",
        "expected": {
            "activity_type": "club",
            "location_name": "Student Center",
            "start": {"date": {"month": 7, "day": 20}, "time": "14:00"},
        },
    },
    # --- U1-U5 regression: no clear clock time -> no invented time ---
    {
        "id": "u1_no_time",
        "tags": ["regression", "missing_time"],
        "text": "Looking for a lunch buddy at the mensa sometime today.",
        "expected": {
            "activity_type": "food",
            "location_name": "North Dining Hall",
            "start": None,
            "warnings": ["missing_start_time"],
        },
    },
    {
        "id": "u2_vague_evening",
        "tags": ["regression", "missing_time"],
        "text": "Want to study together in the library this evening?",
        "expected": {
            "activity_type": "study",
            "location_name": "Main Library",
            "start": None,
            "warnings": ["missing_start_time"],
        },
    },
    {
        "id": "u3_ambiguous_at_7",
        "tags": ["regression", "missing_time"],
        "text": "Tonight around 7 I want to go to the basketball game at the sports hall.",
        "expected": {
            "activity_type": "sports",
            "location_name": "Campus Sports Hall",
            "start": None,
            "warnings": ["missing_start_time"],
        },
    },
    {
        "id": "u4_after_class",
        "tags": ["regression", "missing_time"],
        "text": "Walk around the campus quad after class, anyone?",
        "expected": {
            "activity_type": "explore",
            "location_name": "Campus Quad",
            "start": None,
            "warnings": ["missing_start_time"],
        },
    },
    {
        "id": "u5_sometime_tomorrow",
        "tags": ["regression", "missing_time"],
        "text": "Coffee and homework tomorrow, main library?",
        "expected": {
            "activity_type": "study",
            "location_name": "Main Library",
            "start": None,
            "warnings": ["missing_start_time"],
        },
    },
    # --- Relative dates with clear times ---
    {
        "id": "tomorrow_24h",
        "tags": ["date"],
        "text": "Tomorrow at 12:30 lunch at the mensa?",
        "expected": {
            "activity_type": "food",
            "location_name": "North Dining Hall",
            "start": {"date": {"days": 1}, "time": "12:30"},
        },
    },
    {
        "id": "tonight_pm",
        "tags": ["date"],
        "text": "Tonight 8pm pickup basketball at the gym.",
        "expected": {
            "activity_type": "sports",
            "location_name": "Campus Sports Hall",
            "start": {"date": {"days": 0}, "time": "20:00"},
        },
    },
    {
        "id": "today_meridiem_dots",
        "tags": ["date"],
        "text": "Today at 5 p.m. quick campus walk from the quad.",
        "expected": {
            "activity_type": "explore",
            "location_name": "Campus Quad",
            "start": {"date": {"days": 0}, "time": "17:00"},
        },
    },
    # --- Activity/location coverage ---
    {
        "id": "coffee_food",
        "tags": ["coverage"],
        "text": "Coffee at 10am before lecture?",
        "expected": {"activity_type": "food", "start_time_required": True},
    },
    {
        "id": "language_practice_other",
        "tags": ["coverage"],
        "text": "Looking for a German language practice partner at 16:00.",
        "expected": {"activity_type": "other"},
    },
    {
        "id": "club_fair",
        "tags": ["coverage"],
        "text": "Explore the club fair booths at the student center at 13:00.",
        "expected": {
            "activity_type": "club",
            "location_name": "Student Center",
            "start": {"date": {"days": 0}, "time": "13:00"},
        },
    },
    {
        "id": "study_sprint",
        "tags": ["coverage"],
        "text": "Study sprint in the library at 9am, phones away.",
        "expected": {
            "activity_type": "study",
            "location_name": "Main Library",
            "start": {"date": {"days": 0}, "time": "09:00"},
        },
    },
    {
        "id": "dinner_18",
        "tags": ["coverage"],
        "text": "Dinner near the dining hall at 6pm.",
        "expected": {
            "activity_type": "food",
            "location_name": "North Dining Hall",
            "start": {"date": {"days": 0}, "time": "18:00"},
        },
    },
    # --- U3 regression: expiry bounds ---
    {
        "id": "u3_expiry_bounds",
        "tags": ["regression", "expiry"],
        "text": "Badminton on July 16 at 19:00, sports hall.",
        "expected": {
            "activity_type": "sports",
            "expire_between": [5, 180],
        },
    },
]


# (text, should_flag)
MODERATION_CASES = [
    # Risky
    ("Send me your phone number and address before we meet.", True),
    ("I will bring a weapon to the game.", True),
    ("Come alone in my room after the party.", True),
    ("Let's get drunk first and then decide.", True),
    ("Give me your password and I will register for you.", True),
    ("If you skip this I will threat you.", True),
    # Benign controls
    ("Let's meet at the library entrance.", False),
    ("Let's grab lunch before class.", False),
    ("Basketball at the sports hall tonight, casual game.", False),
    ("I can bring an extra racket for badminton.", False),
    ("Meet at the student center info desk, public spot.", False),
    ("Coffee at the north dining hall, my treat.", False),
]


# Opening-assistant cases: pre-assembled contexts (same shape gather_context
# produces), so the generation + validation steps are benchmarkable without a
# database or network. `expect_mentions` items must appear in at least one
# opener; `shared_expected` asserts the shared interest is actually used.
OPENING_CASES = [
    {
        "id": "sports_shared_interest",
        "context": {
            "post": {"title": "Basketball game tonight", "activity_type": "sports",
                     "location": "Campus Sports Hall", "start_time": ""},
            "viewer_role": "swiper",
            "viewer": {"major": "Design", "year": "Y2", "campus_area": "North",
                       "interests": "coffee, hiking, basketball"},
            "partner": {"major": "CS", "year": "Y3", "campus_area": "Central",
                        "interests": "basketball, board games, coffee"},
            "shared_interests": ["basketball", "coffee"],
        },
        "expect_mentions": ["Campus Sports Hall"],
        "shared_expected": True,
    },
    {
        "id": "food_no_shared_interests",
        "context": {
            "post": {"title": "Lunch at north dining hall", "activity_type": "food",
                     "location": "North Dining Hall", "start_time": ""},
            "viewer_role": "poster",
            "viewer": {"major": "Physics", "year": "Y1", "campus_area": "North", "interests": "chess"},
            "partner": {"major": "History", "year": "Y4", "campus_area": "South", "interests": "hiking"},
            "shared_interests": [],
        },
        "expect_mentions": ["North Dining Hall"],
        "shared_expected": False,
    },
    {
        "id": "study_empty_profiles",
        "context": {
            "post": {"title": "Study session at the library", "activity_type": "study",
                     "location": "Main Library", "start_time": ""},
            "viewer_role": "swiper",
            "viewer": {"major": "", "year": "", "campus_area": "", "interests": ""},
            "partner": {"major": "", "year": "", "campus_area": "", "interests": ""},
            "shared_interests": [],
        },
        "expect_mentions": ["Main Library"],
        "shared_expected": False,
    },
]
