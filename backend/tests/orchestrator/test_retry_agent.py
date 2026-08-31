"""retry_agent 공통 재시도 SubGraph의 가드 배선(예외 안전망, 차단 사유 일반화,
스키마 화이트리스트 차단의 재시도 예산 처리)을 직접 검증한다."""

import logging

import pytest

import orchestrator.subgraphs.retry_agent as retry_agent_module
from orchestrator.errors import QueryInfrastructureError
from orchestrator.guards.result import GuardResult
from orchestrator.subgraphs.retry_agent import make_retry_agent_subgraph

logger = logging.getLogger(__name__)


async def test_query_generation_exception_becomes_safe_infrastructure_error() -> None:
    async def generate(state, previous_query, previous_error) -> str:
        raise RuntimeError("provider secret")

    async def execute(query: str) -> list[dict]:
        raise AssertionError("execute must not be called")

    subgraph = make_retry_agent_subgraph(
        logger=logger,
        label="sql_agent",
        generate=generate,
        execute=execute,
        connection_exceptions=(),
        retryable_exceptions=(),
        empty_result_feedback="EMPTY",
    )

    with pytest.raises(QueryInfrastructureError) as exc_info:
        await subgraph.ainvoke(_initial_state())

    assert "provider secret" not in exc_info.value.message


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


def _make_subgraph(
    *, guard, execute=None, connection_exceptions=(), retryable_exceptions=()
):
    async def generate(state, previous_query, previous_error) -> str:
        return "SELECT 1"

    async def default_execute(query: str) -> list[dict]:
        return [{"count": 1}]

    return make_retry_agent_subgraph(
        logger=logger,
        label="test_agent",
        generate=generate,
        execute=execute or default_execute,
        connection_exceptions=connection_exceptions,
        retryable_exceptions=retryable_exceptions,
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
    assert result["failure"]["kind"] == "internal"


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
    assert result["failure"]["code"] == "QUERY_POLICY_BLOCKED"
    assert "secret_schema" not in str(result["failure"])


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


async def test_connection_failure_is_classified_as_infrastructure() -> None:
    class ConnectionFailureError(Exception):
        pass

    async def execute(query: str) -> list[dict]:
        raise ConnectionFailureError("postgresql://secret-host/internal")

    subgraph = _make_subgraph(
        guard=None,
        execute=execute,
        connection_exceptions=(ConnectionFailureError,),
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["failure"]["code"] == "INFRASTRUCTURE_UNAVAILABLE"
    assert result["failure"]["kind"] == "infrastructure"
    assert "secret-host" not in str(result["failure"])


async def test_timeout_failure_is_safe_and_user_correctable() -> None:
    class QueryTimeoutError(Exception):
        pass

    async def execute(query: str) -> list[dict]:
        raise QueryTimeoutError("SELECT secret FROM internal_table timed out")

    subgraph = _make_subgraph(
        guard=None,
        execute=execute,
        retryable_exceptions=(QueryTimeoutError,),
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["attempt_count"] == 3
    assert result["failure"]["code"] == "QUERY_TIMEOUT"
    assert result["failure"]["kind"] == "user_correctable"
    assert "internal_table" not in str(result["failure"])


async def test_explicit_numeric_filter_must_survive_generated_query() -> None:
    generate_calls = 0
    execute_calls = 0

    async def generate(state, previous_query, previous_error) -> str:
        nonlocal generate_calls
        generate_calls += 1
        return "SELECT listprice FROM production.product WHERE productid = 956"

    async def execute(query: str) -> list[dict]:
        nonlocal execute_calls
        execute_calls += 1
        return [{"listPrice": 2384.07}]

    subgraph = make_retry_agent_subgraph(
        logger=logger,
        label="sql_agent",
        generate=generate,
        execute=execute,
        connection_exceptions=(),
        retryable_exceptions=(),
        empty_result_feedback="EMPTY",
        guard=lambda query: GuardResult(True),
    )

    result = await subgraph.ainvoke(
        _initial_state("Touring-1000 Yellow, 54 중 정가가 0원인 제품을 알려줘.")
    )

    assert generate_calls == 3
    assert execute_calls == 0
    assert result["failure"]["code"] == "QUERY_FILTER_DROPPED"
    assert "2384.07" not in str(result["failure"])
