"""쿼리 가드 판정과 답변 검증 결과를 attempts와 분리된 전용 스트림에 감사
로그로 남긴다. 기본은 파일 전용(logs/*.jsonl)이며, *_ALSO_CONSOLE=true면
루트 로거(콘솔)에도 함께 전파한다 - 확장성 논의 결과 파일 전용을 기본으로 채택
(md/2026-08-25-execute_sql_cypher-구현-고려사항-정리.md §5-23)."""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any


class _JsonlAuditLogger:
    """지정된 파일에 JSON Lines로 한 줄씩 남기는 전용 로거를 지연 생성한다.

    핸들러를 처음 만드는 체크-후-생성 구간이 동시 요청 간 레이스에 노출된다
    (두 스레드가 동시에 None을 보고 각자 FileHandler를 만들어 둘 다 붙는 문제 -
    실제로 재현됨). 락으로 막는다."""

    def __init__(
        self, *, logger_name: str, path_env: str, default_path: str, console_env: str
    ) -> None:
        self._logger_name = logger_name
        self._path_env = path_env
        self._default_path = default_path
        self._console_env = console_env
        self._handler: logging.Handler | None = None
        self._lock = threading.Lock()

    def reset_for_tests(self) -> None:
        """테스트에서 경로/콘솔 전파 환경변수를 바꾼 뒤 핸들러를 다시
        구성하도록 강제한다."""
        logger = logging.getLogger(self._logger_name)
        if self._handler is not None:
            logger.removeHandler(self._handler)
            self._handler = None

    def _get_logger(self) -> logging.Logger:
        logger = logging.getLogger(self._logger_name)
        if self._handler is None:
            with self._lock:
                # 락을 얻으려고 대기하는 동안 다른 스레드가 이미 만들었을 수
                # 있어 다시 확인한다(더블 체크 락킹) - 그렇지 않으면 락
                # 대기열에 걸린 만큼 핸들러가 중복 생성된다.
                if self._handler is None:
                    logger.setLevel(logging.INFO)
                    logger.propagate = (
                        os.getenv(self._console_env, "false").lower() == "true"
                    )
                    log_path = Path(os.getenv(self._path_env, self._default_path))
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    handler = logging.FileHandler(log_path, encoding="utf-8")
                    handler.setFormatter(logging.Formatter("%(message)s"))
                    logger.addHandler(handler)
                    self._handler = handler
        return logger

    def log(self, record: dict[str, Any]) -> None:
        self._get_logger().info(json.dumps(record, ensure_ascii=False))


_guard_audit = _JsonlAuditLogger(
    logger_name="orchestrator.guard_audit",
    path_env="GUARD_AUDIT_LOG_PATH",
    default_path="logs/query_guard_audit.jsonl",
    console_env="GUARD_AUDIT_ALSO_CONSOLE",
)
_answer_audit = _JsonlAuditLogger(
    logger_name="orchestrator.answer_audit",
    path_env="ANSWER_AUDIT_LOG_PATH",
    default_path="logs/answer_validation_audit.jsonl",
    console_env="ANSWER_AUDIT_ALSO_CONSOLE",
)


def reset_for_tests() -> None:
    """테스트에서 GUARD_AUDIT_LOG_PATH/GUARD_AUDIT_ALSO_CONSOLE을 바꾼 뒤
    핸들러를 다시 구성하도록 강제한다."""
    _guard_audit.reset_for_tests()


def reset_answer_audit_for_tests() -> None:
    """테스트에서 ANSWER_AUDIT_LOG_PATH/ANSWER_AUDIT_ALSO_CONSOLE을 바꾼 뒤
    핸들러를 다시 구성하도록 강제한다."""
    _answer_audit.reset_for_tests()


def log_guard_decision(
    *,
    query_type: str,
    intent: str,
    decision: str,
    stage: str,
    reason: str | None,
) -> None:
    """쿼리 가드 판정 하나를 JSON Lines 한 줄로 기록한다."""
    _guard_audit.log(
        {
            "query_type": query_type,
            "intent": intent,
            "decision": decision,
            "stage": stage,
            "reason": reason,
        }
    )


def log_answer_validation(
    *,
    stage: str,
    outcome: str,
    reason: str | None,
    detail: list[str] | None,
) -> None:
    """답변 검증(숫자/영문/한국어 엔티티 근거 대조) 결과 하나를 JSON Lines
    한 줄로 기록한다.

    PR #53 리뷰에서 한국어 grounding 검사 재활성화 이후 오탐률·실패 사유별
    빈도를 모니터링할 지표가 필요하다고 지적됐다(가상제품/브레이크패드 같은
    진짜 환각과, 안전재고처럼 정당한 스키마 의역이 잘못 걸리는 오탐을
    구분하려면 실제 거부 사례의 detail을 봐야 한다)."""
    _answer_audit.log(
        {
            "stage": stage,
            "outcome": outcome,
            "reason": reason,
            "detail": detail,
        }
    )
