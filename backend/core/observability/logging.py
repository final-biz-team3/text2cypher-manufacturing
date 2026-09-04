from __future__ import annotations

import json
import logging
import os
import queue
import sys
from logging.handlers import QueueHandler, QueueListener


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "observability_event", None)
        if isinstance(event, dict):
            return json.dumps(event, ensure_ascii=False, default=str)
        return json.dumps(
            {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


_listener: QueueListener | None = None


def configure_observability_logging() -> None:
    global _listener
    if _listener is not None:
        return
    output = logging.StreamHandler(sys.stdout)
    output.setFormatter(JsonFormatter())
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(
        maxsize=int(os.getenv("LOG_QUEUE_SIZE", "10000"))
    )
    handler = QueueHandler(log_queue)
    target = logging.getLogger("itda.observability")
    target.handlers.clear()
    target.addHandler(handler)
    target.setLevel(
        getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    )
    target.propagate = False
    _listener = QueueListener(log_queue, output, respect_handler_level=True)
    _listener.start()


def stop_observability_logging() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None
