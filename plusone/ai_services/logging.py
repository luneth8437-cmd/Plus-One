import time

from plusone.models import LLMLog


def save_log(user, task_type, input_text, output_json=None, output_text="", model="", strategy="rule_fallback", success=True, started_at=None):
    latency_ms = 0
    if started_at is not None:
        latency_ms = max(0, int((time.perf_counter() - started_at) * 1000))
    return LLMLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        task_type=task_type,
        input_text=input_text,
        output_json=output_json or {},
        output_text=output_text,
        model=model,
        strategy=strategy,
        success=success,
        latency_ms=latency_ms,
    )
