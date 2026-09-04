from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.observability.context import (
    new_request_context,
    reset_request_context,
    set_request_context,
)
from core.observability.events import emit_event


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/internal/metrics":
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode().lower(): v.decode(errors="replace")
            for k, v in scope.get("headers", [])
        }
        context = new_request_context(headers.get("x-request-id"))
        token = set_request_context(context)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                mutable_headers = list(message.get("headers", []))
                mutable_headers.append((b"x-request-id", context.request_id.encode()))
                message["headers"] = mutable_headers
            await send(message)

        emit_event(
            "http.request.started",
            "http",
            method=scope.get("method"),
            path=scope.get("path"),
        )
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            emit_event(
                "http.request.failed",
                "http",
                level="ERROR",
                force=True,
                outcome="failure",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            raise
        finally:
            emit_event(
                "http.request.completed",
                "http",
                force=True,
                outcome="success" if status_code < 500 else "failure",
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            reset_request_context(token)
