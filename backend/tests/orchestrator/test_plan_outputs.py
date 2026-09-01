import asyncio
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.nodes.plan_outputs import make_plan_outputs_node
from orchestrator.output_catalog import OutputCatalog, build_output_catalog
from orchestrator.planning import RouteSubquery, parse_route_draft
from tests.mocks.openai import MockOpenAIClient, make_content_response

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _catalog() -> OutputCatalog:
    return build_output_catalog(
        load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml"),
        load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml"),
    )


def test_output_catalog_separates_source_identities_and_shared_join_aliases() -> None:
    catalog = _catalog()

    assert "categoryId" in catalog.identity_aliases_by_tool["sql"]
    assert "categoryId" not in catalog.identity_aliases_by_tool["graph"]
    assert "categoryId" not in catalog.shared_join_aliases
    assert "componentId" in catalog.shared_join_aliases


def test_router_rejects_sql_only_identity_as_graph_join_before_execution() -> None:
    catalog = _catalog()
    content = """{
      "tool_plan": ["graph", "sql"],
      "subqueries": [
        {
          "id": "graph_products",
          "tool": "graph",
          "question": "제품 관계를 조회한다.",
          "dependsOn": [],
          "joinKeys": ["categoryId"],
          "inputBindings": {}
        },
        {
          "id": "sql_categories",
          "tool": "sql",
          "question": "분류 정보를 조회한다.",
          "dependsOn": [],
          "joinKeys": ["categoryId"],
          "inputBindings": {}
        }
      ],
      "resultTransform": null
    }"""

    with pytest.raises(ValueError, match="joinKeys.*identity alias"):
        parse_route_draft(
            content,
            "제품 관계와 분류를 결합한다.",
            shared_join_aliases=catalog.shared_join_aliases,
        )


async def test_output_planner_uses_source_catalog_and_returns_execution_plan() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","standardCost"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Nebula Bracket의 표준원가",
            "entity": {"productId": 8001, "productName": "Nebula Bracket"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_cost",
                        "tool": "sql",
                        "question": "Nebula Bracket의 표준원가를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
        "standardCost",
    ]
    schema = client.calls[0]["response_format"]["json_schema"]["schema"]
    aliases = schema["properties"]["requiredOutputs"]["items"]["enum"]
    assert "standardCost" in aliases
    assert "pathProductIds" not in aliases
    prompt = client.calls[0]["messages"][0]["content"]
    assert "workOrderId가 결과 행의 기준이면 폐기량은 scrappedQty" in prompt
    assert "지정 완제품에서 부품까지의 연결 경로" in prompt


async def test_output_planner_recovers_owner_qualified_short_property_name() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","color","size"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Touring-1000 Yellow, 54 번호랑 색상, 크기 좀 봐줘.",
            "entity": {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
            },
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_product_attributes",
                        "tool": "sql",
                        "question": (
                            "제품 Touring-1000 Yellow, 54의 번호, 색상, 크기를 "
                            "조회한다."
                        ),
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert "productNumber" in result["subqueries"][0]["requiredOutputs"]


async def test_output_planner_merges_join_and_downstream_binding_outputs() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["componentName"]}'),
        make_content_response('{"requiredOutputs":["actualStock"]}'),
    )
    node = make_plan_outputs_node(client, _catalog())
    route_subqueries: list[RouteSubquery] = [
        {
            "id": "graph_components",
            "tool": "graph",
            "question": "부품 관계를 조회한다.",
            "dependsOn": [],
            "joinKeys": ["componentId"],
        },
        {
            "id": "sql_stock",
            "tool": "sql",
            "question": "앞 단계 부품의 재고를 조회한다.",
            "dependsOn": ["graph_components"],
            "joinKeys": ["componentId"],
            "inputBindings": {"componentIds": "graph_components.componentId"},
        },
    ]

    result = await node(
        {
            "query": "부품 관계와 재고",
            "entity": None,
            "tool_plan": ["graph", "sql"],
            "routeDraft": {
                "tool_plan": ["graph", "sql"],
                "subqueries": route_subqueries,
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "componentName",
        "componentId",
    ]
    assert result["subqueries"][1]["requiredOutputs"] == [
        "actualStock",
        "componentId",
    ]


async def test_output_planner_runs_independent_subqueries_concurrently() -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    class _ConcurrentCompletions:
        async def create(self, **kwargs):
            user_message = kwargs["messages"][-1]["content"]
            tool = "sql" if "source: sql" in user_message else "graph"
            started.append(tool)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.2)
            content = (
                '{"requiredOutputs":["activeSupplierCount"]}'
                if tool == "sql"
                else '{"requiredOutputs":["componentId","componentName","minDepth"]}'
            )
            return make_content_response(content)

    client = SimpleNamespace(chat=SimpleNamespace(completions=_ConcurrentCompletions()))
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "활성 공급업체 수와 최하위 부품을 함께 알려줘.",
            "entity": None,
            "tool_plan": ["sql", "graph"],
            "routeDraft": {
                "tool_plan": ["sql", "graph"],
                "subqueries": [
                    {
                        "id": "sql_count",
                        "tool": "sql",
                        "question": "활성 공급업체 수를 집계한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    },
                    {
                        "id": "graph_leaf",
                        "tool": "graph",
                        "question": "최하위 부품을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    },
                ],
            },
            "resultTransform": None,
        }
    )

    assert started == ["sql", "graph"]
    assert [item["id"] for item in result["subqueries"]] == [
        "sql_count",
        "graph_leaf",
    ]


async def test_output_planner_keeps_a_null_status_field_in_list_results() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","endDate",'
            '"finishedProductIdA","finishedProductIdB"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "종료일이 비어 있는 제품을 제품 ID 순으로 7개 보여줘.",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_unset_dates",
                        "tool": "sql",
                        "question": "종료일이 미등록된 제품을 제품 ID 순으로 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "productId",
        "productName",
        "sellEndDate",
    ]


async def test_output_planner_uses_bom_owner_for_ambiguous_end_date() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["bomId","sellEndDate"]}')
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "BOM 종료일이 미등록된 구성 행을 보여줘.",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_unset_bom_dates",
                        "tool": "sql",
                        "question": "BOM 종료일이 NULL인 구성 행을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == ["bomId", "endDate"]


async def test_output_planner_preserves_model_date_when_owner_is_ambiguous() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["productId","endDate"]}')
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "종료일이 미등록된 행을 보여줘.",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_unset_dates",
                        "tool": "sql",
                        "question": "종료일이 NULL인 행을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert "endDate" in result["subqueries"][0]["requiredOutputs"]
    assert "sellEndDate" not in result["subqueries"][0]["requiredOutputs"]


async def test_output_planner_revalidates_source_after_deterministic_merge() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["productName"]}')
    )
    node = make_plan_outputs_node(client, _catalog())

    with pytest.raises(ValueError, match="graph source ownership.*categoryId"):
        await node(
            {
                "query": "제품 관계와 분류를 결합한다.",
                "entity": None,
                "tool_plan": ["graph", "sql"],
                "routeDraft": {
                    "tool_plan": ["graph", "sql"],
                    "subqueries": [
                        {
                            "id": "graph_products",
                            "tool": "graph",
                            "question": "제품 관계를 조회한다.",
                            "dependsOn": [],
                            "joinKeys": ["categoryId"],
                        },
                        {
                            "id": "sql_categories",
                            "tool": "sql",
                            "question": "분류 정보를 조회한다.",
                            "dependsOn": [],
                            "joinKeys": ["categoryId"],
                        },
                    ],
                },
                "resultTransform": None,
            }
        )


async def test_output_planner_retries_unknown_or_wrong_source_alias() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["pathProductIds"]}'),
        make_content_response('{"requiredOutputs":["productCount"]}'),
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "제품 수",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_count",
                        "tool": "sql",
                        "question": "제품 수를 집계한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == ["productCount"]
    assert len(client.calls) == 2
    assert "source ownership" in client.calls[1]["messages"][-1]["content"]


async def test_output_planner_completes_resolved_sql_entity_identity() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["productCount"]}')
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Fasteners 분류의 제품 수",
            "entity": {
                "productCategoryId": 81,
                "productCategoryName": "Fasteners",
            },
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_category_count",
                        "tool": "sql",
                        "question": "Fasteners 분류의 제품 수를 집계한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "productCount",
        "categoryId",
        "categoryName",
    ]


async def test_output_planner_normalizes_work_order_row_outputs() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["workOrderId","productName",'
            '"scrapReasonName","totalScrappedQty"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "폐기량이 큰 작업지시와 제품 및 사유",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_work_order_scrap",
                        "tool": "sql",
                        "question": "작업지시별 폐기량과 제품 및 사유를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert set(result["subqueries"][0]["requiredOutputs"]) == {
        "workOrderId",
        "productId",
        "productName",
        "scrapReasonId",
        "scrapReasonName",
        "scrappedQty",
    }
    assert set(result["subqueries"][0]["requiredOutputs"]).isdisjoint(
        {"locationId", "locationName"}
    )


async def test_output_planner_completes_graph_location_and_sql_aggregate() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["productId","productName"]}'),
        make_content_response(
            '{"requiredOutputs":["productId","productName",' '"totalScrappedQty"]}'
        ),
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Forge Bay를 거친 제품의 폐기 합계",
            "entity": {"locationId": 91, "locationName": "Forge Bay"},
            "tool_plan": ["graph", "sql"],
            "routeDraft": {
                "tool_plan": ["graph", "sql"],
                "subqueries": [
                    {
                        "id": "graph_location_products",
                        "tool": "graph",
                        "question": "Forge Bay를 거친 제품을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": ["productId"],
                    },
                    {
                        "id": "sql_scrap_totals",
                        "tool": "sql",
                        "question": "앞 단계 제품별 폐기 합계를 집계한다.",
                        "dependsOn": ["graph_location_products"],
                        "joinKeys": ["productId"],
                        "inputBindings": {
                            "productIds": "graph_location_products.productId"
                        },
                    },
                ],
            },
            "resultTransform": None,
        }
    )

    assert set(result["subqueries"][0]["requiredOutputs"]) == {
        "locationId",
        "locationName",
        "productId",
        "productName",
    }
    assert set(result["subqueries"][1]["requiredOutputs"]) == {
        "productId",
        "productName",
        "totalScrappedQty",
        "workOrderCount",
    }


async def test_output_planner_completes_supplier_impact_path() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName",'
            '"finishedProductId","finishedProductName"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "North Mill 공급 중단 영향",
            "entity": {"supplierId": 92, "supplierName": "North Mill"},
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_supplier_impact",
                        "tool": "graph",
                        "question": "North Mill 공급 부품의 완제품 영향을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    outputs = result["subqueries"][0]["requiredOutputs"]
    assert {"depth", "pathProductIds", "pathProductNames"}.issubset(outputs)
    assert "component에서 finishedProduct 방향" in result["subqueries"][0]["question"]


async def test_output_planner_preserves_finished_to_component_path_direction() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["finishedProductId","finishedProductName",'
            '"componentId","componentName","depth","pathProductIds",'
            '"pathProductNames"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Aurora Gearbox에서 Titan Washer까지의 조립 경로",
            "entity": [
                {"productId": 8201, "productName": "Aurora Gearbox"},
                {"productId": 8202, "productName": "Titan Washer"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_product_path",
                        "tool": "graph",
                        "question": "두 제품 사이의 조립 경로를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert (
        "nodes(path) 순서로 finishedProduct에서 component 방향"
        in result["subqueries"][0]["question"]
    )


async def test_output_planner_preserves_work_order_role_from_original_question() -> (
    None
):
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["routingOperationKey",'
            '"sequence","locationId","locationName"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "8307의 라우팅 공정을 작업장 순서대로 보여줘.",
            "entity": None,
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_routing",
                        "tool": "graph",
                        "question": "식별자 8307 제품의 라우팅 공정을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    question = result["subqueries"][0]["question"]
    assert "workOrderId" in result["subqueries"][0]["requiredOutputs"]
    assert question.startswith("8307의 라우팅 공정을")
    assert "숫자는 workOrderId" in question
    assert "productId가 아니다" in question


async def test_bom_shortage_output_contract_is_model_free_and_exact() -> None:
    client = MockOpenAIClient()
    node = make_plan_outputs_node(client, _catalog())
    graph_id = "graph_bom_supply"

    result = await node(
        {
            "query": "Atlas Cart 생산 부족 부품",
            "entity": {"productId": 8002, "productName": "Atlas Cart"},
            "tool_plan": ["graph", "sql"],
            "routeDraft": {
                "tool_plan": ["graph", "sql"],
                "subqueries": [
                    {
                        "id": graph_id,
                        "tool": "graph",
                        "question": "BOM과 공급업체를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": ["componentId"],
                    },
                    {
                        "id": "sql_stock",
                        "tool": "sql",
                        "question": (
                            "부품별 재고와 생산량 9개에 필요한 수량을 BOM에서 "
                            "비교한다."
                        ),
                        "dependsOn": [graph_id],
                        "joinKeys": ["componentId"],
                        "inputBindings": {"componentIds": f"{graph_id}.componentId"},
                    },
                ],
                "resultTransform": {
                    "type": "bom_shortage_v1",
                    "productionQty": 9,
                },
            },
            "resultTransform": {
                "type": "bom_shortage_v1",
                "productionQty": 9,
            },
        }
    )

    assert client.calls == []
    assert set(result["subqueries"][0]["requiredOutputs"]) == {
        "finishedProductId",
        "finishedProductName",
        "componentId",
        "componentName",
        "depth",
        "pathProductIds",
        "quantityPerAssembly",
        "supplierId",
        "supplierName",
    }
    assert result["subqueries"][1]["requiredOutputs"] == [
        "componentId",
        "makeFlag",
        "actualStock",
    ]
    assert result["subqueries"][1]["question"] == (
        "앞 단계 componentId별 makeFlag와 현재 재고 합계를 조회한다. "
        "필요한 수량과 부족량은 resultTransform에서 계산한다."
    )
    graph_question = result["subqueries"][0]["question"]
    assert "active 공급업체를 선택적으로 연결" in graph_question
    assert "sellableFinishedGood로 사전 필터하지 않는다" in graph_question
    assert "외부 구매 판정은 SQL makeFlag" in graph_question
    assert "pathProductIds는 nodes(path) 순서로 finishedProduct에서" in graph_question
    assert "component 방향으로 반환하고 reverse하지 않는다" in graph_question
    assert "quantityPerAssembly도 relationships(path) 순서를 유지" in graph_question


async def test_output_planner_uses_root_role_for_full_hierarchy() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["finishedProductId","finishedProductName",'
            '"componentId","componentName","depth","pathProductIds"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Atlas Assembly 하위 부품 계층",
            "entity": {"productId": 93, "productName": "Atlas Assembly"},
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_hierarchy",
                        "tool": "graph",
                        "question": "Atlas Assembly의 전체 BOM 계층을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    outputs = result["subqueries"][0]["requiredOutputs"]
    assert {"rootProductId", "rootProductName"}.issubset(outputs)
    assert "finishedProductId" not in outputs


async def test_output_planner_completes_terse_bom_tree_from_original_query() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName","minDepth"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Nebula Frame 부품 트리?",
            "entity": {"productId": 8103, "productName": "Nebula Frame"},
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_tree",
                        "tool": "graph",
                        "question": "Nebula Frame의 최하위 부품과 최소 깊이를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert set(result["subqueries"][0]["requiredOutputs"]) == {
        "rootProductId",
        "rootProductName",
        "componentId",
        "componentName",
        "depth",
        "pathProductIds",
        "pathProductNames",
    }
    assert result["subqueries"][0]["question"] == "Nebula Frame 부품 트리?"


async def test_output_planner_normalizes_aggregate_stock_and_purchase_count() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName","locationId",'
            '"locationName","shelf","bin","quantity"]}'
        ),
        make_content_response('{"requiredOutputs":["productCount"]}'),
    )
    node = make_plan_outputs_node(client, _catalog())

    stock_result = await node(
        {
            "query": "앞에서 찾은 부품 재고를 알려줘.",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_stock",
                        "tool": "sql",
                        "question": "부품별 현재 재고를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )
    count_result = await node(
        {
            "query": "자체 생산 대상이 아닌 품목 수를 알려줘.",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_count",
                        "tool": "sql",
                        "question": "자체 생산하지 않는 품목 수를 집계한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    stock_outputs = stock_result["subqueries"][0]["requiredOutputs"]
    assert {"componentId", "componentName", "actualStock"}.issubset(stock_outputs)
    assert set(stock_outputs).isdisjoint(
        {"locationId", "locationName", "shelf", "bin", "quantity"}
    )
    assert count_result["subqueries"][0]["requiredOutputs"] == ["purchasedProductCount"]


async def test_output_planner_completes_work_order_scrap_fact_bundle() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productName","locationId","locationName",'
            '"scrapReasonName"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "작업지시 8307의 생산 품목과 폐기 이유",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_scrap_fact",
                        "tool": "sql",
                        "question": "작업지시의 생산 품목과 폐기 이유를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": ["workOrderId"],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert set(result["subqueries"][0]["requiredOutputs"]) == {
        "workOrderId",
        "productId",
        "productName",
        "scrapReasonId",
        "scrapReasonName",
        "scrappedQty",
    }


async def test_output_planner_preserves_rejected_quantity_ranking_semantics() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["supplierId","supplierName",' '"totalRejectedQty"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "구매 주문이 많이 반려된 업체 다섯 곳을 알려줘.",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_supplier_rejections",
                        "tool": "sql",
                        "question": (
                            "반려된 구매 주문 건수가 많은 업체 상위 5곳을 조회한다."
                        ),
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    question = result["subqueries"][0]["question"]
    assert "purchaseorderid 건수가 아니라 rejectedqty 합계" in question
    assert "totalRejectedQty를 내림차순" in question
