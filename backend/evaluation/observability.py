"""평가 실행에만 사용하는 OpenAI 호출 계측 프록시."""

from time import perf_counter
from typing import Any

from core.observability.pricing import estimate_cost_usd


class _CountingCompletions:
    def __init__(self, owner: "CountingOpenAIClient", completions: Any) -> None:
        self._owner = owner
        self._completions = completions

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        started = perf_counter()
        self._owner.call_count += 1
        try:
            response = await self._completions.create(*args, **kwargs)
            self._owner.record_usage(response, str(kwargs.get("model", "")))
            return response
        finally:
            self._owner.model_elapsed_ms += (perf_counter() - started) * 1000


class _CountingChat:
    def __init__(self, owner: "CountingOpenAIClient", chat: Any) -> None:
        self._chat = chat
        self.completions = _CountingCompletions(owner, chat.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)


class CountingOpenAIClient:
    """chat.completions 호출만 세며 원본 client의 나머지 표면은 위임한다."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.call_count = 0
        self.model_elapsed_ms = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_input_tokens = 0
        self.cache_write_tokens = 0
        self.reasoning_tokens = 0
        self.estimated_cost_usd = 0.0
        self.chat = _CountingChat(self, client.chat)

    def record_usage(self, response: Any, model: str) -> None:
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
        estimated_cost, _ = estimate_cost_usd(
            model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
        )
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_input_tokens += cached_input_tokens
        self.cache_write_tokens += cache_write_tokens
        self.reasoning_tokens += reasoning_tokens
        if estimated_cost is not None:
            self.estimated_cost_usd += estimated_cost

    def reset_case(self) -> None:
        self.call_count = 0
        self.model_elapsed_ms = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_input_tokens = 0
        self.cache_write_tokens = 0
        self.reasoning_tokens = 0
        self.estimated_cost_usd = 0.0

    def snapshot(self) -> dict[str, int | float]:
        return {
            "modelCallCount": self.call_count,
            "modelElapsedMs": round(self.model_elapsed_ms, 3),
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "estimatedCostUsd": round(self.estimated_cost_usd, 10),
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
