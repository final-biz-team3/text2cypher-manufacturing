from __future__ import annotations

import os
import time
from collections.abc import Awaitable
from typing import Any

from core.observability.events import emit_event
from core.observability.metrics import (
    MODEL_CACHE_WRITE_TOKENS,
    MODEL_CACHED_INPUT_TOKENS,
    MODEL_CALL_DURATION,
    MODEL_CALLS,
    MODEL_ESTIMATED_COST,
    MODEL_INPUT_TOKENS,
    MODEL_OUTPUT_TOKENS,
    MODEL_REASONING_TOKENS,
)
from core.observability.pricing import PRICING_VERSION, estimate_cost_usd


async def observe_model_call(purpose: str, model: str, call: Awaitable[Any]) -> Any:
    started = time.perf_counter()
    emit_event("model.call.started", "model", model_purpose=purpose, model_name=model)
    try:
        response = await call
    except Exception:
        duration = time.perf_counter() - started
        MODEL_CALLS.labels(purpose, "failure").inc()
        MODEL_CALL_DURATION.labels(purpose, "failure").observe(duration)
        emit_event(
            "model.call.failed",
            "model",
            level="ERROR",
            force=True,
            outcome="failure",
            model_purpose=purpose,
            model_name=model,
            duration_ms=round(duration * 1000, 3),
        )
        raise
    duration = time.perf_counter() - started
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    cached_input_tokens = int(getattr(prompt_details, "cached_tokens", 0) or 0)
    cache_write_tokens = int(
        getattr(prompt_details, "cache_write_tokens", 0)
        or getattr(prompt_details, "cache_creation_tokens", 0)
        or 0
    )
    reasoning_tokens = int(getattr(completion_details, "reasoning_tokens", 0) or 0)
    estimated_cost_usd, pricing_tier = estimate_cost_usd(
        model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
    )
    MODEL_CALLS.labels(purpose, "success").inc()
    MODEL_CALL_DURATION.labels(purpose, "success").observe(duration)
    MODEL_INPUT_TOKENS.labels(purpose).inc(input_tokens)
    MODEL_OUTPUT_TOKENS.labels(purpose).inc(output_tokens)
    MODEL_CACHED_INPUT_TOKENS.labels(purpose).inc(cached_input_tokens)
    MODEL_CACHE_WRITE_TOKENS.labels(purpose).inc(cache_write_tokens)
    MODEL_REASONING_TOKENS.labels(purpose).inc(reasoning_tokens)
    if estimated_cost_usd is not None:
        MODEL_ESTIMATED_COST.labels(purpose).inc(estimated_cost_usd)
    emit_event(
        "model.call.completed",
        "model",
        model_purpose=purpose,
        model_name=model,
        duration_ms=round(duration * 1000, 3),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        pricing_status="configured" if estimated_cost_usd is not None else "unknown",
        pricing_tier=pricing_tier,
        pricing_version=os.getenv("OPENAI_PRICING_VERSION", PRICING_VERSION),
    )
    return response
