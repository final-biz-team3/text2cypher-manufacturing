"""평가 실행에만 사용하는 OpenAI 호출 계측 프록시."""

from time import perf_counter
from typing import Any


class _CountingCompletions:
    def __init__(self, owner: "CountingOpenAIClient", completions: Any) -> None:
        self._owner = owner
        self._completions = completions

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        started = perf_counter()
        self._owner.call_count += 1
        try:
            return await self._completions.create(*args, **kwargs)
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
        self.chat = _CountingChat(self, client.chat)

    def reset_case(self) -> None:
        self.call_count = 0
        self.model_elapsed_ms = 0.0

    def snapshot(self) -> dict[str, int | float]:
        return {
            "modelCallCount": self.call_count,
            "modelElapsedMs": round(self.model_elapsed_ms, 3),
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
