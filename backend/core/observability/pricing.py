from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextPrice:
    input_per_million: float
    cached_input_per_million: float
    cache_write_per_million: float
    output_per_million: float


@dataclass(frozen=True, slots=True)
class ModelPrice:
    short: ContextPrice
    long: ContextPrice


PRICING_VERSION = "gpt-5.6-2026-09-02"
MODEL_PRICES: dict[str, ModelPrice] = {
    "gpt-5.6-sol": ModelPrice(
        short=ContextPrice(4.00, 0.40, 5.00, 20.00),
        long=ContextPrice(8.00, 0.80, 10.00, 30.00),
    ),
    "gpt-5.6-terra": ModelPrice(
        short=ContextPrice(2.00, 0.20, 2.50, 12.00),
        long=ContextPrice(4.00, 0.40, 5.00, 18.00),
    ),
    "gpt-5.6-luna": ModelPrice(
        short=ContextPrice(0.20, 0.02, 0.25, 1.20),
        long=ContextPrice(0.40, 0.04, 0.50, 1.80),
    ),
}


def pricing_tier(input_tokens: int) -> str:
    threshold = int(os.getenv("OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS", "272000"))
    return "long" if input_tokens > threshold else "short"


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    cached_input_tokens: int,
    cache_write_tokens: int,
    output_tokens: int,
) -> tuple[float | None, str]:
    model_price = MODEL_PRICES.get(model)
    tier = pricing_tier(input_tokens)
    if model_price is None:
        return None, tier
    price = model_price.long if tier == "long" else model_price.short
    uncached_input_tokens = max(
        input_tokens - cached_input_tokens - cache_write_tokens, 0
    )
    cost = (
        uncached_input_tokens * price.input_per_million
        + cached_input_tokens * price.cached_input_per_million
        + cache_write_tokens * price.cache_write_per_million
        + output_tokens * price.output_per_million
    ) / 1_000_000
    return round(cost, 10), tier
