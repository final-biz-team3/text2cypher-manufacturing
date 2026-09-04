from __future__ import annotations

import hashlib
import os
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from uuid_utils import uuid7


@dataclass(slots=True)
class RequestContext:
    request_id: str
    client_request_id: str | None = None
    question_fingerprint: str | None = None
    question_redacted: str | None = None
    route: str = "UNKNOWN"
    sampled: bool = True
    failed: bool = False
    planned_tools: list[str] = field(default_factory=list)
    executed_tools: list[str] = field(default_factory=list)
    successful_tools: list[str] = field(default_factory=list)
    failed_tools: list[str] = field(default_factory=list)
    skipped_tools: list[str] = field(default_factory=list)


_context: ContextVar[RequestContext | None] = ContextVar(
    "observability_context", default=None
)


def new_request_context(client_request_id: str | None = None) -> RequestContext:
    request_id = str(uuid7())
    env = os.getenv("DEPLOYMENT_ENVIRONMENT", os.getenv("APP_ENV", "dev")).lower()
    rate = float(
        os.getenv(
            "LOG_SUCCESS_DETAIL_SAMPLE_RATE",
            "0.1" if env in {"prod", "production"} else "1.0",
        )
    )
    bucket = int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return RequestContext(
        request_id, client_request_id, sampled=bucket < max(0.0, min(rate, 1.0))
    )


def set_request_context(context: RequestContext) -> Token:
    return _context.set(context)


def reset_request_context(token: Token) -> None:
    _context.reset(token)


def get_request_context() -> RequestContext | None:
    return _context.get()


def request_id() -> str:
    context = get_request_context()
    return context.request_id if context else str(uuid7())
