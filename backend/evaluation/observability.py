"""평가 실행에만 사용하는 OpenAI 호출 계측 프록시."""

from time import perf_counter
from typing import Any

from core.observability.pricing import estimate_cost_usd


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _token_count(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


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
        self.chat = _CountingChat(self, client.chat)
        self.reset_case()

    def record_usage(self, response: Any, model: str) -> None:
        usage = _field(response, "usage")
        if usage is None:
            return
        input_tokens = _token_count(_field(usage, "prompt_tokens"))
        output_tokens = _token_count(_field(usage, "completion_tokens"))
        prompt_details = _field(usage, "prompt_tokens_details")
        completion_details = _field(usage, "completion_tokens_details")
        cached_input_tokens = _token_count(_field(prompt_details, "cached_tokens"))
        cache_write_tokens = _token_count(
            _field(prompt_details, "cache_write_tokens")
            or _field(prompt_details, "cache_creation_tokens")
        )
        reasoning_tokens = _token_count(_field(completion_details, "reasoning_tokens"))
        total_tokens = _token_count(_field(usage, "total_tokens"))
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
        self.total_tokens += total_tokens
        self.usage_reported_call_count += 1
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
        self.usage_reported_call_count = 0
        self.total_tokens = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "modelCallCount": self.call_count,
            "modelElapsedMs": round(self.model_elapsed_ms, 3),
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "estimatedCostUsd": round(self.estimated_cost_usd, 10),
            "modelTokenUsage": {
                "reportedCallCount": self.usage_reported_call_count,
                "promptTokens": self.input_tokens,
                "cachedPromptTokens": self.cached_input_tokens,
                "cacheWritePromptTokens": self.cache_write_tokens,
                "completionTokens": self.output_tokens,
                "reasoningTokens": self.reasoning_tokens,
                "totalTokens": self.total_tokens,
            },
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
