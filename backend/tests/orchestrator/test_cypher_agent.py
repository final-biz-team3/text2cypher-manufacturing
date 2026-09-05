"""Cypher Agent SubGraph의 생성-실행과 self-correction 재시도를 테스트한다."""

from pathlib import Path
from typing import cast

from neo4j.exceptions import ClientError, CypherSyntaxError, ServiceUnavailable

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphQueryPolicy, GraphSchema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.output_catalog import build_output_catalog
from orchestrator.subgraphs.cypher_agent import (
    _query_contract_error,
    _result_contract_error,
    make_cypher_agent_subgraph,
)
from orchestrator.subgraphs.retry_agent import RetryAgentState
from tests.mocks.openai import MockOpenAIClient, make_content_response

QUERY_POLICY = GraphQueryPolicy(bomAsOfDate="2014-08-08", bomMaxDepth=4)

_TEST_GRAPH_SCHEMA = GraphSchema.model_validate(
    {
        "nodes": {
            "Product": {"properties": {"productId": {"type": "INTEGER"}}},
            "Supplier": {"properties": {"supplierId": {"type": "INTEGER"}}},
        },
        "relationships": {
            "SUPPLIES": {"from": "Supplier", "to": "Product", "properties": {}},
            "REQUIRES_COMPONENT": {
                "from": "Product",
                "to": "Product",
                "properties": {},
            },
        },
    }
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SEMANTIC_CATALOG = build_output_catalog(
    load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml"),
    load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml"),
)


def test_required_output_preflight_treats_alias_keys_as_case_sensitive() -> None:
    assert (
        _query_contract_error(
            "MATCH (p:Product) RETURN p.productId AS productid", ["productId"]
        )
        == "RETURN에 필수 alias가 없습니다: productId"
    )


def _initial_state(query: str = "부품 사용처를 알려줘.") -> dict:
    return {
        "query": query,
        "entity": None,
        "schema": "(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        "messages": [],
        "result": None,
        "error": None,
    }


async def test_cypher_agent_returns_result_when_execution_succeeds() -> None:
    """실행이 성공하면 result를 채우고 error는 None이다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n")
    )

    async def execute_cypher(cypher: str) -> list[dict]:
        return [{"n": "x"}]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == [{"n": "x"}]
    assert result["error"] is None
    assert result["messages"][-1]["content"] == "MATCH (n:Product) RETURN n"
    assert len(openai_client.calls) == 1


async def test_cypher_agent_returns_error_when_execution_fails() -> None:
    """실행이 실패하면 예외를 전파하지 않고 error 필드에 담아 정상 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n")
    )

    async def execute_cypher(cypher: str) -> None:
        raise ValueError("unknown property")

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "질의 실행 중 내부 오류가 발생했습니다."
    assert len(openai_client.calls) == 1


async def test_cypher_agent_retries_after_retryable_error_then_succeeds() -> None:
    """실행 오류(화이트리스트)가 나면 쿼리를 재생성해 재시도하고, 성공하면 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) WHERE n.bad RETURN n"),
        make_content_response("MATCH (n:Product) RETURN n"),
    )
    calls = []

    async def execute_cypher(cypher: str) -> list[dict]:
        calls.append(cypher)
        if len(calls) == 1:
            raise CypherSyntaxError("Invalid input 'bad'")
        return [{"n": "x"}]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == [{"n": "x"}]
    assert result["error"] is None
    assert len(openai_client.calls) == 2
    assert len(result["attempts"]) == 2


async def test_cypher_agent_does_not_retry_on_connection_error() -> None:
    """접속(인프라) 오류는 쿼리를 재생성해도 해결되지 않으므로 재시도하지 않는다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n")
    )

    async def execute_cypher(cypher: str) -> None:
        raise ServiceUnavailable("could not connect to server")

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "접속 오류가 발생했습니다."
    assert len(openai_client.calls) == 1


async def test_cypher_agent_stops_after_max_attempts_exceeded() -> None:
    """실행 오류가 계속되면 원본 1회 + 재시도 2회(총 3회)까지만 시도하고 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n"),
        make_content_response("MATCH (n:Product) RETURN n"),
        make_content_response("MATCH (n:Product) RETURN n"),
    )

    async def execute_cypher(cypher: str) -> None:
        raise CypherSyntaxError("invalid syntax")

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "쿼리를 실행하지 못했습니다."
    assert result["attempt_count"] == 3
    assert len(openai_client.calls) == 3
    assert len(result["attempts"]) == 3


async def test_cypher_agent_retries_once_on_empty_result_then_accepts() -> None:
    """빈 결과는 1회만 재시도하고, 재시도 후에도 비면 정답으로 받아들인다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product {id: -1}) RETURN n"),
        make_content_response("MATCH (n:Product {id: -1}) RETURN n"),
    )

    async def execute_cypher(cypher: str) -> list:
        return []

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == []
    assert result["error"] is None
    assert result["empty_reason"] == "NO_DATA"
    assert len(openai_client.calls) == 2


async def test_cypher_agent_marks_empty_result_inconclusive_after_budget_exhausted() -> (
    None
):
    """실행 오류로 재시도 예산을 다 쓴 뒤 마지막 시도가 빈 결과면, 빈 결과를
    재시도해볼 기회조차 없었으므로 정답으로 확신하지 않고 INCONCLUSIVE로
    표시한다(그리고 내부용 EMPTY_RESULT 문자열이 error로 새어나가면 안 된다)."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n"),
        make_content_response("MATCH (n:Product) RETURN n"),
        make_content_response("MATCH (n:Product {id: -1}) RETURN n"),
    )
    calls = []

    async def execute_cypher(cypher: str) -> list:
        calls.append(cypher)
        if len(calls) <= 2:
            raise CypherSyntaxError("invalid syntax")
        return []

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == []
    assert result["error"] is None
    assert result["empty_reason"] == "INCONCLUSIVE"
    assert result["attempt_count"] == 3
    assert len(result["attempts"]) == 3


async def test_cypher_agent_retries_after_query_timeout_then_succeeds() -> None:
    """Neo4j 쿼리 타임아웃(ClientError, 특정 neo4j_code)은 재시도 대상이다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) RETURN n LIMIT 999999999"),
        make_content_response("MATCH (n:Product) RETURN n LIMIT 10"),
    )
    calls = []

    async def execute_cypher(cypher: str) -> list[dict]:
        calls.append(cypher)
        if len(calls) == 1:
            # ClientError.code는 읽기 전용 프로퍼티라(생성자 인자도 없음)
            # 직접 대입이 안 된다 - 서버 응답을 파싱할 때 채워지는
            # 내부 속성(_neo4j_code)에 직접 값을 넣어 재현한다.
            error = ClientError("The transaction has been terminated...")
            error._neo4j_code = (
                "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"
            )
            raise error
        return [{"n": "x"}]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["result"] == [{"n": "x"}]
    assert result["error"] is None
    assert len(openai_client.calls) == 2


async def test_cypher_agent_blocks_write_query_before_execution() -> None:
    """가드가 쓰기 절을 감지하면 execute_cypher를 호출하지 않고 재시도 피드백을 준다."""
    openai_client = MockOpenAIClient(
        make_content_response("MATCH (n:Product) DETACH DELETE n"),
        make_content_response("MATCH (n:Product) RETURN n"),
    )
    execute_calls = []

    async def execute_cypher(cypher: str) -> list[dict]:
        execute_calls.append(cypher)
        return [{"n": "x"}]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert execute_calls == ["MATCH (n:Product) RETURN n"]
    assert result["result"] == [{"n": "x"}]
    assert len(result["attempts"]) == 2
    assert "WRITE_KEYWORD_DETECTED" in result["attempts"][0]["error"]


async def test_cypher_agent_repairs_missing_required_return_alias_before_execution() -> (
    None
):
    openai_client = MockOpenAIClient(
        make_content_response(
            "MATCH (root:Product), (component:Product) "
            "RETURN root.productId AS rootProductId"
        ),
        make_content_response(
            "MATCH (root:Product), (component:Product) "
            "RETURN root.productId AS rootProductId, "
            "component.productId AS componentId"
        ),
    )
    execute_calls = []

    async def execute_cypher(cypher: str) -> list[dict]:
        execute_calls.append(cypher)
        return [{"rootProductId": 8101, "componentId": 8102}]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )
    state = {
        **_initial_state(),
        "required_outputs": ["rootProductId", "componentId"],
    }

    result = await subgraph.ainvoke(state)

    assert result["error"] is None
    assert execute_calls == [result["messages"][-1]["content"]]
    assert len(openai_client.calls) == 2
    assert result["attempts"][0]["error"] == (
        "RETURN에 필수 alias가 없습니다: componentId"
    )


async def test_cypher_agent_repairs_coupled_independent_bom_paths_before_execution() -> (
    None
):
    coupled = (
        "MATCH pA = (a:Product)-[:REQUIRES_COMPONENT*1..4]->(c:Product), "
        "pB = (b:Product)-[:REQUIRES_COMPONENT*1..4]->(c) "
        "RETURN c.productId AS componentId"
    )
    separated = (
        "MATCH pA = (a:Product)-[:REQUIRES_COMPONENT*1..4]->(c:Product) "
        "WITH a, c, min(length(pA)) AS minDepthA "
        "MATCH pB = (b:Product)-[:REQUIRES_COMPONENT*1..4]->(c) "
        "RETURN c.productId AS componentId"
    )
    openai_client = MockOpenAIClient(
        make_content_response(coupled),
        make_content_response(separated),
    )
    execute_calls: list[str] = []

    async def execute_cypher(cypher: str) -> list[dict]:
        execute_calls.append(cypher)
        return [{"componentId": 8102}]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(
        _initial_state("두 제품의 공통 하위 부품을 알려줘.")
    )

    assert result["error"] is None
    assert execute_calls == [separated]
    assert len(openai_client.calls) == 2
    assert "각 anchor 경로를 별도의 MATCH 절" in result["attempts"][0]["error"]


async def test_cypher_agent_repairs_relationship_list_used_as_path_before_execution() -> (
    None
):
    invalid = (
        "MATCH (a:Product)-[path:REQUIRES_COMPONENT*1..4]->(c:Product) "
        "WHERE all(r IN relationships(path) WHERE r.startDate IS NOT NULL) "
        "RETURN c.productId AS componentId"
    )
    repaired = (
        "MATCH path = (a:Product)-[:REQUIRES_COMPONENT*1..4]->(c:Product) "
        "WHERE all(r IN relationships(path) WHERE r.startDate IS NOT NULL) "
        "RETURN c.productId AS componentId"
    )
    openai_client = MockOpenAIClient(
        make_content_response(invalid),
        make_content_response(repaired),
    )
    execute_calls: list[str] = []

    async def execute_cypher(cypher: str) -> list[dict]:
        execute_calls.append(cypher)
        return [{"componentId": 8102}]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
    )

    result = await subgraph.ainvoke(_initial_state())

    assert result["error"] is None
    assert execute_calls == [repaired]
    assert len(openai_client.calls) == 2
    assert "대괄호 안 변수는 Path가 아니라 관계 List" in result["attempts"][0]["error"]


def test_bom_path_result_invariant_rejects_invalid_depth_and_path_lengths() -> None:
    state = cast(
        RetryAgentState,
        {
            **_initial_state(),
            "required_outputs": [
                "rootProductId",
                "rootProductName",
                "componentId",
                "componentName",
                "depth",
                "pathProductIds",
                "pathProductNames",
            ],
        },
    )
    valid = {
        "rootProductId": 1,
        "rootProductName": "Root",
        "componentId": 2,
        "componentName": "Part",
        "depth": 1,
        "pathProductIds": [1, 2],
        "pathProductNames": ["Root", "Part"],
    }

    assert _result_contract_error([valid], state, _SEMANTIC_CATALOG) is None
    for invalid in (
        {**valid, "depth": True},
        {**valid, "depth": 0},
        {**valid, "pathProductIds": [1]},
        {**valid, "pathProductNames": ["Root"]},
    ):
        assert _result_contract_error([invalid], state, _SEMANTIC_CATALOG) is not None


async def test_cypher_agent_repairs_invalid_bom_path_result_locally() -> None:
    first = (
        "MATCH p = (root:Product)-[:REQUIRES_COMPONENT*1..4]->(component:Product) "
        "RETURN root.productId AS rootProductId, root.name AS rootProductName, "
        "component.productId AS componentId, component.name AS componentName, "
        "length(p) AS depth, [n IN nodes(p) | n.productId] AS pathProductIds, "
        "[n IN nodes(p) | n.name] AS pathProductNames"
    )
    repaired = first + " ORDER BY depth"
    openai_client = MockOpenAIClient(
        make_content_response(first),
        make_content_response(repaired),
    )
    execution_count = 0

    async def execute_cypher(cypher: str) -> list[dict]:
        nonlocal execution_count
        execution_count += 1
        return [
            {
                "rootProductId": 1,
                "rootProductName": "Root",
                "componentId": 2,
                "componentName": "Part",
                "depth": 0 if execution_count == 1 else 1,
                "pathProductIds": [1, 2],
                "pathProductNames": ["Root", "Part"],
            }
        ]

    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=execute_cypher,
        query_policy=QUERY_POLICY,
        graph_schema=_TEST_GRAPH_SCHEMA,
        semantic_catalog=_SEMANTIC_CATALOG,
    )
    state = {
        **_initial_state(),
        "required_outputs": [
            "rootProductId",
            "rootProductName",
            "componentId",
            "componentName",
            "depth",
            "pathProductIds",
            "pathProductNames",
        ],
    }

    result = await subgraph.ainvoke(state)

    assert execution_count == 2
    assert len(openai_client.calls) == 2
    assert result["attempts"][0]["error"].endswith("depth는 최소 1 이상이어야 합니다.")
    assert result["attempts"][1]["error"] is None
    assert result["retryDiagnostics"][0]["stage"] == "result_invariant"
    assert result["retryDiagnostics"][0]["recovered"] is True
