"""One shared budget, including concurrent and legacy candidate model calls."""

from time import monotonic
from typing import Any


class BudgetExceededError(RuntimeError):
    pass


class RequestBudget:
    def __init__(self, seconds: float = 120, calls: int = 12):
        self.deadline = monotonic() + seconds
        self.max_calls = calls
        self.calls = 0

    def reserve(self) -> float:
        remaining = self.deadline - monotonic()
        if self.calls >= self.max_calls or remaining <= 0:
            raise BudgetExceededError("Request model/time budget exhausted")
        self.calls += 1
        return remaining


class BudgetClient:
    """Proxy the SDK so legacy code cannot bypass call accounting or retry limits."""

    def __init__(self, client: Any, budget: RequestBudget):
        self.client = client
        self.budget = budget
        self.chat = self
        self.completions = self

    async def create(self, **kwargs: Any) -> Any:
        remaining = self.budget.reserve()
        # Disable SDK retries: every actual request has a budget reservation.
        client = self.client.with_options(max_retries=0, timeout=remaining)
        return await client.chat.completions.create(**kwargs)
