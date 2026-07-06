import os

from django.conf import settings


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"


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
        from openai import OpenAI
    except Exception:
        return None

    kwargs = {"api_key": config["api_key"]}
    if config["base_url"]:
        kwargs["base_url"] = config["base_url"]
    return OpenAI(**kwargs), config


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
    return client.chat.completions.create(**kwargs)
