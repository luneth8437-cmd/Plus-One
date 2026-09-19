import json
import re
import time

from django.conf import settings

from plusone.ai_services.client import chat_completion as default_chat_completion
from plusone.ai_services.client import llm_client as default_llm_client
from plusone.ai_services.client import request_timeout_seconds
from plusone.ai_services.logging import save_log
from plusone.models import LLMLog


UNSAFE_PATTERNS = (
    ("harass", re.compile(r"(?<![a-z])harass(?:ed|es|ing|ment)?(?![a-z])", re.IGNORECASE)),
    ("hate", re.compile(r"(?<![a-z])hate(?:d|s|ful)?(?![a-z])", re.IGNORECASE)),
    ("threat", re.compile(r"(?<![a-z])threat(?:s|en|ens|ened|ening)?(?![a-z])", re.IGNORECASE)),
    ("weapon", re.compile(r"(?<![a-z])weapons?(?![a-z])", re.IGNORECASE)),
    ("drugs", re.compile(r"(?<![a-z])drugs?(?![a-z])", re.IGNORECASE)),
    ("drunk", re.compile(r"(?<![a-z])drunk(?![a-z])", re.IGNORECASE)),
    ("private contact", re.compile(
        r"(?<![a-z])(?:send|give|share|tell|text|message|dm)\s+(?:me\s+(?:your\s+)?|your\s+)"
        r"(?:phone(?:\s+number)?|mobile(?:\s+number)?|number|home\s+address|address|"
        r"password|wechat(?:\s+id)?|whatsapp|telegram)(?![a-z])",
        re.IGNORECASE,
    )),
    ("private contact", re.compile(
        r"(?<![a-z])(?:can|could|would)\s+you\s+(?:send|give|share|tell|text|message|dm)\s+"
        r"(?:me\s+)?your\s+(?:phone(?:\s+number)?|mobile(?:\s+number)?|home\s+address|"
        r"address|password|wechat(?:\s+id)?|whatsapp|telegram)(?![a-z])",
        re.IGNORECASE,
    )),
    ("private contact", re.compile(
        r"(?<![a-z])(?:what(?:'s|\s+is)|may\s+i\s+have|can\s+i\s+have)\s+your\s+"
        r"(?:phone(?:\s+number)?|mobile(?:\s+number)?|home\s+address|address|"
        r"password|wechat(?:\s+id)?|whatsapp|telegram)(?![a-z])",
        re.IGNORECASE,
    )),
    ("private contact", re.compile(
        r"(?:发|给|告诉|加)(?:给)?我(?:一下)?(?:你的|你)?(?:手机号|手机号码|电话号码|"
        r"微信号|微信|住址|家庭地址|密码)"
    )),
    ("private contact", re.compile(
        r"(?:把|将)(?:你的|你)?(?:手机号|手机号码|电话号码|微信号|微信|住址|家庭地址|密码)"
        r"(?:发|给|告诉)(?:给)?我"
    )),
    ("private contact", re.compile(
        r"(?:你的|你)(?:手机号|手机号码|电话号码|微信号|微信|住址|家庭地址|密码)(?:是|为)?(?:多少|什么)"
    )),
    ("private contact", re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")),
    ("private contact", re.compile(
        r"(?:微信|微信号|wechat(?:\s+id)?|vx)\s*[:：号是]\s*[a-z][a-z0-9_-]{5,19}",
        re.IGNORECASE,
    )),
    ("unsafe meetup", re.compile(r"(?<![a-z])alone\s+in\s+my\s+room(?![a-z])", re.IGNORECASE)),
    ("harass", re.compile(r"骚扰|威胁|仇恨")),
    ("weapon", re.compile(r"武器|刀具|枪支")),
    ("drugs", re.compile(r"毒品|吸毒")),
    ("drunk", re.compile(r"喝醉|灌醉")),
)

UNAVAILABLE_REASON = "Safety checking is temporarily unavailable. Please try again."


def _unavailable_result():
    return {
        "flagged": False,
        "categories": [],
        "reason": UNAVAILABLE_REASON,
        "service_unavailable": True,
    }


def rule_moderate_text(text):
    value = str(text or "")
    hits = sorted({category for category, pattern in UNSAFE_PATTERNS if pattern.search(value)})
    return {
        "flagged": bool(hits),
        "categories": hits,
        "reason": "Matched safety keywords." if hits else "No safety keyword matched.",
        "service_unavailable": False,
    }


def _normalize_provider_result(result):
    if not isinstance(result, dict) or not isinstance(result.get("flagged"), bool):
        raise ValueError("Moderation provider returned an invalid decision.")
    categories = result.get("categories") or []
    if not isinstance(categories, list):
        raise ValueError("Moderation provider returned invalid categories.")
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "Content passed the safety check." if not result["flagged"] else "Content may be unsafe."
    return {
        "flagged": result["flagged"],
        "categories": [str(category) for category in categories if category],
        "reason": reason.strip(),
        "service_unavailable": False,
    }


def moderate_text(user, text, llm_client=default_llm_client, chat_completion=default_chat_completion):
    started_at = time.perf_counter()
    mode = getattr(settings, "PLUSONE_MODERATION_MODE", "external")
    if mode == "rules":
        if settings.DEBUG:
            result = rule_moderate_text(text)
            save_log(
                user, LLMLog.TaskType.MODERATION, text, result,
                json.dumps(result, ensure_ascii=False), "", "rules_debug", True, started_at,
            )
            return result
        result = _unavailable_result()
        save_log(
            user, LLMLog.TaskType.MODERATION, text, result,
            "Rules mode is disabled outside DEBUG.", "", "rules_disabled", False, started_at,
        )
        return result
    if mode not in {"external", "provider"}:
        result = _unavailable_result()
        save_log(
            user, LLMLog.TaskType.MODERATION, text, result,
            f"Unknown moderation mode: {mode!r}.", "", "invalid_mode", False, started_at,
        )
        return result

    llm = llm_client()
    if llm:
        client, llm_config = llm
        model = llm_config["model"]
        strategy = llm_config["strategy"]
        try:
            response = chat_completion(
                client,
                llm_config,
                timeout=request_timeout_seconds("moderation"),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Return JSON: flagged boolean, categories array, reason string. Flag unsafe campus meetup content, harassment, personal information, or inappropriate text."},
                    {"role": "user", "content": text},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            result = _normalize_provider_result(json.loads(raw))
            save_log(user, LLMLog.TaskType.MODERATION, text, result, raw, model, strategy, True, started_at)
            return result
        except Exception as exc:
            result = _unavailable_result()
            save_log(
                user, LLMLog.TaskType.MODERATION, text, result, str(exc), model,
                f"{strategy}_unavailable", False, started_at,
            )
            return result
    result = _unavailable_result()
    save_log(
        user, LLMLog.TaskType.MODERATION, text, result,
        "No moderation provider is configured.", "", "provider_unavailable", False, started_at,
    )
    return result
