"""SQL Agent SubGraph의 생성-실행과 self-correction 재시도를 테스트한다."""

import psycopg

from agents.sql.schema.models import SqlSchema
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph
from tests.mocks.openai import MockOpenAIClient, make_content_response

_TEST_SQL_SCHEMA = SqlSchema.model_validate(
    {
        "tables": {
            "production.product": {"columns": {"productid": {"type": "INTEGER"}}}
        },
        "joins": [],
    }
)


def _initial_state(query: str = "제품 수를 알려줘.") -> dict:
    return {
        "query": query,
        "entity": None,
        "schema": "production.product {productid: INTEGER}",
        "messages": [],
        "result": None,
        "error": None,
    }


async def test_sql_agent_returns_result_when_execution_succeeds() -> None:
    """실행이 성공하면 result를 채우고 error는 None이다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT COUNT(*) FROM production.product")
    )

    async def execute_sql(sql: str) -> list[dict]:
        return [{"count": 10}]

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == [{"count": 10}]
    assert result["error"] is None
    assert (
        result["messages"][-1]["content"] == "SELECT COUNT(*) FROM production.product"
    )
    assert len(openai_client.calls) == 1


async def test_sql_agent_preserves_executor_truncation_metadata() -> None:
    """production executor의 행 초과 신호를 실행 계획까지 보존한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT productid FROM production.product")
    )

    async def execute_sql(sql: str) -> dict:
        return {"rows": [{"productId": 10}], "truncated": True}

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )
    state = _initial_state()
    state["required_outputs"] = ["productId"]

    result = await subgraph.ainvoke(state)

    assert result["result"] == [{"productId": 10}]
    assert result["truncated"] is True


async def test_sql_agent_returns_error_when_execution_fails() -> None:
    """실행이 실패하면 예외를 전파하지 않고 error 필드에 담아 정상 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT bad_column FROM production.product")
    )

    async def execute_sql(sql: str) -> None:
        raise ValueError("column bad_column does not exist")

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "column bad_column does not exist"
    assert len(openai_client.calls) == 1


async def test_sql_agent_retries_after_retryable_error_then_succeeds() -> None:
    """실행 오류(화이트리스트)가 나면 쿼리를 재생성해 재시도하고, 성공하면 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT bad_column FROM production.product"),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    calls = []

    async def execute_sql(sql: str) -> list[dict]:
        calls.append(sql)
        if len(calls) == 1:
            raise psycopg.errors.UndefinedColumn("column bad_column does not exist")
        return [{"count": 10}]

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == [{"count": 10}]
    assert result["error"] is None
    assert len(openai_client.calls) == 2
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["error"] == "column bad_column does not exist"
    assert result["attempts"][1]["error"] is None


async def test_sql_agent_retries_after_postgresql_42p10_then_succeeds() -> None:
    """SELECT DISTINCT와 ORDER BY 투영 불일치(42P10)를 보정해 재실행한다."""
    first_sql = "SELECT DISTINCT productid FROM production.product ORDER BY name"
    corrected_sql = (
        "SELECT DISTINCT productid, name FROM production.product ORDER BY name"
    )
    error_message = (
        "for SELECT DISTINCT, ORDER BY expressions must appear in select list"
    )
    openai_client = MockOpenAIClient(
        make_content_response(first_sql),
        make_content_response(corrected_sql),
    )
    executor_calls = []

    async def execute_sql(sql: str) -> list[dict]:
        executor_calls.append(sql)
        if len(executor_calls) == 1:
            raise psycopg.errors.InvalidColumnReference(error_message)
        return [{"productid": 10, "name": "Widget"}]

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert len(openai_client.calls) == 2
    assert executor_calls == [first_sql, corrected_sql]
    retry_system_prompt = openai_client.calls[1]["messages"][0]["content"]
    assert first_sql in retry_system_prompt
    assert error_message in retry_system_prompt
    assert result["attempts"] == [
        {"query": first_sql, "error": error_message},
        {"query": corrected_sql, "error": None},
    ]
    assert result["retryDiagnostics"] == [
        {
            "stage": "execution",
            "reasonCode": "QUERY_EXECUTION_FAILED",
            "errorType": "InvalidColumnReference",
            "sqlstate": "42P10",
            "recovered": True,
        }
    ]
    assert result["result"] == [{"productid": 10, "name": "Widget"}]
    assert result["error"] is None


async def test_sql_agent_retries_when_required_alias_is_missing() -> None:
    """비어 있지 않은 결과의 모든 행에 필수 alias가 있을 때까지 재생성한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT productid FROM production.product"),
        make_content_response(
            'SELECT productid AS "productId" FROM production.product'
        ),
    )
    calls = []

    async def execute_sql(sql: str) -> list[dict]:
        calls.append(sql)
        if len(calls) == 1:
            return [{"productId": 10}, {"productid": 11}]
        return [{"productId": 10}, {"productId": 11}]

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )
    state = _initial_state()
    state["required_outputs"] = ["productId"]

    result = await subgraph.ainvoke(state)

    assert result["result"] == [{"productId": 10}, {"productId": 11}]
    assert len(result["attempts"]) == 2
    assert "1번 행" in result["attempts"][0]["error"]
    assert "productId" in result["attempts"][0]["error"]
    retry_system_prompt = openai_client.calls[1]["messages"][0]["content"]
    assert "필수 alias" in retry_system_prompt


async def test_sql_agent_retries_after_query_canceled_then_succeeds() -> None:
    """QueryCanceled(statement_timeout 등)는 OperationalError의 서브클래스지만
    실행 오류(재시도 대상)로 분류돼야 하며, 접속 오류로 오분류되어 즉시
    종료되면 안 된다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT * FROM production.product"),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    calls = []

    async def execute_sql(sql: str) -> list[dict]:
        calls.append(sql)
        if len(calls) == 1:
            raise psycopg.errors.QueryCanceled("canceling statement due to timeout")
        return [{"count": 10}]

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == [{"count": 10}]
    assert result["error"] is None
    assert len(openai_client.calls) == 2


async def test_sql_agent_does_not_retry_on_connection_error() -> None:
    """접속(인프라) 오류는 쿼리를 재생성해도 해결되지 않으므로 재시도하지 않는다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT COUNT(*) FROM production.product")
    )

    async def execute_sql(sql: str) -> None:
        raise psycopg.OperationalError("connection refused")

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "접속 오류가 발생했습니다."
    assert len(openai_client.calls) == 1


async def test_sql_agent_stops_after_max_attempts_exceeded() -> None:
    """실행 오류가 계속되면 원본 1회 + 재시도 2회(총 3회)까지만 시도하고 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT a FROM production.product"),
        make_content_response("SELECT b FROM production.product"),
        make_content_response("SELECT c FROM production.product"),
    )

    async def execute_sql(sql: str) -> None:
        raise psycopg.errors.UndefinedColumn("column does not exist")

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "column does not exist"
    assert result["attempt_count"] == 3
    assert len(openai_client.calls) == 3
    assert len(result["attempts"]) == 3


async def test_sql_agent_retries_once_on_empty_result_then_accepts() -> None:
    """빈 결과는 1회만 재시도하고, 재시도 후에도 비면 정답으로 받아들인다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT * FROM production.product WHERE 1=0"),
        make_content_response("SELECT * FROM production.product WHERE 1=0"),
    )

    async def execute_sql(sql: str) -> list:
        return []

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == []
    assert result["error"] is None
    assert result["empty_reason"] == "NO_DATA"
    assert len(openai_client.calls) == 2


async def test_sql_agent_marks_empty_result_inconclusive_after_budget_exhausted() -> (
    None
):
    """실행 오류로 재시도 예산을 다 쓴 뒤 마지막 시도가 빈 결과면, 빈 결과를
    재시도해볼 기회조차 없었으므로 정답으로 확신하지 않고 INCONCLUSIVE로
    표시한다(그리고 내부용 EMPTY_RESULT 문자열이 error로 새어나가면 안 된다)."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT bad_column FROM production.product"),
        make_content_response("SELECT bad_column2 FROM production.product"),
        make_content_response("SELECT * FROM production.product WHERE 1=0"),
    )
    calls = []

    async def execute_sql(sql: str) -> list:
        calls.append(sql)
        if len(calls) <= 2:
            raise psycopg.errors.UndefinedColumn("bad column")
        return []

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == []
    assert result["error"] is None
    assert result["empty_reason"] == "INCONCLUSIVE"
    assert result["attempt_count"] == 3
    assert len(result["attempts"]) == 3


async def test_sql_agent_blocks_write_query_before_execution() -> None:
    """가드가 쓰기 절을 감지하면 execute_sql을 호출하지 않고 재시도 피드백을 준다."""
    openai_client = MockOpenAIClient(
        make_content_response("DELETE FROM production.product"),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    execute_calls = []

    async def execute_sql(sql: str) -> list[dict]:
        execute_calls.append(sql)
        return [{"count": 10}]

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert execute_calls == ["SELECT COUNT(*) FROM production.product"]
    assert result["result"] == [{"count": 10}]
    assert len(result["attempts"]) == 2
    assert "WRITE_KEYWORD_DETECTED" in result["attempts"][0]["error"]


async def test_sql_agent_does_not_retry_unknown_table_guard_block() -> None:
    """화이트리스트에 없는 테이블 참조는 재생성해도 같은 결론에 도달하므로
    재시도 예산을 낭비하지 않고 1회만에 종료한다(스키마명은 응답에 노출 안 됨)."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT * FROM production.unknown_table")
    )
    execute_calls = []

    async def execute_sql(sql: str) -> list[dict]:
        execute_calls.append(sql)
        return [{"count": 10}]

    subgraph = make_sql_agent_subgraph(
        openai_client, execute_sql=execute_sql, sql_schema=_TEST_SQL_SCHEMA
    )

    result = await subgraph.ainvoke(_initial_state())

    assert execute_calls == []
    assert result["result"] is None
    assert len(openai_client.calls) == 1
    assert "UNKNOWN_TABLE" in result["error"]
    assert "unknown_table" not in result["error"]
