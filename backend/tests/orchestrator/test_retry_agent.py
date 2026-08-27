"""retry_agent 공통 재시도 SubGraph의 가드 배선(예외 안전망, 차단 사유 일반화,
스키마 화이트리스트 차단의 재시도 예산 처리)을 직접 검증한다."""

import logging

import orchestrator.subgraphs.retry_agent as retry_agent_module
from orchestrator.guards.result import GuardResult
from orchestrator.subgraphs.retry_agent import make_retry_agent_subgraph

logger = logging.getLogger(__name__)


def _initial_state(query: str = "제품 수를 알려줘.") -> dict:
    return {
        "query": query,
        "entity": None,
        "schema": "production.product {productid: INTEGER}",
        "messages": [],
        "result": None,
        "error": None,
        "attempt_count": 0,
        "attempts": [],
        "empty_retried": False,
        "empty_reason": None,
    }


def _make_subgraph(*, guard, execute=None):
    async def generate(state, previous_query, previous_error) -> str:
        return "SELECT 1"

    async def default_execute(query: str) -> list[dict]:
        return [{"count": 1}]

    return make_retry_agent_subgraph(
        logger=logger,
        label="test_agent",
        generate=generate,
        execute=execute or default_execute,
        connection_exceptions=(),
        retryable_exceptions=(),
        empty_result_feedback="EMPTY",
        guard=guard,
    )


async def test_guard_exception_does_not_propagate_and_is_not_retried() -> None:
    """가드 자체가 예외를 던져도(파싱 버그 등) 그래프 밖으로 raise되지 않고,
    execute도 호출되지 않으며, 재시도 대상으로도 취급되지 않는다."""
    execute_calls = []

    async def execute(query: str) -> list[dict]:
        execute_calls.append(query)
        return [{"count": 1}]

    def broken_guard(query: str) -> GuardResult:
        raise RecursionError("maximum recursion depth exceeded")

    subgraph = _make_subgraph(guard=broken_guard, execute=execute)

    result = await subgraph.ainvoke(_initial_state())

    assert execute_calls == []
    assert result["result"] is None
    assert result["error"] == "쿼리 검증 중 오류가 발생했습니다."
    assert len(result["attempts"]) == 1


async def test_audit_log_failure_does_not_propagate_or_block_guard_decision(
    monkeypatch,
) -> None:
    """감사 로그 기록(log_guard_decision)이 실패해도(디스크 풀/권한 문제 등)
    그래프 밖으로 raise되지 않고, 가드 판정(허용/차단) 자체는 정상적으로
    이어진다 - 감사 로그는 부가 기능이지 보안 결정 자체가 아니다."""

    def broken_log_guard_decision(**kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        retry_agent_module, "log_guard_decision", broken_log_guard_decision
    )

    def guard(query: str) -> GuardResult:
        return GuardResult(True)

    execute_calls = []

    async def execute(query: str) -> list[dict]:
        execute_calls.append(query)
        return [{"count": 1}]

    subgraph = _make_subgraph(guard=guard, execute=execute)

    result = await subgraph.ainvoke(_initial_state())

    assert execute_calls == ["SELECT 1"]
    assert result["result"] == [{"count": 1}]
    assert result["error"] is None


async def test_guard_block_message_hides_reason_detail_but_keeps_reason_code() -> None:
    """차단 사유 상세(reason_detail, 예: 실제 테이블/라벨명)는 API 응답까지
    흘러가는 error에 노출되면 안 되지만, reason_code는 LLM 재시도 피드백을
    위해 남아 있어야 한다."""

    def guard(query: str) -> GuardResult:
        return GuardResult(
            False,
            "UNKNOWN_TABLE",
            "스키마에 없는 테이블 참조: secret_schema.secret_table",
        )

    subgraph = _make_subgraph(guard=guard)

    result = await subgraph.ainvoke(_initial_state())

    assert "UNKNOWN_TABLE" in result["error"]
    assert "secret_schema" not in result["error"]
    assert "secret_table" not in result["error"]


async def test_unknown_table_guard_block_is_not_retried() -> None:
    """스키마 화이트리스트가 원인인 차단(UNKNOWN_TABLE)은 재생성으로 고칠 수 없으므로
    재시도 예산을 낭비하지 않고 1회 시도 후 즉시 종료한다."""
    generate_calls = []
    execute_calls = []

    async def generate(state, previous_query, previous_error) -> str:
        generate_calls.append(previous_error)
        return "SELECT 1"

    async def execute(query: str) -> list[dict]:
        execute_calls.append(query)
        return [{"count": 1}]

    def guard(query: str) -> GuardResult:
        return GuardResult(False, "UNKNOWN_TABLE", "스키마에 없는 테이블 참조: x.y")

    subgraph = make_retry_agent_subgraph(
        logger=logger,
        label="test_agent",
        generate=generate,
        execute=execute,
        connection_exceptions=(),
        retryable_exceptions=(),
        empty_result_feedback="EMPTY",
        guard=guard,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert execute_calls == []
    assert len(generate_calls) == 1
    assert result["attempt_count"] == 1
    assert len(result["attempts"]) == 1


async def test_write_keyword_guard_block_is_still_retried() -> None:
    """쿼리 형태 문제(WRITE_KEYWORD_DETECTED)는 LLM이 재생성으로 고칠 여지가
    있으므로 기존처럼 재시도 대상으로 남는다."""
    call_count = 0

    def guard(query: str) -> GuardResult:
        nonlocal call_count
        call_count += 1
        return GuardResult(False, "WRITE_KEYWORD_DETECTED", "쓰기 키워드 감지: DELETE")

    subgraph = _make_subgraph(guard=guard)

    result = await subgraph.ainvoke(_initial_state())

    assert call_count == 3
    assert result["attempt_count"] == 3
