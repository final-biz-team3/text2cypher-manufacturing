import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.nodes.plan_outputs import make_plan_outputs_node
from orchestrator.output_catalog import build_output_catalog
from orchestrator.planning import RouteSubquery, parse_route_draft
from tests.mocks.openai import MockOpenAIClient, make_content_response

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _catalog():
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


async def test_output_planner_keeps_common_component_summary_without_paths() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["finishedProductIdA","finishedProductIdB",'
            '"finishedProductId","finishedProductName","componentId",'
            '"componentName","minDepthA","minDepthB","pathProductIds"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Cedar Bike와 Quartz Bike에 모두 들어가는 공통 부품은?",
            "entity": [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_common_components",
                        "tool": "graph",
                        "question": "두 완제품의 BOM 공통 부품과 최소 깊이를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "finishedProductIdA",
        "finishedProductIdB",
        "componentId",
        "componentName",
        "minDepthA",
        "minDepthB",
    ]
    assert result["subqueries"][0]["question"] == (
        "Cedar Bike와 Quartz Bike에 모두 들어가는 공통 부품은?"
    )


async def test_output_planner_drops_filter_only_property_from_common_summary() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName","endDate"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    original_query = "Cedar Bike와 Quartz Bike의 현재 공통 부품을 보여줘"

    result = await node(
        {
            "query": original_query,
            "entity": [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_common_components",
                        "tool": "graph",
                        "question": (
                            "BOM endDate를 현재 날짜로 필터하고 두 제품의 "
                            "공통 부품을 조회한다"
                        ),
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "finishedProductIdA",
        "finishedProductIdB",
        "componentId",
        "componentName",
        "minDepthA",
        "minDepthB",
    ]


async def test_output_planner_drops_explicit_filter_only_common_property() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName","endDate"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = "Cedar Bike와 Quartz Bike의 BOM 유효 종료일이 2020년 이후인 공통 부품"

    result = await node(
        {
            "query": query,
            "entity": [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_common_components",
                        "tool": "graph",
                        "question": query,
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "finishedProductIdA",
        "finishedProductIdB",
        "componentId",
        "componentName",
        "minDepthA",
        "minDepthB",
    ]


async def test_output_planner_keeps_requested_bom_status_in_common_summary() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName","startDate",'
            '"pathProductIds","depth"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": (
                "Cedar Bike와 Quartz Bike에 모두 들어가는 부품 중 "
                "BOM 종료일이 미등록된 공통 부품은?"
            ),
            "entity": [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_common_components",
                        "tool": "graph",
                        "question": (
                            "두 완제품의 BOM 종료일이 NULL인 공통 부품과 최소 "
                            "깊이를 조회한다."
                        ),
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "finishedProductIdA",
        "finishedProductIdB",
        "componentId",
        "componentName",
        "minDepthA",
        "minDepthB",
        "endDate",
    ]


async def test_output_planner_keeps_explicit_bom_scalar_in_common_summary() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName",'
            '"quantityPerAssembly","finishedProductName"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": (
                "Cedar Bike와 Quartz Bike의 공통 부품과 " "단위당 필요 수량을 보여줘"
            ),
            "entity": [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_common_components",
                        "tool": "graph",
                        "question": "두 완제품의 공통 부품과 BOM 수량을 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "finishedProductIdA",
        "finishedProductIdB",
        "componentId",
        "componentName",
        "minDepthA",
        "minDepthB",
        "quantityPerAssembly",
    ]


async def test_output_planner_keeps_owner_identity_for_common_component_property() -> (
    None
):
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName",'
            '"supplierId","supplierName","active"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = (
        "Cedar Bike와 Quartz Bike의 공통 부품 중 활성 공급업체와 " "활성 상태를 보여줘"
    )

    result = await node(
        {
            "query": query,
            "entity": [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_common_components",
                        "tool": "graph",
                        "question": query,
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "finishedProductIdA",
        "finishedProductIdB",
        "componentId",
        "componentName",
        "minDepthA",
        "minDepthB",
        "supplierId",
        "supplierName",
        "active",
    ]


async def test_output_planner_keeps_scalars_pathless_beside_missing_bom_status() -> (
    None
):
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["componentId","componentName",'
            '"quantityPerAssembly","endDate"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = (
        "Cedar Bike와 Quartz Bike의 BOM 종료일이 미등록된 공통 부품과 "
        "단위당 필요 수량을 보여줘"
    )

    result = await node(
        {
            "query": query,
            "entity": [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            "tool_plan": ["graph"],
            "routeDraft": {
                "tool_plan": ["graph"],
                "subqueries": [
                    {
                        "id": "graph_common_components",
                        "tool": "graph",
                        "question": query,
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert result["subqueries"][0]["requiredOutputs"] == [
        "finishedProductIdA",
        "finishedProductIdB",
        "componentId",
        "componentName",
        "minDepthA",
        "minDepthB",
        "quantityPerAssembly",
        "endDate",
    ]


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


async def test_output_planner_normalizes_where_stock_to_location_rows() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Nebula Bracket 재고 어디에 몇 개 있어?",
            "entity": {"productId": 8001, "productName": "Nebula Bracket"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": "Nebula Bracket 재고가 어느 위치에 몇 개 있는지 조회한다.",
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


@pytest.mark.parametrize(
    "location_word",
    [
        "location",
        "locations",
        "bins",
        "wherever",
        "anywhere",
        "somewhere",
        "everywhere",
        "elsewhere",
        "whereabouts",
    ],
)
async def test_output_planner_normalizes_english_location_inventory_rows(
    location_word: str,
) -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["actualStock","productName"]}')
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": f"Show the inventory {location_word} and quantity for product X",
            "entity": {"productId": 8001, "productName": "X"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": "Show the inventory locations and quantities.",
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]
    assert "actualStock" not in result["subqueries"][0]["requiredOutputs"]


@pytest.mark.parametrize("product_name", ["Location", "Locations"])
async def test_output_planner_does_not_treat_resolved_product_name_as_location_intent(
    product_name: str,
) -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = f"Show total inventory for product {product_name}"

    result = await node(
        {
            "query": query,
            "entity": {"productId": 8001, "productName": product_name},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory",
                        "tool": "sql",
                        "question": query,
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
        "actualStock",
    ]


@pytest.mark.parametrize("product_name", ["Location", "location"])
async def test_output_planner_keeps_location_intent_beside_matching_product_name(
    product_name: str,
) -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = f"Show inventory by location for product {product_name}"

    result = await node(
        {
            "query": query,
            "entity": {"productId": 8001, "productName": product_name},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": query,
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


async def test_output_planner_keeps_location_intent_after_preposition_modifier() -> (
    None
):
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = "Show inventory at each location for product Location"

    result = await node(
        {
            "query": query,
            "entity": {"productId": 8001, "productName": "Location"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": query,
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


async def test_output_planner_removes_whole_entity_name_after_location_substring() -> (
    None
):
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = "Show stock allocation and total inventory for product Location"

    result = await node(
        {
            "query": query,
            "entity": {"productId": 8001, "productName": "Location"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory",
                        "tool": "sql",
                        "question": query,
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
        "actualStock",
    ]


async def test_output_planner_keeps_location_intent_after_fuzzy_confirmation() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    confirmed = {"productId": 8001, "productName": "Location"}
    query = "Show Locatoin inventory by location"

    result = await node(
        {
            "query": query,
            "entity": confirmed,
            "confirmed_entity": confirmed,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": query,
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


@pytest.mark.parametrize(
    ("query", "entity", "confirmed_entity"),
    [
        (
            "Show total inventory for supplier location",
            {"supplierId": 91, "supplierName": "location"},
            None,
        ),
        (
            "Location 제품의 총 재고를 보여줘",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
        ),
        (
            "Show Location inventory",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
        ),
        (
            "위치 재고 알려줘",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
        ),
        (
            "Show total inventory for product Location; is Location in stock?",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show total inventory for product Location; is location in stock?",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show total inventory for product location; is location in stock?",
            {"productId": 8001, "productName": "location"},
            None,
        ),
        (
            "Show Location stock for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show total inventory from Location",
            {"supplierId": 91, "supplierName": "Location"},
            None,
        ),
        (
            "Show total inventory in Location",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show total inventory at Shelf",
            {"productId": 8001, "productName": "Shelf"},
            None,
        ),
        (
            "Show total inventory for product Locations",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
        ),
        (
            "Show location inventory; is location in stock?",
            {"productId": 8001, "productName": "location"},
            None,
        ),
    ],
)
async def test_output_planner_removes_resolved_location_name_in_entity_context(
    query: str,
    entity: dict,
    confirmed_entity: dict | None,
) -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": query,
            "entity": entity,
            "confirmed_entity": confirmed_entity,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory",
                        "tool": "sql",
                        "question": query,
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    expected = (
        [
            "productId",
            "productName",
            "actualStock",
            "supplierId",
            "supplierName",
        ]
        if "supplierId" in entity
        else [
            "productId",
            "productName",
            "actualStock",
        ]
    )
    assert result["subqueries"][0]["requiredOutputs"] == expected


async def test_output_planner_scrubs_overlapping_entity_names_longest_first() -> None:
    query = "Compare total inventory for Frame and Location Frame"
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": query,
            "entity": [
                {"productId": 8001, "productName": "Frame"},
                {"productId": 8002, "productName": "Location Frame"},
            ],
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory",
                        "tool": "sql",
                        "question": query,
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
        "actualStock",
    ]


@pytest.mark.parametrize(
    "query",
    [
        "Show inventory at location Forge Bay",
        "작업장 Forge Bay 재고 수량을 보여줘",
    ],
)
async def test_output_planner_preserves_location_type_for_named_location(
    query: str,
) -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["locationId","locationName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": query,
            "entity": {"locationId": 91, "locationName": "Forge Bay"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_location_inventory",
                        "tool": "sql",
                        "question": query,
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


@pytest.mark.parametrize(
    ("query", "entity", "confirmed_entity"),
    [
        (
            "제품 위치의 재고를 각 위치에서 보여줘",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
        ),
        (
            "Show Location inventory across each location",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
        ),
        (
            "Show inventory per warehouse location for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show Shelf inventory with shelf details",
            {"productId": 8001, "productName": "Shelf"},
            {"productId": 8001, "productName": "Shelf"},
        ),
        (
            "Show Location inventory and location breakdown",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
        ),
        (
            "Show shelf inventory for product Shelf",
            {"productId": 8001, "productName": "Shelf"},
            None,
        ),
        (
            "Show bin stock for product Bin",
            {"productId": 8001, "productName": "Bin"},
            None,
        ),
        (
            "Show location inventory for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show location breakdown and inventory for product location",
            {"productId": 8001, "productName": "location"},
            {"productId": 8001, "productName": "location"},
        ),
        (
            "위치 제품의 재고를 위치별로 보여줘",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
        ),
        (
            "Show Location inventory according to location",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show Location inventory grouped by location",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "위치 제품의 재고 위치를 보여줘",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
        ),
        (
            "위치 제품의 재고 위치가 필요해",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
        ),
        (
            "위치 제품의 재고 위치는 어떻게 돼",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
        ),
        (
            "위치 제품의 재고를 위치에서 보여줘",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
        ),
        (
            "작업 제품의 재고를 작업장별로 보여줘",
            {"productId": 8001, "productName": "작업"},
            {"productId": 8001, "productName": "작업"},
        ),
        (
            "What is the location of inventory for product Location?",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "What shelf holds inventory for product Shelf?",
            {"productId": 8001, "productName": "Shelf"},
            None,
        ),
        (
            "Show inventory for product Bin and list the bin values",
            {"productId": 8001, "productName": "Bin"},
            None,
        ),
        (
            "List the product locations and inventory quantities for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
        ),
        (
            "Show inventory location for product location",
            {"productId": 8001, "productName": "location"},
            {"productId": 8001, "productName": "location"},
        ),
        (
            "Show inventory location for product Locatoin",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
        ),
    ],
)
async def test_output_planner_preserves_location_grammar_after_name_scrubbing(
    query: str,
    entity: dict,
    confirmed_entity: dict | None,
) -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": query,
            "entity": entity,
            "confirmed_entity": confirmed_entity,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": query,
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


async def test_output_planner_keeps_location_intent_after_supplier_confirmation() -> (
    None
):
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    confirmed = {"supplierId": 91, "supplierName": "Location"}
    query = "Show inventory by location for supplier Locatoin"

    result = await node(
        {
            "query": query,
            "entity": confirmed,
            "confirmed_entity": confirmed,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": query,
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
        "supplierId",
        "supplierName",
    ]


async def test_output_planner_preserves_explicit_scalar_with_location_inventory() -> (
    None
):
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["actualStock","productName","standardCost"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": (
                "Show the inventory locations, quantities, and standard cost "
                "for product X"
            ),
            "entity": {"productId": 8001, "productName": "X"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": (
                            "Show inventory locations, quantities, and standard cost."
                        ),
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
        "standardCost",
    ]
    assert "actualStock" not in result["subqueries"][0]["requiredOutputs"]


async def test_output_planner_keeps_shortage_inputs_with_location_inventory() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","safetyStockLevel",'
            '"actualStock","shortageQty"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = "Show inventory by location and safety-stock shortage for product X"

    result = await node(
        {
            "query": query,
            "entity": {"productId": 8001, "productName": "X"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_location_shortage",
                        "tool": "sql",
                        "question": query,
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    outputs = result["subqueries"][0]["requiredOutputs"]
    assert {"locationId", "shelf", "bin", "quantity"}.issubset(outputs)
    assert {"safetyStockLevel", "actualStock", "shortageQty"}.issubset(outputs)


async def test_output_planner_preserves_selected_calculation_with_location() -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["productName","averageListPrice"]}')
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Show inventory locations and average list price",
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": (
                            "Show inventory locations and average list price."
                        ),
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
        "averageListPrice",
    ]
    assert "listPrice" not in result["subqueries"][0]["requiredOutputs"]


@pytest.mark.parametrize("alias", ["supplierId", "categoryId", "scrapReasonName"])
async def test_output_planner_preserves_selected_canonical_location_alias(
    alias: str,
) -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","' + alias + '"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())
    query = f"Show inventory locations, quantities, and {alias}"

    result = await node(
        {
            "query": query,
            "entity": None,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": query,
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert alias in result["subqueries"][0]["requiredOutputs"]


async def test_output_planner_preserves_selected_cost_with_korean_location() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock",'
            '"standardCost"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Nebula Bracket 재고가 어디에 몇 개 있고 원가는 얼마야?",
            "entity": {"productId": 8001, "productName": "Nebula Bracket"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": "위치별 재고 수량과 원가를 조회한다.",
                        "dependsOn": [],
                        "joinKeys": [],
                    }
                ],
            },
            "resultTransform": None,
        }
    )

    assert "standardCost" in result["subqueries"][0]["requiredOutputs"]
    assert "actualStock" not in result["subqueries"][0]["requiredOutputs"]


@pytest.mark.parametrize(
    ("query", "entity"),
    [
        ("Show product ID and inventory location", None),
        (
            "Show inventory locations for the product named Size",
            {"productId": 8001, "productName": "Size"},
        ),
    ],
)
async def test_output_planner_does_not_expand_unselected_location_aliases(
    query: str,
    entity: dict | None,
) -> None:
    client = MockOpenAIClient(
        make_content_response('{"requiredOutputs":["productId","productName"]}')
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": query,
            "entity": entity,
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory_locations",
                        "tool": "sql",
                        "question": query,
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
        "locationId",
        "locationName",
        "shelf",
        "bin",
        "quantity",
    ]


async def test_output_planner_does_not_treat_allocation_as_location() -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": "Show stock allocation for product X",
            "entity": {"productId": 8001, "productName": "X"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory",
                        "tool": "sql",
                        "question": "Show stock allocation for product X.",
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
        "actualStock",
    ]


@pytest.mark.parametrize(
    "query",
    [
        "Show product name and total inventory for product X",
        "Show total production inventory for product X",
    ],
)
async def test_output_planner_ignores_generic_schema_owner_tokens_for_location(
    query: str,
) -> None:
    client = MockOpenAIClient(
        make_content_response(
            '{"requiredOutputs":["productId","productName","actualStock"]}'
        )
    )
    node = make_plan_outputs_node(client, _catalog())

    result = await node(
        {
            "query": query,
            "entity": {"productId": 8001, "productName": "X"},
            "tool_plan": ["sql"],
            "routeDraft": {
                "tool_plan": ["sql"],
                "subqueries": [
                    {
                        "id": "sql_inventory",
                        "tool": "sql",
                        "question": query,
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
        "actualStock",
    ]


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


def test_schema_catalog_has_no_evaluation_dependency() -> None:
    for relative_path in (
        "backend/orchestrator/output_catalog.py",
        "backend/orchestrator/nodes/plan_outputs.py",
    ):
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "queries/evaluation" not in source
        assert "manifest" not in source
        assert "gold/" not in source.casefold()
        assert not re.search(r"\b(?:RQ|HQ)\d+\b", source)
