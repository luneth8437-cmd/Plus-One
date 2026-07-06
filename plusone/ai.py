from .ai_services.client import DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL
from .ai_services.client import chat_completion as _default_chat_completion
from .ai_services.client import llm_client as _default_llm_client
from .ai_services.icebreaker import generate_icebreaker as _generate_icebreaker
from .ai_services.icebreaker import rule_generate_icebreaker
from .ai_services.moderation import UNSAFE_KEYWORDS, moderate_text as _moderate_text
from .ai_services.moderation import rule_moderate_text
from .ai_services.parsing import parse_activity_text as _parse_activity_text
from .ai_services.parsing import rule_parse_activity
from .ai_services.parsing import suggest_ambiguous_time_options


def _llm_client():
    return _default_llm_client()


def _chat_completion(client, llm_config, **kwargs):
    return _default_chat_completion(client, llm_config, **kwargs)


def parse_activity_text(user, text):
    return _parse_activity_text(user, text, llm_client=_llm_client, chat_completion=_chat_completion)


def generate_icebreaker(user, post):
    return _generate_icebreaker(user, post, llm_client=_llm_client, chat_completion=_chat_completion)


def moderate_text(user, text):
    return _moderate_text(user, text, llm_client=_llm_client, chat_completion=_chat_completion)
