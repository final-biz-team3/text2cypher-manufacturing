"""route_query 노드가 질의를 SQL·GRAPH 실행 계획으로 분류하는 동작을 테스트한다."""

import pytest

from orchestrator.nodes.route_query import RoutePlanError, make_route_query_node
from tests.mocks.openai import MockOpenAIClient, make_content_response


async def test_route_query_returns_sql_tool_plan_for_numeric_query() -> None:
    """수치 조회 질의는 ["sql"]로 라우팅된다."""
    openai_client = MockOpenAIClient(make_content_response('["sql"]'))
    node = make_route_query_node(openai_client)

    result = await node(
        {
            "query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘.",
            "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"},
        }
    )

    assert result == {
        "tool_plan": ["sql"],
        "routeDraft": {
            "tool_plan": ["sql"],
            "subqueries": [
                {
                    "id": "sql_query",
                    "tool": "sql",
                    "question": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘.",
                    "dependsOn": [],
                    "joinKeys": [],
                }
            ],
        },
        "resultTransform": None,
    }


async def test_route_query_returns_graph_tool_plan_for_relationship_query() -> None:
    """다단계 관계 탐색 질의는 ["graph"]로 라우팅된다."""
    openai_client = MockOpenAIClient(make_content_response('["graph"]'))
    node = make_route_query_node(openai_client)

    result = await node(
        {
            "query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘.",
            "entity": {"productId": 492, "productName": "Paint - Black"},
        }
    )

    assert result == {
        "tool_plan": ["graph"],
        "routeDraft": {
            "tool_plan": ["graph"],
            "subqueries": [
                {
                    "id": "graph_query",
                    "tool": "graph",
                    "question": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘.",
                    "dependsOn": [],
                    "joinKeys": [],
                }
            ],
        },
        "resultTransform": None,
    }


async def test_route_query_sends_query_and_entity_in_prompt() -> None:
    """LLM에 보내는 프롬프트에 질의 원문과 확정된 entity를 포함한다."""
    openai_client = MockOpenAIClient(make_content_response('["sql"]'))
    node = make_route_query_node(openai_client)

    await node(
        {
            "query": "활성 공급업체 수를 알려줘.",
            "entity": None,
        }
    )

    sent_messages = openai_client.calls[0]["messages"]
    response_format = openai_client.calls[0]["response_format"]
    assert openai_client.calls[0]["reasoning_effort"] == "medium"
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert "uniqueItems" not in schema["properties"]["tool_plan"]
    subquery_schema = schema["properties"]["subqueries"]["items"]
    assert subquery_schema["additionalProperties"] is False
    assert "requiredOutputs" not in subquery_schema["properties"]
    system_message = next(m["content"] for m in sent_messages if m["role"] == "system")
    assert "schema-aware planner가 결정" in system_message
    assert "requiredOutputs" in system_message
    assert "재고, 가격, 비용" in system_message
    assert "BOM 경로, 영향 관계" in system_message
    assert "단일 source의 joinKeys는 빈 배열" in system_message
    assert "도구마다 subquery를 정확히 하나" in system_message
    assert "Cobalt Works 공급 중단" in system_message
    assert "이 경우 workOrderId를 전달하지 않는다" in system_message
    user_message = next(m["content"] for m in sent_messages if m["role"] == "user")
    assert "활성 공급업체 수를 알려줘." in user_message


async def test_route_query_accepts_high_reasoning_effort() -> None:
    openai_client = MockOpenAIClient(make_content_response('["sql"]'))
    node = make_route_query_node(openai_client, reasoning_effort="high")

    await node({"query": "활성 공급업체 수를 알려줘.", "entity": None})

    assert openai_client.calls[0]["reasoning_effort"] == "high"


async def test_route_query_requires_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENAI_MODEL이 없으면 라우팅 요청 전에 즉시 실패한다."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    openai_client = MockOpenAIClient(make_content_response('["sql"]'))
    node = make_route_query_node(openai_client)

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        await node({"query": "제품의 정가를 알려줘.", "entity": None})

    assert openai_client.calls == []


@pytest.mark.parametrize(
    "tool_plan_json",
    ["[]", '["unknown"]', '"sql"'],
)
async def test_route_query_rejects_invalid_plan(tool_plan_json: str) -> None:
    """재생성한 계획도 잘못되면 마지막 응답을 담아 거부한다."""
    openai_client = MockOpenAIClient(
        make_content_response(tool_plan_json), make_content_response(tool_plan_json)
    )
    node = make_route_query_node(openai_client)

    with pytest.raises(RoutePlanError) as exc_info:
        await node({"query": "질의", "entity": None})

    assert exc_info.value.raw_response == tool_plan_json
    assert exc_info.value.tool_plan is None
    assert len(openai_client.calls) == 2


async def test_route_query_preserves_valid_route_when_subquery_plan_is_invalid() -> (
    None
):
    """하위 계획 검증 실패가 올바른 HYBRID route 선택까지 지우지 않는다."""
    raw_response = (
        '{"tool_plan":["graph","sql"],"subqueries":['
        '{"id":"graph_step","tool":"graph","question":"경로 조회",'
        '"dependsOn":[],"requiredOutputs":["componentId"],'
        '"joinKeys":["componentId"]},'
        '{"id":"sql_step","tool":"sql","question":"재고 조회",'
        '"dependsOn":["graph_step"],'
        '"inputBindings":{"componentIds":"graph_step.componentId"},'
        '"requiredOutputs":[],"joinKeys":["componentId"]}]}'
    )
    openai_client = MockOpenAIClient(
        make_content_response(raw_response), make_content_response(raw_response)
    )
    node = make_route_query_node(openai_client)

    with pytest.raises(RoutePlanError) as exc_info:
        await node({"query": "영향 경로와 재고를 알려줘.", "entity": None})

    assert "requiredOutputs를 생성할 수 없습니다" in str(exc_info.value)
    assert exc_info.value.tool_plan == ["graph", "sql"]


async def test_route_query_retries_invalid_plan_with_validation_feedback() -> None:
    """첫 계획의 검증 오류를 전달하고 두 번째의 올바른 계획을 사용한다."""
    invalid = '{"tool_plan":["sql"],"subqueries":[]}'
    valid = (
        '{"tool_plan":["sql"],"subqueries":['
        '{"id":"sql_count","tool":"sql","question":"제품 수를 센다.",'
        '"dependsOn":[],"joinKeys":[],"inputBindings":{}}],'
        '"resultTransform":null}'
    )
    openai_client = MockOpenAIClient(
        make_content_response(invalid), make_content_response(valid)
    )
    node = make_route_query_node(openai_client)

    result = await node({"query": "제품 수를 알려줘.", "entity": None})

    assert result["routeDraft"]["subqueries"][0]["id"] == "sql_count"
    retry_messages = openai_client.calls[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": invalid}
    assert "비어 있지 않은 배열" in retry_messages[-1]["content"]


async def test_route_query_retries_missing_raw_binding_join_keys() -> None:
    invalid = (
        '{"tool_plan":["graph","sql"],"subqueries":['
        '{"id":"graph_impact","tool":"graph","question":"영향 부품을 조회한다.",'
        '"dependsOn":[],"joinKeys":[],"inputBindings":{}},'
        '{"id":"sql_stock","tool":"sql","question":"영향 부품 재고를 조회한다.",'
        '"dependsOn":["graph_impact"],"joinKeys":[],"inputBindings":{'
        '"componentIds":"graph_impact.componentId"}}],"resultTransform":null}'
    )
    valid = invalid.replace('"joinKeys":[]', '"joinKeys":["componentId"]')
    client = MockOpenAIClient(
        make_content_response(invalid), make_content_response(valid)
    )
    node = make_route_query_node(client)

    result = await node({"query": "영향 부품과 재고를 알려줘.", "entity": None})

    assert len(client.calls) == 2
    assert "양쪽" in client.calls[1]["messages"][-1]["content"]
    assert result["rawRouteDraft"]["subqueries"][0]["joinKeys"] == ["componentId"]
    assert result["rawRouteDraft"]["subqueries"][1]["joinKeys"] == ["componentId"]
    assert result["routeDraft"]["subqueries"][0]["joinKeys"] == ["componentId"]
    assert result["routeDraft"]["subqueries"][1]["joinKeys"] == ["componentId"]


async def test_route_query_retries_product_size_used_as_korean_production_qty() -> None:
    wrong = (
        '{"tool_plan":["graph","sql"],"subqueries":['
        '{"id":"graph_bom","tool":"graph","question":"BOM을 조회한다.",'
        '"dependsOn":[],"joinKeys":["componentId"],"inputBindings":{}},'
        '{"id":"sql_stock","tool":"sql","question":"재고를 조회한다.",'
        '"dependsOn":["graph_bom"],"joinKeys":["componentId"],"inputBindings":{'
        '"componentIds":"graph_bom.componentId"}}],"resultTransform":{'
        '"type":"bom_shortage_v1","productionQty":58}}'
    )
    valid = wrong.replace('"productionQty":58', '"productionQty":10')
    client = MockOpenAIClient(
        make_content_response(wrong), make_content_response(valid)
    )
    node = make_route_query_node(client)

    result = await node(
        {
            "query": "HL Road Frame - Black, 58 열 개를 만들 때 부족한 부품?",
            "entity": {
                "productId": 680,
                "productName": "HL Road Frame - Black, 58",
            },
        }
    )

    assert len(client.calls) == 2
    assert "생산 수량 10" in client.calls[1]["messages"][-1]["content"]
    assert result["resultTransform"] == {
        "type": "bom_shortage_v1",
        "productionQty": 10,
    }


@pytest.mark.parametrize(
    ("query", "content", "expected_tools"),
    [
        (
            "활성 공급업체 중 같은 부품을 공급하는 업체 쌍을 보여줘.",
            '{"tool_plan":["graph"],"subqueries":['
            '{"id":"graph_supplier_pairs","tool":"graph",'
            '"question":"활성 공급업체의 공동 공급 관계를 집계한다.",'
            '"dependsOn":[],"joinKeys":[],"inputBindings":{}}],'
            '"resultTransform":null}',
            ["graph"],
        ),
        (
            "Forge Cell을 거친 제품의 폐기량을 알려줘.",
            '{"tool_plan":["graph","sql"],"subqueries":['
            '{"id":"graph_location_products","tool":"graph",'
            '"question":"Forge Cell을 거친 제품을 조회한다.",'
            '"dependsOn":[],"joinKeys":["productId"],'
            '"inputBindings":{}},'
            '{"id":"sql_scrap","tool":"sql",'
            '"question":"앞 단계 productId별 폐기량을 집계한다.",'
            '"dependsOn":["graph_location_products"],'
            '"joinKeys":["productId"],'
            '"inputBindings":{"productIds":'
            '"graph_location_products.productId"}}],'
            '"resultTransform":null}',
            ["graph", "sql"],
        ),
        (
            "활성 공급처별로 공급 품목 종류 수가 많은 순서를 알려줘.",
            '{"tool_plan":["sql"],"subqueries":['
            '{"id":"sql_supplier_product_count","tool":"sql",'
            '"question":"활성 공급처별 공급 품목 종류 수를 집계한다.",'
            '"dependsOn":[],"joinKeys":[],"inputBindings":{}}],'
            '"resultTransform":null}',
            ["sql"],
        ),
        (
            "작업장별로 처리한 작업 주문 수가 많은 순서를 알려줘.",
            '{"tool_plan":["sql"],"subqueries":['
            '{"id":"sql_location_order_count","tool":"sql",'
            '"question":"작업장별 서로 다른 작업 주문 수를 집계한다.",'
            '"dependsOn":[],"joinKeys":[],"inputBindings":{}}],'
            '"resultTransform":null}',
            ["sql"],
        ),
    ],
)
async def test_route_query_accepts_synthetic_source_ownership_families(
    query: str,
    content: str,
    expected_tools: list[str],
) -> None:
    client = MockOpenAIClient(make_content_response(content))
    node = make_route_query_node(client)

    result = await node({"query": query, "entity": None})

    assert result["tool_plan"] == expected_tools
    prompt = client.calls[0]["messages"][0]["content"]
    assert "공급업체 쌍·공동 공급 부품" in prompt
    assert "특정 작업장을 거친 제품" in prompt
    assert "공급업체별 서로 다른 공급 제품 수" in prompt
    assert "작업장별 서로 다른 작업지시 수" in prompt
