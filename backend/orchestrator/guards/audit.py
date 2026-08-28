"""쿼리 가드 차단/허용 이력을 attempts와 분리된 전용 스트림에 감사 로그로 남긴다.
기본은 파일 전용(logs/query_guard_audit.jsonl)이며, GUARD_AUDIT_ALSO_CONSOLE=true면
루트 로거(콘솔)에도 함께 전파한다 - 확장성 논의 결과 파일 전용을 기본으로 채택
(md/2026-08-25-execute_sql_cypher-구현-고려사항-정리.md §5-23)."""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

_LOGGER_NAME = "orchestrator.guard_audit"

_configured_handler: logging.Handler | None = None
# log_guard_decision이 run_in_threadpool을 통해 실제 워커 스레드에서 호출되므로,
# 핸들러를 처음 만드는 체크-후-생성 구간이 동시 요청 간 레이스에 노출된다
# (두 스레드가 동시에 None을 보고 각자 FileHandler를 만들어 둘 다 붙는 문제 -
# 실제로 재현됨). 락으로 막는다.
_handler_lock = threading.Lock()


def _log_path() -> Path:
    return Path(os.getenv("GUARD_AUDIT_LOG_PATH", "logs/query_guard_audit.jsonl"))


def reset_for_tests() -> None:
    """테스트에서 GUARD_AUDIT_LOG_PATH/GUARD_AUDIT_ALSO_CONSOLE을 바꾼 뒤
    핸들러를 다시 구성하도록 강제한다."""
    global _configured_handler
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured_handler is not None:
        logger.removeHandler(_configured_handler)
        _configured_handler = None


def _get_logger() -> logging.Logger:
    global _configured_handler
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured_handler is None:
        with _handler_lock:
            # 락을 얻으려고 대기하는 동안 다른 스레드가 이미 만들었을 수 있어
            # 다시 확인한다(더블 체크 락킹) - 그렇지 않으면 락 대기열에 걸린
            # 만큼 핸들러가 중복 생성된다.
            if _configured_handler is None:
                logger.setLevel(logging.INFO)
                logger.propagate = (
                    os.getenv("GUARD_AUDIT_ALSO_CONSOLE", "false").lower() == "true"
                )
                log_path = _log_path()
                log_path.parent.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(log_path, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
                _configured_handler = handler
    return logger


def log_guard_decision(
    *,
    query_type: str,
    intent: str,
    decision: str,
    stage: str,
    reason: str | None,
) -> None:
    """쿼리 가드 판정 하나를 JSON Lines 한 줄로 기록한다."""
    record: dict[str, Any] = {
        "query_type": query_type,
        "intent": intent,
        "decision": decision,
        "stage": stage,
        "reason": reason,
    }
    _get_logger().info(json.dumps(record, ensure_ascii=False))
