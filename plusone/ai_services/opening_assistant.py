"""Opening assistant: the one agent-shaped AI surface in Plus One.

Pipeline (multi-step, each step testable in isolation):

1. gather_context(match, for_user)  - tool step: read both parties' activity
   card and profiles, compute shared interests, and strip anything that could
   deanonymize a participant (no names, no usernames).
2. LLM generation                    - one bounded call that turns the context
   into 2-3 personalized openers, each with a stated reason.
3. validate_openers(...)             - guardrail step: length cap, moderation
   check per opener, dedupe, drop anything that asks for personal info.
4. rule_generate_openers(context)    - deterministic fallback that uses the
   same context, so personalization survives an LLM outage.

The assistant never sends anything: suggestions fill the chat input and the
human presses Send (same assist-not-auto-publish rule as post creation).
"""

import json
import time

from plusone.ai_services.client import chat_completion as default_chat_completion
from plusone.ai_services.client import llm_client as default_llm_client
from plusone.ai_services.logging import save_log
from plusone.ai_services.moderation import rule_moderate_text
from plusone.models import ActivityPost, LLMLog, UserProfile

MAX_OPENERS = 3
MIN_OPENERS = 2
MAX_OPENER_LENGTH = 200

# Openers must not fish for identifying details in an anonymous chat.
PERSONAL_INFO_PROBES = (
    "your name", "real name", "last name", "phone", "wechat", "instagram",
    "snapchat", "dorm room", "your address", "student id",
)


def _interest_tokens(raw):
    return {
        token.strip().lower()
        for token in (raw or "").replace(";", ",").split(",")
        if token.strip()
    }


def _profile_card(user):
    profile = UserProfile.objects.filter(user=user).first()
    return {
        "major": profile.major if profile else "",
        "year": profile.year if profile else "",
        "campus_area": profile.campus_area if profile else "",
        "interests": profile.interests if profile else "",
    }


def gather_context(match, for_user):
    """Step 1: assemble both cards without identity leakage.

    Deliberately excluded: display_name, username, avatar - the chat is
    anonymous and the assistant must not undo that.
    """
    post = match.post
    partner = match.swiper if for_user.id == match.poster_id else match.poster
    viewer_card = _profile_card(for_user)
    partner_card = _profile_card(partner)
    shared = sorted(
        _interest_tokens(viewer_card["interests"]) & _interest_tokens(partner_card["interests"])
    )
    return {
        "post": {
            "title": post.title,
            "activity_type": post.activity_type,
            "location": post.location.name,
            "start_time": post.start_time.isoformat() if post.start_time else "",
        },
        "viewer_role": "poster" if for_user.id == match.poster_id else "swiper",
        "viewer": viewer_card,
        "partner": partner_card,
        "shared_interests": shared,
    }


def validate_openers(candidates):
    """Step 3: guardrails. Returns only openers safe to show."""
    valid, seen = [], set()
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        reason = str(item.get("reason", "")).strip()[:160]
        if not text or len(text) > MAX_OPENER_LENGTH:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        if any(probe in lowered for probe in PERSONAL_INFO_PROBES):
            continue
        if rule_moderate_text(text).get("flagged"):
            continue
        seen.add(lowered)
        valid.append({"text": text, "reason": reason})
        if len(valid) == MAX_OPENERS:
            break
    return valid


def rule_generate_openers(context):
    """Step 4: deterministic fallback built from the same gathered context."""
    post = context["post"]
    location = post["location"]
    shared = context["shared_interests"]
    openers = []
    if shared:
        openers.append({
            "text": f"I noticed we both put {shared[0]} in our interests - nice coincidence. Still up for {post['title'].lower()}?",
            "reason": f"References your shared interest ({shared[0]}) to make the opener personal.",
        })
    type_lines = {
        ActivityPost.ActivityType.SPORTS: (
            f"Hey! Are you planning to play the whole time at {location}, or just drop in for a bit?",
            "Activity-specific question that is easy to answer and settles expectations.",
        ),
        ActivityPost.ActivityType.FOOD: (
            f"Hi! Should we meet at the entrance of {location}, or grab a table first?",
            "Moves straight to logistics, which suits a short five-minute chat.",
        ),
        ActivityPost.ActivityType.STUDY: (
            f"Hey! Quiet-focus session or okay to chat a little while we work at {location}?",
            "Clarifies study style upfront so the meetup matches expectations.",
        ),
    }
    line = type_lines.get(post["activity_type"])
    if line:
        openers.append({"text": line[0], "reason": line[1]})
    openers.append({
        "text": f"Hi! I can head to {location} soon - what would make this plan easiest for you?",
        "reason": "Open-ended but concrete: confirms the place and hands them control.",
    })
    return validate_openers(openers)


def generate_openers(user, match, llm_client=default_llm_client, chat_completion=default_chat_completion):
    """Steps 1-4 end to end. Always returns 2-3 validated openers."""
    started_at = time.perf_counter()
    context = gather_context(match, user)
    prompt = json.dumps(context, ensure_ascii=False)
    llm = llm_client()
    if llm:
        client, llm_config = llm
        model = llm_config["model"]
        strategy = llm_config["strategy"]
        try:
            response = chat_completion(
                client,
                llm_config,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You help two anonymous students start a friendly five-minute chat "
                            "after matching on a campus activity. Using the provided context, return JSON "
                            '{"openers": [{"text": ..., "reason": ...}]} with exactly 3 openers the viewer '
                            "could send as their first message. Rules: platonic and casual; max 200 characters "
                            "each; reference the shared activity, location, or shared_interests when available; "
                            "each reason explains in one sentence why that opener fits this specific match; "
                            "never ask for names, socials, or any identifying information."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            openers = validate_openers(json.loads(raw).get("openers"))
            if len(openers) < MIN_OPENERS:
                # Top up from the deterministic path instead of failing.
                extra = [o for o in rule_generate_openers(context)
                         if o["text"].lower() not in {v["text"].lower() for v in openers}]
                openers = (openers + extra)[:MAX_OPENERS]
                strategy = f"{strategy}_topped_up_rule"
            save_log(user, LLMLog.TaskType.OPENING_ASSISTANT, prompt,
                     {"openers": openers}, raw, model, strategy, True, started_at)
            return openers
        except Exception as exc:
            openers = rule_generate_openers(context)
            save_log(user, LLMLog.TaskType.OPENING_ASSISTANT, prompt,
                     {"openers": openers}, str(exc), model,
                     f"{strategy}_failed_rule_fallback", False, started_at)
            return openers
    openers = rule_generate_openers(context)
    save_log(user, LLMLog.TaskType.OPENING_ASSISTANT, prompt,
             {"openers": openers}, json.dumps(openers), "", "rule_fallback", True, started_at)
    return openers
