import asyncio
import math
import os

from django.conf import settings


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_LLM_TIMEOUT_SECONDS = 8.0
DEFAULT_LLM_MAX_RETRIES = 0
MODERATION_TIMEOUT_SECONDS = 5.0
INTERACTIVE_TIMEOUT_SECONDS = 8.0
MIN_REQUEST_TIMEOUT_SECONDS = 0.1


def _positive_float_env(name, default):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _bounded_timeout_env(name, default, maximum, fallback_name=None):
    try:
        raw_value = os.environ.get(name)
        if raw_value is None and fallback_name:
            raw_value = os.environ.get(fallback_name)
        value = float(default if raw_value is None else raw_value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return max(MIN_REQUEST_TIMEOUT_SECONDS, min(value, maximum))


def request_timeout_seconds(task):
    """Return a per-request timeout capped by the user-facing latency budget."""
    if task == "moderation":
        return _bounded_timeout_env(
            "PLUSONE_MODERATION_TIMEOUT_SECONDS",
            MODERATION_TIMEOUT_SECONDS,
            MODERATION_TIMEOUT_SECONDS,
        )
    name = "PLUSONE_PARSING_TIMEOUT_SECONDS" if task == "parsing" else "PLUSONE_OPENERS_TIMEOUT_SECONDS"
    return _bounded_timeout_env(
        name,
        INTERACTIVE_TIMEOUT_SECONDS,
        INTERACTIVE_TIMEOUT_SECONDS,
        fallback_name="PLUSONE_INTERACTIVE_TIMEOUT_SECONDS",
    )


def llm_config():
    # DeepSeek is the primary provider for this project. OpenAI stays as a
    # compatible fallback because both providers use the OpenAI client shape.
    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY")
    if deepseek_api_key:
        return {
            "api_key": deepseek_api_key,
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL),
            "model": (
                os.environ.get("PLUSONE_LLM_MODEL")
                or os.environ.get("DEEPSEEK_MODEL")
                or getattr(settings, "PLUSONE_DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL)
            ),
            "strategy": "deepseek",
        }

    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if openai_api_key:
        return {
            "api_key": openai_api_key,
            "base_url": os.environ.get("OPENAI_BASE_URL", ""),
            "model": (
                os.environ.get("PLUSONE_LLM_MODEL")
                or os.environ.get("PLUSONE_OPENAI_MODEL")
                or getattr(settings, "PLUSONE_OPENAI_MODEL", "gpt-4o-mini")
            ),
            "strategy": "openai",
        }

    return None


def llm_client():
    config = llm_config()
    if not config:
        return None
    try:
        from openai import AsyncOpenAI
    except Exception:
        return None

    kwargs = {
        "api_key": config["api_key"],
        "timeout": _positive_float_env("PLUSONE_LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS),
        # These calls sit directly in request/response paths. Retrying inside
        # the SDK can exceed the product latency budget, so callers get one
        # cancellable HTTP attempt and decide how to fall back.
        "max_retries": DEFAULT_LLM_MAX_RETRIES,
    }
    if config["base_url"]:
        kwargs["base_url"] = config["base_url"]
    return AsyncOpenAI(**kwargs), config


def chat_completion(client, llm_config, **kwargs):
    kwargs["model"] = llm_config["model"]
    if llm_config["strategy"] == "deepseek":
        # Keep reasoning disabled by default so short moderation/parse calls
        # stay fast and predictable during the user-facing create/chat flow.
        extra_body = kwargs.pop("extra_body", {}) or {}
        thinking_type = os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower()
        if thinking_type not in {"enabled", "disabled"}:
            thinking_type = "disabled"
        extra_body.setdefault("thinking", {"type": thinking_type})
        kwargs["extra_body"] = extra_body
    budget = min(float(kwargs.get("timeout", INTERACTIVE_TIMEOUT_SECONDS)), INTERACTIVE_TIMEOUT_SECONDS)

    async def request():
        # Cancellation propagates into HTTPX and closes its socket. A read
        # timeout alone is not a total wall-clock deadline (a trickling server
        # can reset it), and an abandoned worker thread would keep spending.
        async with client:
            return await client.chat.completions.create(**kwargs)

    async def bounded_request():
        return await asyncio.wait_for(request(), timeout=budget)

    return asyncio.run(bounded_request())
