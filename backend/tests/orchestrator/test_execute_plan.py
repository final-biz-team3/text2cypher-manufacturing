"""검증된 하위 질의 계획의 병렬 wave 실행과 의존성 처리를 테스트한다."""

import asyncio
import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from agents.cypher.schema.models import GraphQueryPolicy, GraphSchema
from orchestrator.nodes.execute_plan import make_execute_plan_node
from orchestrator.planning import Subquery
from orchestrator.query_failures import make_query_failure
from orchestrator.state import QueryFailure
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from tests.mocks.openai import MockOpenAIClient, make_content_response

_TEST_GRAPH_SCHEMA = GraphSchema.model_validate(
    {
        "nodes": {"Product": {"properties": {"productId": {"type": "INTEGER"}}}},
        "relationships": {},
    }
)


class _FakeAgent:
    def __init__(self, *results: dict[str, Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(state)
        return self.results[len(self.calls) - 1]


def _result(
    query: str,
    rows: list[dict[str, Any]] | None,
    *,
    error: str | None = None,
    empty_reason: str | None = None,
    failure: QueryFailure | None = None,
) -> dict[str, Any]:
    return {
        "messages": [{"role": "assistant", "content": query}],
        "result": rows,
        "error": error,
        "attempts": [{"query": query, "error": error}],
        "empty_reason": empty_reason,
        "failure": failure,
    }


def _step(
    subquery_id: str,
    tool: str,
    question: str,
    *,
    depends_on: list[str] | None = None,
    outputs: list[str] | None = None,
    bindings: dict[str, str] | None = None,
) -> Subquery:
    step: Subquery = {
        "id": subquery_id,
        "tool": tool,
        "question": question,
        "dependsOn": depends_on or [],
        "requiredOutputs": outputs or [],
        "joinKeys": [],
    }
    if bindings:
        step["inputBindings"] = bindings
    return step


def _node(sql_agent: _FakeAgent, graph_agent: _FakeAgent):
    return make_execute_plan_node(
        sql_agent=sql_agent,
        cypher_agent=graph_agent,
        sql_schema_text="SQL SCHEMA",
        cypher_schema_text="GRAPH SCHEMA",
    )


@pytest.mark.parametrize(
    ("tool", "query_field", "result_field"),
    [
        ("sql", "sql_query", "sql_result"),
        ("graph", "cypher_query", "graph_result"),
    ],
)
async def test_execute_plan_keeps_single_tool_result_fields(
    tool: str, query_field: str, result_field: str
) -> None:
    """단일 SQL·GRAPH 계획은 기존 query/result 필드에 그대로 대응한다."""
    sql_agent = _FakeAgent(_result("SELECT 1", [{"value": 1}]))
    graph_agent = _FakeAgent(_result("RETURN 1", [{"value": 1}]))
    result = await _node(sql_agent, graph_agent)(
        {
            "query": "원질문",
            "entity": {"productId": 10},
            "subqueries": [_step("only", tool, "하위 질문")],
        }
    )

    assert result[query_field] == ("SELECT 1" if tool == "sql" else "RETURN 1")
    assert result[result_field]["result"] == [{"value": 1}]
    unused_tool = "graph" if tool == "sql" else "sql"
    assert result["cypher_query" if unused_tool == "graph" else "sql_query"] is None


async def test_execute_plan_runs_independent_subqueries_concurrently() -> None:
    """dependsOn이 없는 SQL과 GRAPH는 같은 실행 wave에서 시작한다."""
    both_started = asyncio.Event()
    started: list[str] = []

    class _ConcurrentAgent:
        def __init__(self, name: str, query: str) -> None:
            self.name = name
            self.query = query

        async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
            started.append(self.name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.2)
            return _result(self.query, [{"source": self.name}])

    result = await make_execute_plan_node(
        sql_agent=_ConcurrentAgent("sql", "SELECT 1"),
        cypher_agent=_ConcurrentAgent("graph", "RETURN 1"),
        sql_schema_text="SQL SCHEMA",
        cypher_schema_text="GRAPH SCHEMA",
    )(
        {
            "query": "독립 복합 질문",
            "subqueries": [
                _step("sql_facts", "sql", "수치를 조회한다."),
                _step("graph_paths", "graph", "경로를 조회한다."),
            ],
        }
    )

    assert started == ["sql", "graph"]
    assert result["sql_query"] == "SELECT 1"
    assert result["cypher_query"] == "RETURN 1"


async def test_execute_plan_passes_subquery_context_and_aligned_bindings() -> None:
    """동일 dependency의 binding 배열은 행 순서와 중복을 함께 보존한다."""
    graph_agent = _FakeAgent(
        _result(
            "MATCH path",
            [
                {"componentId": 7, "supplierId": 2},
                {"componentId": 7, "supplierId": 3},
                {"componentId": 9, "supplierId": 3},
            ],
        )
    )
    sql_agent = _FakeAgent(_result("SELECT stock", [{"componentId": 7}]))
    entity = {"supplierId": 2, "supplierName": "테스트 공급업체"}
    result = await _node(sql_agent, graph_agent)(
        {
            "query": "부품별 재고 부족량을 알려줘.",
            "entity": entity,
            "subqueries": [
                _step(
                    "graph_components",
                    "graph",
                    "영향 부품을 찾는다.",
                    outputs=["componentId", "supplierId"],
                ),
                _step(
                    "sql_stock",
                    "sql",
                    "찾은 부품의 재고를 조회한다.",
                    depends_on=["graph_components"],
                    outputs=["componentId"],
                    bindings={
                        "componentIds": "graph_components.componentId",
                        "supplierIds": "graph_components.supplierId",
                    },
                ),
            ],
        }
    )

    assert graph_agent.calls[0]["query"] == "부품별 재고 부족량을 알려줘."
    assert graph_agent.calls[0]["source_scope"] == "영향 부품을 찾는다."
    assert graph_agent.calls[0]["entity"] == entity
    assert graph_agent.calls[0]["required_outputs"] == [
        "componentId",
        "supplierId",
    ]
    assert sql_agent.calls[0]["query"] == "부품별 재고 부족량을 알려줘."
    assert sql_agent.calls[0]["source_scope"] == "찾은 부품의 재고를 조회한다."
    assert sql_agent.calls[0]["entity"] is None
    assert sql_agent.calls[0]["required_outputs"] == ["componentId"]
    assert sql_agent.calls[0]["input_bindings"] == {
        "componentIds": [7, 7, 9],
        "supplierIds": [2, 3, 3],
    }
    assert result["cypher_query"] == "MATCH path"
    assert result["sql_query"] == "SELECT stock"
    assert "subquery_results" not in result


async def test_execute_plan_fails_safely_for_invalid_input_binding_row() -> None:
    graph_agent = _FakeAgent(_result("MATCH path", [{"componentName": "missing id"}]))
    sql_agent = _FakeAgent(_result("SELECT stock", [{"componentId": 7}]))

    result = await _node(sql_agent, graph_agent)(
        {
            "query": "복합 질문",
            "subqueries": [
                _step(
                    "graph_components",
                    "graph",
                    "부품을 찾는다.",
                    outputs=["componentId"],
                ),
                _step(
                    "sql_stock",
                    "sql",
                    "재고를 찾는다.",
                    depends_on=["graph_components"],
                    bindings={"componentIds": "graph_components.componentId"},
                ),
            ],
        }
    )

    assert sql_agent.calls == []
    assert result["sql_query"] is None
    assert result["sql_result"]["result"] is None
    assert result["sql_result"]["error"] == (
        "하위 질의 입력 계획이 유효하지 않아 실행하지 않았습니다."
    )
    assert result["sql_result"]["attempts"] == []
    assert result["sql_result"]["failure"]["code"] == "INPUT_BINDING_INVALID"
    assert result["sql_result"]["failure"]["dependent_failure"] is True
    assert "componentId" not in result["sql_result"]["error"]


async def test_execute_plan_serializes_sql_scalars_for_graph_generation() -> None:
    """SQL의 Decimal·날짜 binding이 Cypher 생성 프롬프트까지 안전하게 전달된다."""
    sql_agent = _FakeAgent(
        _result(
            "SELECT price, as_of",
            [
                {"price": Decimal("12.50"), "asOf": date(2026, 8, 28)},
                {"price": Decimal("12.50"), "asOf": date(2026, 8, 29)},
            ],
        )
    )
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (p:Product) RETURN p")
    )

    async def execute_cypher(cypher: str) -> list[dict[str, Any]]:
        return [{"p": {"productId": 10}}]

    graph_agent = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=GraphQueryPolicy(
            bomAsOfDate="2014-08-08",
            bomMaxDepth=4,
        ),
        graph_schema=_TEST_GRAPH_SCHEMA,
    )
    node = make_execute_plan_node(
        sql_agent=sql_agent,
        cypher_agent=graph_agent,
        sql_schema_text="SQL SCHEMA",
        cypher_schema_text="GRAPH SCHEMA",
    )

    result = await node(
        {
            "query": "SQL 조건으로 그래프를 찾는다.",
            "subqueries": [
                _step(
                    "sql_values",
                    "sql",
                    "가격과 기준일을 찾는다.",
                    outputs=["price", "asOf"],
                ),
                _step(
                    "graph_products",
                    "graph",
                    "가격과 기준일에 해당하는 제품 관계를 찾는다.",
                    depends_on=["sql_values"],
                    bindings={
                        "prices": "sql_values.price",
                        "asOfDates": "sql_values.asOf",
                    },
                ),
            ],
        }
    )

    graph_user_message = next(
        message["content"]
        for message in openai_client.calls[0]["messages"]
        if message["role"] == "user"
    )
    assert json.loads(graph_user_message)["inputBindings"] == {
        "prices": ["12.50", "12.50"],
        "asOfDates": ["2026-08-28", "2026-08-29"],
    }
    assert result["cypher_query"] == "MATCH (p:Product) RETURN p"
    assert result["graph_result"]["error"] is None


@pytest.mark.parametrize("upstream_rows", [None, []])
async def test_execute_plan_skips_dependent_after_failure_or_empty_result(
    upstream_rows: list[dict[str, Any]] | None,
) -> None:
    """실패 또는 빈 선행 결과가 있으면 의존 단계의 광범위 조회를 막는다."""
    error = "graph failed" if upstream_rows is None else None
    graph_agent = _FakeAgent(_result("MATCH bad", upstream_rows, error=error))
    sql_agent = _FakeAgent(_result("SELECT broad", [{"componentId": 1}]))
    result = await _node(sql_agent, graph_agent)(
        {
            "query": "복합 질문",
            "subqueries": [
                _step(
                    "graph_components",
                    "graph",
                    "부품을 찾는다.",
                    outputs=["componentId"],
                ),
                _step(
                    "sql_stock",
                    "sql",
                    "재고를 찾는다.",
                    depends_on=["graph_components"],
                    bindings={"ids": "graph_components.componentId"},
                ),
            ],
        }
    )

    assert sql_agent.calls == []
    assert result["sql_query"] is None
    if upstream_rows is None:
        assert result["sql_result"]["result"] is None
        assert "선행 하위 질의" in result["sql_result"]["error"]
    else:
        assert result["sql_result"]["result"] == []
        assert result["sql_result"]["error"] is None


async def test_execute_plan_preserves_inconclusive_empty_dependency() -> None:
    """오류 후 빈 결과를 데이터가 없다는 확정 결과로 바꾸지 않는다."""
    graph_agent = _FakeAgent(_result("MATCH empty", [], empty_reason="INCONCLUSIVE"))
    sql_agent = _FakeAgent(_result("SELECT broad", [{"componentId": 1}]))
    result = await _node(sql_agent, graph_agent)(
        {
            "query": "복합 질문",
            "subqueries": [
                _step(
                    "graph_components",
                    "graph",
                    "부품을 찾는다.",
                    outputs=["componentId"],
                ),
                _step(
                    "sql_stock",
                    "sql",
                    "재고를 찾는다.",
                    depends_on=["graph_components"],
                    bindings={"ids": "graph_components.componentId"},
                ),
            ],
        }
    )

    assert sql_agent.calls == []
    assert result["graph_result"]["empty_reason"] == "INCONCLUSIVE"
    assert result["sql_result"]["empty_reason"] == "INCONCLUSIVE"


async def test_execute_plan_continues_independent_step_after_failure() -> None:
    """실패한 단계에 의존하지 않는 다른 도구 단계는 계속 실행한다."""
    graph_agent = _FakeAgent(_result("MATCH bad", None, error="graph failed"))
    sql_agent = _FakeAgent(_result("SELECT count", [{"count": 3}]))
    result = await _node(sql_agent, graph_agent)(
        {
            "query": "독립 복합 질문",
            "subqueries": [
                _step("graph_paths", "graph", "경로를 찾는다."),
                _step("sql_count", "sql", "개수를 센다."),
            ],
        }
    )

    assert len(sql_agent.calls) == 1
    assert result["graph_result"]["error"] == "graph failed"
    assert result["sql_result"]["result"] == [{"count": 3}]


async def test_execute_plan_preserves_primary_safe_failure_for_answer_node() -> None:
    primary_failure = make_query_failure(
        code="QUERY_TIMEOUT",
        stage="execution",
        category="TIMEOUT",
        kind="user_correctable",
        retryable=True,
        user_safe_reason="조회가 제한 시간 안에 완료되지 않았습니다.",
        suggested_action="조회 범위를 줄여 주세요.",
        failed_tool="graph",
    )
    graph_agent = _FakeAgent(
        _result(
            "MATCH secret",
            None,
            error="raw database error",
            failure=primary_failure,
        )
    )
    sql_agent = _FakeAgent(_result("SELECT broad", [{"value": 1}]))

    result = await _node(sql_agent, graph_agent)(
        {
            "query": "복합 질문",
            "subqueries": [
                _step("graph_first", "graph", "선행 조회"),
                _step(
                    "sql_second",
                    "sql",
                    "후속 조회",
                    depends_on=["graph_first"],
                ),
            ],
        }
    )

    assert result["query_failure"] == primary_failure
    assert "raw database error" not in str(result["query_failure"])
    assert result["sql_result"]["failure"]["code"] == "DEPENDENCY_FAILED"


async def test_execute_plan_prioritizes_infrastructure_over_user_failure() -> None:
    user_failure = make_query_failure(
        code="QUERY_CONTRACT_FAILED",
        stage="validation",
        category="QUERY_INVALID",
        kind="user_correctable",
        retryable=False,
        user_safe_reason="조건을 구성하지 못했습니다.",
        suggested_action="조건을 구체화해 주세요.",
        failed_tool="sql",
    )
    infrastructure_failure = make_query_failure(
        code="INFRASTRUCTURE_UNAVAILABLE",
        stage="execution",
        category="CONNECTION_ERROR",
        kind="infrastructure",
        retryable=True,
        user_safe_reason="조회 시스템에 연결할 수 없습니다.",
        suggested_action="잠시 후 다시 시도해 주세요.",
        failed_tool="graph",
    )
    sql_agent = _FakeAgent(
        _result("SELECT bad", None, error="bad query", failure=user_failure)
    )
    graph_agent = _FakeAgent(
        _result(
            "MATCH bad",
            None,
            error="connection failed",
            failure=infrastructure_failure,
        )
    )

    result = await _node(sql_agent, graph_agent)(
        {
            "query": "독립 복합 질문",
            "subqueries": [
                _step("sql_first", "sql", "첫 조회"),
                _step("graph_second", "graph", "둘째 조회"),
            ],
        }
    )

    assert result["query_failure"] == infrastructure_failure
