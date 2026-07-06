import time

from plusone.ai_services.client import chat_completion as default_chat_completion
from plusone.ai_services.client import llm_client as default_llm_client
from plusone.ai_services.logging import save_log
from plusone.models import ActivityPost, LLMLog


def rule_generate_icebreaker(post):
    if post.activity_type == ActivityPost.ActivityType.SPORTS:
        return "Are you planning to watch the full game or just drop by for a bit?"
    if post.activity_type == ActivityPost.ActivityType.FOOD:
        return "Are you heading there now, or should we meet near the entrance first?"
    if post.activity_type == ActivityPost.ActivityType.STUDY:
        return "Do you prefer a quiet table or a quick planning check-in first?"
    if post.activity_type == ActivityPost.ActivityType.CLUB:
        return "Any booths you already want to visit first?"
    return "What would make this plan easy for you to join?"


def generate_icebreaker(user, post, llm_client=default_llm_client, chat_completion=default_chat_completion):
    started_at = time.perf_counter()
    prompt = f"{post.title} at {post.location.name}, type={post.activity_type}"
    llm = llm_client()
    if llm:
        client, llm_config = llm
        model = llm_config["model"]
        strategy = llm_config["strategy"]
        try:
            response = chat_completion(
                client,
                llm_config,
                messages=[
                    {"role": "system", "content": "Write one friendly, platonic, short icebreaker for a five-minute campus meetup chat."},
                    {"role": "user", "content": prompt},
                ],
            )
            text = (response.choices[0].message.content or "").strip()[:240]
            save_log(user, LLMLog.TaskType.ICEBREAKER, prompt, {}, text, model, strategy, True, started_at)
            return text
        except Exception as exc:
            text = rule_generate_icebreaker(post)
            save_log(user, LLMLog.TaskType.ICEBREAKER, prompt, {}, str(exc), model, f"{strategy}_failed_rule_fallback", False, started_at)
            return text
    text = rule_generate_icebreaker(post)
    save_log(user, LLMLog.TaskType.ICEBREAKER, prompt, {}, text, "", "rule_fallback", True, started_at)
    return text
