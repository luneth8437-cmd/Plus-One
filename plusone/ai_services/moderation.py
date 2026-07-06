import json
import time

from plusone.ai_services.client import chat_completion as default_chat_completion
from plusone.ai_services.client import llm_client as default_llm_client
from plusone.ai_services.logging import save_log
from plusone.models import LLMLog


UNSAFE_KEYWORDS = {
    "harass",
    "hate",
    "threat",
    "weapon",
    "drugs",
    "drunk",
    "address",
    "phone number",
    "password",
    "alone in my room",
}


def rule_moderate_text(text):
    lowered = text.lower()
    hits = [keyword for keyword in UNSAFE_KEYWORDS if keyword in lowered]
    return {
        "flagged": bool(hits),
        "categories": hits,
        "reason": "Matched demo safety keywords." if hits else "No demo safety keyword matched.",
    }


def _merge_moderation_results(llm_result, rule_result):
    # Local rules are a hard safety floor: if they flag content, the final
    # answer must stay flagged even when the model response is permissive.
    llm_categories = llm_result.get("categories") or []
    if not isinstance(llm_categories, list):
        llm_categories = [str(llm_categories)]
    rule_categories = rule_result.get("categories") or []
    categories = sorted({str(category) for category in [*llm_categories, *rule_categories] if category})
    flagged = bool(llm_result.get("flagged")) or bool(rule_result.get("flagged"))

    if rule_result.get("flagged") and llm_result.get("reason"):
        reason = f"{rule_result['reason']} Model note: {llm_result['reason']}"
    elif rule_result.get("flagged"):
        reason = rule_result["reason"]
    else:
        reason = llm_result.get("reason") or rule_result.get("reason", "")

    return {
        **llm_result,
        "flagged": flagged,
        "categories": categories,
        "reason": reason,
    }


def moderate_text(user, text, llm_client=default_llm_client, chat_completion=default_chat_completion):
    started_at = time.perf_counter()
    rule_result = rule_moderate_text(text)
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
                    {"role": "system", "content": "Return JSON: flagged boolean, categories array, reason string. Flag unsafe campus meetup content, harassment, personal information, or inappropriate text."},
                    {"role": "user", "content": text},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            result = _merge_moderation_results(json.loads(raw), rule_result)
            save_log(user, LLMLog.TaskType.MODERATION, text, result, raw, model, strategy, True, started_at)
            return result
        except Exception as exc:
            # Moderation must fail closed to the deterministic rule result;
            # posting/chatting should not depend on the external API being up.
            save_log(user, LLMLog.TaskType.MODERATION, text, rule_result, str(exc), model, f"{strategy}_failed_rule_fallback", False, started_at)
            return rule_result
    save_log(user, LLMLog.TaskType.MODERATION, text, rule_result, json.dumps(rule_result), "", "rule_fallback", True, started_at)
    return rule_result
