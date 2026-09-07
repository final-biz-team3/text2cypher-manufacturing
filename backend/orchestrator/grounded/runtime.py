"""Per-request behavior; never change global retry behavior for concurrent B calls."""

from contextvars import ContextVar

grounded_execution: ContextVar[bool] = ContextVar("grounded_execution", default=False)
