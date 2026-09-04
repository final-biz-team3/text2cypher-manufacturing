"""평가 질의 유형과 독립적인 route node 경계를 테스트한다."""

import json
from functools import lru_cache
from pathlib import Path

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.nodes.route_query import RoutePlanError, make_route_query_node
from orchestrator.output_catalog import build_output_catalog
from orchestrator.semantic_catalog import QuerySemanticCatalog
from tests.mocks.openai import MockOpenAIClient, make_content_response

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@lru_cache
def _catalog() -> QuerySemanticCatalog:
    return build_output_catalog(
        load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml"),
        load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml"),
    )


def _subquery(
    subquery_id: str,
    tool: str,
    *,
    question: str = "요청한 사실을 조회한다.",
    depends_on: list[str] | None = None,
    join_keys: list[str] | None = None,
    bindings: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "id": subquery_id,
        "tool": tool,
        "question": question,
        "dependsOn": depends_on or [],
        "joinKeys": join_keys or [],
        "inputBindings": bindings or [],
    }


def _response(
    subqueries: list[dict[str, object]], transform: dict[str, object] | None = None
) -> str:
    return json.dumps(
        {"subqueries": subqueries, "resultTransform": transform},
        ensure_ascii=False,
    )


async def test_route_query_derives_single_source_plan() -> None:
    raw = _response([_subquery("sql_facts", "sql")])
    client = MockOpenAIClient(make_content_response(raw))

    result = await make_route_query_node(client, catalog=_catalog())(
        {"query": "가상 제품의 색상을 알려줘.", "entity": None}
    )

    assert result["tool_plan"] == ["sql"]
    assert result["routeDraft"]["tool_plan"] == ["sql"]
    assert result["rawRouteDraft"] == json.loads(raw)
    assert "tool_plan" not in result["rawRouteDraft"]
    assert result["rawRouteDraft"]["subqueries"][0]["question"] == (
        "요청한 사실을 조회한다."
    )
    assert result["routeDraft"]["subqueries"][0]["question"] == (
        "가상 제품의 색상을 알려줘."
    )


async def test_route_query_derives_dependency_order_and_aligned_bindings() -> None:
    raw = _response(
        [
            _subquery(
                "sql_facts",
                "sql",
                depends_on=["graph_parts"],
                bindings=[
                    {
                        "target": "componentIds",
                        "sourceSubqueryId": "graph_parts",
                        "sourceOutput": "componentId",
                    },
                    {
                        "target": "quantities",
                        "sourceSubqueryId": "graph_parts",
                        "sourceOutput": "quantityPerAssembly",
                    },
                ],
            ),
            _subquery("graph_parts", "graph"),
        ]
    )
    client = MockOpenAIClient(make_content_response(raw))

    result = await make_route_query_node(client, catalog=_catalog())(
        {"query": "가상 조립품의 부품별 값을 계산해줘.", "entity": None}
    )

    assert result["tool_plan"] == ["graph", "sql"]
    consumer = result["routeDraft"]["subqueries"][1]
    assert consumer["inputBindings"] == {
        "componentIds": "graph_parts.componentId",
        "quantities": "graph_parts.quantityPerAssembly",
    }
    assert consumer["joinKeys"] == []


async def test_route_prompt_uses_physical_and_semantic_capabilities() -> None:
    raw = _response([_subquery("graph_facts", "graph")])
    client = MockOpenAIClient(make_content_response(raw))
    node = make_route_query_node(
        client,
        reasoning_effort="high",
        catalog=_catalog(),
        sql_schema_text="SQL_PHYSICAL_SENTINEL",
        graph_schema_text="GRAPH_PHYSICAL_SENTINEL",
    )

    await node(
        {
            "query": "warehouse 공급 폐기 location이라는 제품의 관계를 알려줘.",
            "entity": {"productName": "warehouse 공급 폐기 location"},
        }
    )

    call = client.calls[0]
    assert call["reasoning_effort"] == "high"
    schema = call["response_format"]["json_schema"]["schema"]
    assert "tool_plan" not in schema["properties"]
    binding = schema["properties"]["subqueries"]["items"]["properties"]["inputBindings"]
    assert binding["type"] == "array"
    assert (
        "quantityPerAssembly" in binding["items"]["properties"]["sourceOutput"]["enum"]
    )
    system = call["messages"][0]["content"]
    assert "SQL_PHYSICAL_SENTINEL" in system
    assert "GRAPH_PHYSICAL_SENTINEL" in system
    assert "actualStock" in system
    assert "quantityPerAssembly" in system
    assert "공급업체 쌍·공동 공급 부품" not in system
    assert "특정 작업장을 거친 제품" not in system
    user = json.loads(call["messages"][1]["content"])
    assert user["query"].startswith("warehouse")


async def test_route_query_retries_once_with_structural_feedback() -> None:
    invalid = '{"subqueries":[],"resultTransform":null}'
    valid = _response([_subquery("sql_count", "sql")])
    client = MockOpenAIClient(
        make_content_response(invalid), make_content_response(valid)
    )

    result = await make_route_query_node(client, catalog=_catalog())(
        {"query": "가상 레코드 수를 알려줘.", "entity": None}
    )

    assert result["tool_plan"] == ["sql"]
    assert len(client.calls) == 2
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": invalid}
    assert "structural validation" in retry_messages[-1]["content"]


async def test_route_query_fails_safely_after_one_correction() -> None:
    invalid = '["sql"]'
    client = MockOpenAIClient(
        make_content_response(invalid), make_content_response(invalid)
    )

    with pytest.raises(RoutePlanError) as exc_info:
        await make_route_query_node(client, catalog=_catalog())(
            {"query": "질의", "entity": None}
        )

    assert exc_info.value.raw_response == invalid
    assert exc_info.value.tool_plan is None
    assert len(client.calls) == 2


async def test_route_query_does_not_apply_global_numeric_literal_recovery() -> None:
    raw = _response(
        [
            _subquery(
                "sql_facts",
                "sql",
                question="가격 조건을 적용해 제품을 조회한다.",
            )
        ]
    )
    client = MockOpenAIClient(make_content_response(raw))

    result = await make_route_query_node(client, catalog=_catalog())(
        {"query": "가격이 1만 원 이상인 제품", "entity": None}
    )

    assert result["tool_plan"] == ["sql"]
    assert len(client.calls) == 1


async def test_formal_transform_quantity_mismatch_gets_one_correction() -> None:
    route = [
        _subquery("graph_bom", "graph", join_keys=["componentId"]),
        _subquery(
            "sql_stock",
            "sql",
            depends_on=["graph_bom"],
            join_keys=["componentId"],
            bindings=[
                {
                    "target": "componentIds",
                    "sourceSubqueryId": "graph_bom",
                    "sourceOutput": "componentId",
                }
            ],
        ),
    ]
    wrong = _response(route, {"type": "bom_shortage_v1", "productionQty": 7})
    valid = _response(route, {"type": "bom_shortage_v1", "productionQty": 3})
    client = MockOpenAIClient(
        make_content_response(wrong), make_content_response(valid)
    )

    result = await make_route_query_node(client, catalog=_catalog())(
        {"query": "가상 조립품 세 개를 만들 때 부족분", "entity": None}
    )

    assert result["resultTransform"] == {
        "type": "bom_shortage_v1",
        "productionQty": 3,
    }
    assert len(client.calls) == 2


async def test_route_query_requires_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    client = MockOpenAIClient(
        make_content_response(_response([_subquery("sql_facts", "sql")]))
    )

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        await make_route_query_node(client, catalog=_catalog())(
            {"query": "질의", "entity": None}
        )

    assert client.calls == []
