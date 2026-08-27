"""안전성 검사 -> 엔티티 확정 -> self-correction 흐름을 테스트한다."""

import logging
from pathlib import Path

from orchestrator.graph import (
    _PROJECT_ROOT,
    _ontology_path,
    _schema_dir,
    build_orchestrator_graph,
)
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_tool_call_response,
)
from tests.mocks.postgres import MockAsyncPostgresPool

_READ = make_content_response(
    '{"intent":"READ","confidence":0.99,"reason":"조회 요청"}'
)


def test_graph_data_paths_use_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SCHEMA_DIR", "/container/schema-data")
    monkeypatch.setenv(
        "ONTOLOGY_PATH", "/container/ontology-data/manufacturing_terms.yaml"
    )

    assert _schema_dir() == Path("/container/schema-data")
    assert _ontology_path() == Path("/container/ontology-data/manufacturing_terms.yaml")


def test_graph_data_paths_fall_back_to_project_files(monkeypatch) -> None:
    monkeypatch.delenv("SCHEMA_DIR", raising=False)
    monkeypatch.delenv("ONTOLOGY_PATH", raising=False)

    assert _schema_dir() == _PROJECT_ROOT / "schema"
    assert _ontology_path() == _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"


async def test_graph_resolves_entity_then_runs_sql_agent_once() -> None:
    """제품명이 있는 SQL형 질의는 entity 확정 후 sql_agent가 한 번 생성·실행을
    시도한다(execute_sql이 자리표시라 항상 실패하고 error에 담긴다)."""
    openai_client = MockOpenAIClient(
        _READ,
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        ),
        make_content_response('["sql"]'),
        make_content_response(
            "SELECT listprice, standardcost FROM production.product "
            "WHERE productid = 956"
        ),
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    graph = build_orchestrator_graph(openai_client, pool)

    result = await graph.ainvoke(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == (
        "SELECT listprice, standardcost FROM production.product WHERE productid = 956"
    )
    assert result["sql_result"]["result"] is None
    assert "self-correction 구현에서 채운다" in result["sql_result"]["error"]
    assert result["cypher_query"] is None
    assert result["graph_result"] is None
    assert len(openai_client.calls) == 4


async def test_graph_routes_to_graph_and_runs_cypher_agent_once() -> None:
    """부품 사용처를 묻는 질의는 entity 확정 후 graph로 라우팅되고 cypher_agent가
    한 번 생성·실행을 시도한다."""
    openai_client = MockOpenAIClient(
        _READ,
        make_tool_call_response(
            "extract_entity", {"entityType": "product", "entityName": "Paint - Black"}
        ),
        make_content_response('["graph"]'),
        make_content_response(
            "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
            "WHERE part.productId = 492 RETURN parent"
        ),
    )
    pool = MockAsyncPostgresPool(rows_by_name={"Paint - Black": (492, "Paint - Black")})
    graph = build_orchestrator_graph(openai_client, pool)

    result = await graph.ainvoke(
        {"query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘."}
    )

    assert result["entity"] == {"productId": 492, "productName": "Paint - Black"}
    assert result["tool_plan"] == ["graph"]
    assert result["sql_query"] is None
    assert result["sql_result"] is None
    assert result["cypher_query"] == (
        "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
        "WHERE part.productId = 492 RETURN parent"
    )
    assert result["graph_result"]["result"] is None
    assert "self-correction 구현에서 채운다" in result["graph_result"]["error"]
    assert len(openai_client.calls) == 4


async def test_graph_runs_both_agents_independently_for_hybrid_tool_plan() -> None:
    """tool_plan이 ["sql", "graph"] Hybrid일 때 sql_agent와 cypher_agent가 각각
    독립적으로 실행되고, 한쪽의 attempts/error가 다른 쪽으로 섞이지 않는다."""
    openai_client = MockOpenAIClient(
        _READ,
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        ),
        make_content_response('["sql", "graph"]'),
        make_content_response(
            "SELECT listprice, standardcost FROM production.product "
            "WHERE productid = 956"
        ),
        make_content_response(
            "MATCH (p:Product {productId: 956})<-[:SUPPLIES]-(s:Supplier) RETURN s"
        ),
    )
    pool = MockAsyncPostgresPool(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    graph = build_orchestrator_graph(openai_client, pool)

    result = await graph.ainvoke(
        {
            "query": (
                "Touring-1000 Yellow, 54의 정가와 표준원가, "
                "그리고 이 제품에 부품을 공급하는 업체를 알려줘."
            )
        }
    )

    assert result["tool_plan"] == ["sql", "graph"]

    assert result["sql_query"] == (
        "SELECT listprice, standardcost FROM production.product WHERE productid = 956"
    )
    assert result["sql_result"]["result"] is None
    assert "SQL 실행/검증은" in result["sql_result"]["error"]
    assert len(result["sql_result"]["attempts"]) == 1

    assert result["cypher_query"] == (
        "MATCH (p:Product {productId: 956})<-[:SUPPLIES]-(s:Supplier) RETURN s"
    )
    assert result["graph_result"]["result"] is None
    assert "Cypher 실행/검증은" in result["graph_result"]["error"]
    assert len(result["graph_result"]["attempts"]) == 1

    assert len(openai_client.calls) == 5


async def test_graph_builds_final_answer_from_sql_result() -> None:
    """특정 제품을 지칭하지 않는 집계 질의도 sql_agent를 거쳐 final_answer가 채워진다."""
    openai_client = MockOpenAIClient(
        _READ,
        make_content_response("[]"),
        make_content_response('["sql"]'),
        make_content_response("SELECT pg_catalog.count(*) FROM production.product"),
    )
    pool = MockAsyncPostgresPool(rows_by_name={})
    graph = build_orchestrator_graph(openai_client, pool)

    result = await graph.ainvoke({"query": "전체 제품 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == "SELECT pg_catalog.count(*) FROM production.product"
    assert "self-correction 구현에서 채운다" in result["final_answer"]


async def test_graph_stops_before_llm_when_user_requests_write(caplog) -> None:
    caplog.set_level(logging.INFO)
    openai_client = MockOpenAIClient()
    graph = build_orchestrator_graph(
        openai_client, MockAsyncPostgresPool(rows_by_name={})
    )

    result = await graph.ainvoke({"query": "모든 제품을 삭제해줘."})

    assert result["natural_guard"]["decision"] == "BLOCK_WRITE"
    assert result["execution_allowed"] is False
    assert result.get("tool_plan") is None
    assert openai_client.calls == []
    assert "detected_actions" in caplog.text


async def test_graph_stops_before_answer_when_generated_query_is_blocked() -> None:
    unsafe = make_content_response("MATCH (p:Product) DELETE p RETURN p")
    openai_client = MockOpenAIClient(
        _READ,
        make_content_response("[]"),
        make_content_response('["graph"]'),
        unsafe,
        unsafe,
        unsafe,
    )
    graph = build_orchestrator_graph(
        openai_client, MockAsyncPostgresPool(rows_by_name={})
    )

    result = await graph.ainvoke({"query": "제품 관계를 조회해줘."})

    assert result["query_guard"]["decision"] == "BLOCKED"
    assert result["execution_allowed"] is False
    assert result.get("final_answer") is None
    assert "읽기 전용 정책" in result["error"]
