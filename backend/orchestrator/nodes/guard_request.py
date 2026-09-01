"""사용자 요청 자체의 쓰기 의도를 조회 계획 전에 차단한다."""

import logging
import re
from collections.abc import Awaitable, Callable

from starlette.concurrency import run_in_threadpool

from orchestrator.guards.audit import log_guard_decision
from orchestrator.guards.shared import CYPHER_WRITE_KEYWORDS, SQL_WRITE_KEYWORDS
from orchestrator.query_failures import make_query_failure
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_KOREAN_FORMAL_WRITE_REQUEST = re.compile(
    r"(?:삭제|제거|수정|변경|추가|삽입|생성|초기화|파기|폐기|갱신|등록)"
    r"(?:해\s*줘(?:요)?|해\s*주세요|해라|하라|하세요|해\s*줄래|해\s*주시겠|"
    r"해\s*주십시오|해\s*바랍니다|을\s*부탁|를\s*부탁)"
)
_KOREAN_COLLOQUIAL_WRITE_REQUEST = re.compile(
    r"(?:지워|없애|바꿔|넣어|만들어|덮어\s*써)"
    r"(?:\s*줘(?:요)?|\s*주세요|라|\s*줄래|\s*주시겠|\s*주십시오)?"
    r"(?=\s|[.!?]|$)"
)
_KOREAN_TERSE_WRITE_REQUEST = re.compile(
    r"(?:삭제|제거|수정|변경|추가|삽입|생성|초기화|파기|폐기|갱신|등록)"
    r"\s*(?:요청|실행|처리)?\s*[.!?]?$"
)
_SQL_WRITE_SYNTAX = re.compile(
    r"(?i)\b(?:delete\s+from|update\s+[A-Za-z_][\w.]*\s+set|"
    r"insert\s+into|drop\s+(?:table|database|schema)|truncate\s+(?:table\s+)?|"
    r"alter\s+(?:table|database|schema)|create\s+(?:table|database|schema))\b"
)
_SHARED_WRITE_KEYWORD = re.compile(
    r"(?i)^\s*(?:"
    + "|".join(
        re.escape(keyword)
        for keyword in sorted(SQL_WRITE_KEYWORDS | CYPHER_WRITE_KEYWORDS)
    )
    + r")\b"
)
_ENGLISH_WRITE_REQUEST = re.compile(
    r"(?i)^\s*(?:(?:please|kindly)\s+|(?:can|could|would|will)\s+you\s+)?"
    r"(?:delete|remove|update|insert|drop|truncate|alter|modify|change|add|create|"
    r"reset|destroy)\b"
)


def has_write_intent(query: str) -> bool:
    """서술형 조회는 허용하고 명령형 쓰기 요청과 실제 쓰기 구문을 차단한다."""
    return any(
        pattern.search(query)
        for pattern in (
            _KOREAN_FORMAL_WRITE_REQUEST,
            _KOREAN_COLLOQUIAL_WRITE_REQUEST,
            _KOREAN_TERSE_WRITE_REQUEST,
            _SQL_WRITE_SYNTAX,
            _SHARED_WRITE_KEYWORD,
            _ENGLISH_WRITE_REQUEST,
        )
    )


def make_guard_request_node() -> Callable[[OrchestratorState], Awaitable[dict]]:
    async def guard_request(state: OrchestratorState) -> dict:
        blocked = has_write_intent(state["query"])
        try:
            await run_in_threadpool(
                log_guard_decision,
                query_type="request",
                intent=state["query"],
                decision="BLOCK" if blocked else "ALLOW",
                stage="request_guard",
                reason="WRITE_INTENT_DETECTED" if blocked else None,
            )
        except Exception as exc:
            logger.error("요청 가드 감사 로그 기록 실패(무시하고 계속): %s", exc)
        if not blocked:
            return {"query_failure": None}
        return {
            "query_failure": make_query_failure(
                code="REQUEST_POLICY_BLOCKED",
                stage="validation",
                category="POLICY_BLOCKED",
                kind="user_correctable",
                retryable=False,
                user_safe_reason="데이터를 변경하는 요청은 안전 정책상 실행할 수 없습니다.",
                suggested_action="조회하거나 확인하려는 내용으로 질문을 바꿔 주세요.",
            )
        }

    return guard_request
