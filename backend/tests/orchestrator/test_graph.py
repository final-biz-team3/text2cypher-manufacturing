"""엔티티 확정 -> 라우팅 -> 실제 SQL/Cypher 실행까지의 전체 흐름을 테스트한다.
execute_sql/execute_cypher가 이제 진짜 DB를 치므로 이 파일은 전부 integration
마커가 붙는다(OpenAI 호출만 mock, DB는 실제)."""

import json
from decimal import Decimal

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# core.postgres/orchestrator.execution.*는 os.getenv()로 환경변수를 직접
# 읽고 load_dotenv()를 호출하지 않는다(main.py만 호출한다) - main.py를
# import하지 않는 이 테스트는 여기서 직접 .env를 로드해야 한다.
load_dotenv()

from core.postgres import bootstrap_postgres, get_pool, open_pool  # noqa: E402
from orchestrator.graph import build_orchestrator_graph  # noqa: E402
from tests.mocks.openai import (  # noqa: E402
    MockOpenAIClient,
    make_content_response,
    make_tool_call_response,
)

# execute_cypher가 Neo4j reader 드라이버 싱글턴을 모듈 레벨 전역으로 갖고
# 있어서, 테스트 함수마다 기본(function scope)으로 다른 이벤트 루프를 쓰면
# 한 테스트에서 만든 드라이버를 다른 테스트(다른 루프)가 재사용하려다
# "attached to a different loop" RuntimeError가 난다(실측으로 확인함 -
# Postgres AsyncConnectionPool은 우연히 버텼지만 Neo4j 소켓 계층은 안 버팀).
# 이 파일의 테스트와 fixture 전체를 하나의 module 스코프 이벤트 루프로
# 묶어서 근본적으로 막는다.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def postgres_pool():
    """execute_sql/resolve_entity가 공유하는 앱 전역 read pool을 연다."""
    await bootstrap_postgres()
    await open_pool()
    return get_pool()


async def test_graph_resolves_entity_then_runs_sql_agent_once(postgres_pool) -> None:
    """제품명이 있는 SQL형 질의는 entity 확정 후 sql_agent가 실제 DB 결과를 받는다."""
    openai_client = MockOpenAIClient(
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
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == (
        "SELECT listprice, standardcost FROM production.product "
        "WHERE productid = 956"
    )
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"] == [
        {"listprice": Decimal("2384.07"), "standardcost": Decimal("1481.9379")}
    ]
    assert result["cypher_query"] is None
    assert result["graph_result"] is None
    assert result["composed_result"] == {
        "mode": "single",
        "rows": result["sql_result"]["result"],
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 1,
        "truncated": False,
    }
    assert result["final_answer"] == f"COMPOSED: {result['composed_result']}"
    assert len(openai_client.calls) == 3


async def test_graph_routes_to_graph_and_runs_cypher_agent_once(postgres_pool) -> None:
    """부품 사용처를 묻는 질의는 entity 확정 후 graph로 라우팅되고 cypher_agent가
    실제 Neo4j 결과를 받는다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity", {"entityType": "product", "entityName": "Paint - Black"}
        ),
        make_content_response('["graph"]'),
        make_content_response(
            "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
            "WHERE part.productId = 492 RETURN parent"
        ),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

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
    assert result["graph_result"]["error"] is None
    graph_rows = result["graph_result"]["result"]
    assert graph_rows
    assert any(row["parent"]["productId"] == 680 for row in graph_rows)
    assert result["composed_result"]["mode"] == "single"
    assert result["composed_result"]["rows"] == graph_rows
    assert result["final_answer"] == f"COMPOSED: {result['composed_result']}"


async def test_graph_runs_both_agents_independently_for_hybrid_tool_plan(
    postgres_pool,
) -> None:
    """tool_plan이 ["sql", "graph"] Hybrid일 때 sql_agent와 cypher_agent가 각각
    독립적으로 실행되고, 한쪽의 attempts/error가 다른 쪽으로 섞이지 않는다."""
    openai_client = MockOpenAIClient(
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
            "MATCH (finished:Product {productId: 956})-[:REQUIRES_COMPONENT]->"
            "(component:Product) RETURN component"
        ),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {
            "query": (
                "Touring-1000 Yellow, 54의 정가와 표준원가, "
                "그리고 이 제품에 들어가는 부품을 알려줘."
            )
        }
    )

    assert result["tool_plan"] == ["sql", "graph"]

    assert result["sql_query"] == (
        "SELECT listprice, standardcost FROM production.product "
        "WHERE productid = 956"
    )
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"] == [
        {"listprice": Decimal("2384.07"), "standardcost": Decimal("1481.9379")}
    ]
    assert len(result["sql_result"]["attempts"]) == 1

    assert result["cypher_query"] == (
        "MATCH (finished:Product {productId: 956})-[:REQUIRES_COMPONENT]->"
        "(component:Product) RETURN component"
    )
    assert result["graph_result"]["error"] is None
    assert result["graph_result"]["result"]
    assert len(result["graph_result"]["attempts"]) == 1

    assert result["composed_result"] == {
        "mode": "separate",
        "rows": [],
        "sections": {
            "sql_query": {
                "tool": "sql",
                "rows": result["sql_result"]["result"],
                "empty_reason": None,
            },
            "graph_query": {
                "tool": "graph",
                "rows": result["graph_result"]["result"],
                "empty_reason": None,
            },
        },
        "error": None,
        "empty_reason": None,
        "total_count": len(result["sql_result"]["result"])
        + len(result["graph_result"]["result"]),
        "truncated": False,
    }
    assert result["final_answer"] == f"COMPOSED: {result['composed_result']}"

    assert len(openai_client.calls) == 4


async def test_graph_builds_final_answer_from_sql_result(postgres_pool) -> None:
    """특정 제품을 지칭하지 않는 집계 질의도 sql_agent를 거쳐 final_answer가 채워진다."""
    openai_client = MockOpenAIClient(
        make_content_response("[]"),
        make_content_response('["sql"]'),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke({"query": "전체 제품 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == "SELECT COUNT(*) FROM production.product"
    assert result["sql_result"]["error"] is None
    assert result["sql_result"]["result"] == [{"count": 504}]
    assert result["final_answer"] is not None
    assert "504" in result["final_answer"]


async def test_graph_composes_canonical_rq18_many_to_one(postgres_pool) -> None:
    """RQ18 GRAPH 97행과 SQL 1행을 순서대로 결합해 97행을 만든다."""
    route_plan = """{
      "tool_plan": ["graph", "sql"],
      "subqueries": [
        {
          "id": "graph_components",
          "tool": "graph",
          "question": "활성 공급업체 Allenson Cycles의 공급 부품과 영향 완제품 경로를 조회한다.",
          "dependsOn": [],
          "requiredOutputs": ["componentId", "componentName", "finishedProductId", "finishedProductName", "depth", "pathProductIds"],
          "joinKeys": ["componentId"],
          "inputBindings": {}
        },
        {
          "id": "sql_stock",
          "tool": "sql",
          "question": "앞 단계에서 확인한 componentId별 현재 재고를 조회한다.",
          "dependsOn": ["graph_components"],
          "inputBindings": {"componentIds": "graph_components.componentId"},
          "requiredOutputs": ["componentId", "actualStock"],
          "joinKeys": ["componentId"]
        }
      ],
      "resultTransform": null
    }"""
    cypher = """MATCH (supplier:Supplier {supplierId: 1494})-[:SUPPLIES]->(component:Product)
MATCH path = (component)<-[rels:REQUIRES_COMPONENT*1..4]-(finished:Product)
WHERE supplier.active = true
  AND finished.sellableFinishedGood = true
  AND all(rel IN rels WHERE rel.startDate <= date('2014-08-08')
    AND (rel.endDate IS NULL OR date('2014-08-08') < rel.endDate))
RETURN component.productId AS componentId,
  component.name AS componentName,
  finished.productId AS finishedProductId,
  finished.name AS finishedProductName,
  length(path) AS depth,
  [node IN nodes(path) | node.productId] AS pathProductIds
ORDER BY componentId, depth, finishedProductId, pathProductIds"""
    sql = """SELECT p.productid AS "componentId",
  COALESCE(SUM(i.quantity), 0) AS "actualStock"
FROM production.product AS p
LEFT JOIN production.productinventory AS i ON i.productid = p.productid
WHERE p.productid IN (530)
GROUP BY p.productid
ORDER BY p.productid"""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "supplier", "entityName": "Allenson Cycles"},
        ),
        make_content_response(route_plan),
        make_content_response(cypher),
        make_content_response(sql),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {
            "query": (
                "공급업체 Allenson Cycles가 공급을 중단하면 영향을 받는 부품과 "
                "완제품, 각 부품의 현재 재고를 알려줘."
            )
        }
    )

    graph_rows = result["graph_result"]["result"]
    assert graph_rows
    expected_component_ids = [row["componentId"] for row in graph_rows]
    sql_user_message = next(
        message["content"]
        for message in openai_client.calls[3]["messages"]
        if message["role"] == "user"
    )
    assert json.loads(sql_user_message)["inputBindings"] == {
        "componentIds": expected_component_ids
    }
    assert result["sql_query"] == sql
    assert result["sql_result"]["error"] is None
    sql_rows = result["sql_result"]["result"]
    assert len(graph_rows) == 97
    assert len(sql_rows) == 1
    assert {row["componentId"] for row in sql_rows} == set(expected_component_ids)
    assert sql_rows[0]["actualStock"] == 780

    composed = result["composed_result"]
    assert composed["mode"] == "joined"
    assert composed["error"] is None
    assert composed["total_count"] == 97
    assert composed["truncated"] is False
    assert len(composed["rows"]) == 97
    assert all(row["actualStock"] == 780 for row in composed["rows"])
    assert [
        {key: value for key, value in row.items() if key != "actualStock"}
        for row in composed["rows"]
    ] == graph_rows
    assert result["final_answer"] == f"COMPOSED: {composed}"


async def test_graph_composes_canonical_rq19_bom_shortage(postgres_pool) -> None:
    """RQ19 source 전체를 검증해 결정적인 외부 구매 부족량 1행을 만든다."""
    route_plan = """{
      "tool_plan": ["graph", "sql"],
      "subqueries": [
        {
          "id": "graph_bom_supply",
          "tool": "graph",
          "question": "완제품 HL Road Frame - Black, 58의 유효 BOM 경로별 필요 수량 계수와 활성 공급업체를 조회한다.",
          "dependsOn": [],
          "requiredOutputs": ["finishedProductId", "finishedProductName", "componentId", "componentName", "depth", "pathProductIds", "quantityPerAssembly", "supplierId", "supplierName"],
          "joinKeys": ["componentId"],
          "inputBindings": {}
        },
        {
          "id": "sql_component_stock",
          "tool": "sql",
          "question": "앞 단계에서 확인한 componentId별 makeflag와 현재 재고를 조회한다.",
          "dependsOn": ["graph_bom_supply"],
          "inputBindings": {"componentIds": "graph_bom_supply.componentId"},
          "requiredOutputs": ["componentId", "makeFlag", "actualStock"],
          "joinKeys": ["componentId"]
        }
      ],
      "resultTransform": {"type": "bom_shortage_v1", "productionQty": 10}
    }"""
    cypher = """MATCH (finished:Product {productId: 680})
MATCH path = (finished)-[:REQUIRES_COMPONENT*1..4]->(component:Product)
WHERE all(rel IN relationships(path)
  WHERE rel.startDate <= date('2014-08-08')
    AND (rel.endDate IS NULL OR date('2014-08-08') < rel.endDate))
  AND all(node IN nodes(path)
    WHERE single(other IN nodes(path) WHERE other.productId = node.productId))
OPTIONAL MATCH (supplier:Supplier)-[:SUPPLIES]->(component)
WHERE supplier.active = true
RETURN finished.productId AS finishedProductId,
  finished.name AS finishedProductName,
  component.productId AS componentId,
  component.name AS componentName,
  length(path) AS depth,
  [node IN nodes(path) | node.productId] AS pathProductIds,
  [rel IN relationships(path) | rel.quantityPerAssembly] AS quantityPerAssembly,
  supplier.supplierId AS supplierId,
  supplier.name AS supplierName
ORDER BY componentId, depth, pathProductIds, supplierId"""
    sql = """SELECT p.productid AS "componentId",
  p.makeflag AS "makeFlag",
  COALESCE(SUM(i.quantity), 0) AS "actualStock"
FROM production.product AS p
LEFT JOIN production.productinventory AS i ON i.productid = p.productid
WHERE p.productid IN (
  316, 324, 325, 326, 327, 331, 350, 399, 478, 482, 483,
  484, 485, 486, 487, 492, 531, 532, 533, 534, 804
)
GROUP BY p.productid, p.makeflag
ORDER BY p.productid"""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {
                "entityType": "product",
                "entityName": "HL Road Frame - Black, 58",
            },
        ),
        make_content_response(route_plan),
        make_content_response(cypher),
        make_content_response(sql),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {
            "query": (
                "완제품 HL Road Frame - Black, 58을 10개 생산할 때 부족한 부품, "
                "부족 수량과 공급 가능한 업체를 알려줘."
            )
        }
    )

    graph_rows = result["graph_result"]["result"]
    sql_rows = result["sql_result"]["result"]
    assert len(graph_rows) == 25
    assert len(sql_rows) == 21
    assert len({row["componentId"] for row in graph_rows}) == 21

    composed = result["composed_result"]
    assert composed["mode"] == "joined"
    assert composed["transform"] == "bom_shortage_v1"
    assert composed["error"] is None
    assert composed["total_count"] == 1
    assert composed["truncated"] is False
    assert composed["rows"] == [
        {
            "finishedProductId": 680,
            "finishedProductName": "HL Road Frame - Black, 58",
            "productionQty": Decimal("10.000000"),
            "componentId": 492,
            "componentName": "Paint - Black",
            "requiredQty": Decimal("80.000000"),
            "actualStock": Decimal("47.000000"),
            "shortageQty": Decimal("33.000000"),
            "suppliers": [
                {"supplierId": 1584, "supplierName": "Trey Research"},
                {"supplierId": 1692, "supplierName": "Carlson Specialties"},
            ],
        }
    ]
    assert result["final_answer"] == f"COMPOSED: {composed}"


async def test_graph_composes_canonical_rq20_one_to_many(postgres_pool) -> None:
    """RQ20 SQL 1행에 GRAPH 공정 2행을 sequence 순서로 결합한다."""
    route_plan = """{
      "tool_plan": ["sql", "graph"],
      "subqueries": [
        {
          "id": "sql_scrap_facts",
          "tool": "sql",
          "question": "작업지시 17747의 제품, 폐기 수량과 폐기사유를 조회한다.",
          "dependsOn": [],
          "requiredOutputs": ["workOrderId", "productId", "productName", "scrappedQty", "scrapReasonId", "scrapReasonName"],
          "joinKeys": ["workOrderId"],
          "inputBindings": {}
        },
        {
          "id": "graph_operations",
          "tool": "graph",
          "question": "작업지시 17747의 공정, 작업장과 공정 순서를 조회한다.",
          "dependsOn": [],
          "requiredOutputs": ["workOrderId", "routingOperationKey", "sequence", "locationId", "locationName"],
          "joinKeys": ["workOrderId"],
          "inputBindings": {}
        }
      ],
      "resultTransform": null
    }"""
    sql = """SELECT w.workorderid AS "workOrderId",
  w.productid AS "productId",
  p.name AS "productName",
  w.scrappedqty AS "scrappedQty",
  r.scrapreasonid AS "scrapReasonId",
  r.name AS "scrapReasonName"
FROM production.workorder AS w
JOIN production.product AS p ON p.productid = w.productid
LEFT JOIN production.scrapreason AS r ON r.scrapreasonid = w.scrapreasonid
WHERE w.workorderid = 17747
ORDER BY w.workorderid"""
    cypher = """MATCH (workOrder:WorkOrder {workOrderId: 17747})
  -[:HAS_OPERATION]->(operation:RoutingOperation)
  -[:PERFORMED_AT]->(location:Location)
RETURN workOrder.workOrderId AS workOrderId,
  operation.routingOperationKey AS routingOperationKey,
  operation.sequence AS sequence,
  location.locationId AS locationId,
  location.name AS locationName
ORDER BY sequence, routingOperationKey"""
    openai_client = MockOpenAIClient(
        make_content_response("[]"),
        make_content_response(route_plan),
        make_content_response(sql),
        make_content_response(cypher),
    )
    graph = build_orchestrator_graph(openai_client, postgres_pool)

    result = await graph.ainvoke(
        {
            "query": (
                "작업지시 17747의 생산 제품, 폐기 수량과 폐기사유, "
                "거친 공정과 작업장을 알려줘."
            )
        }
    )

    sql_rows = result["sql_result"]["result"]
    graph_rows = result["graph_result"]["result"]
    assert len(sql_rows) == 1
    assert len(graph_rows) == 2

    composed = result["composed_result"]
    assert composed["mode"] == "joined"
    assert composed["error"] is None
    assert composed["total_count"] == 2
    assert len(composed["rows"]) == 2
    assert [row["sequence"] for row in composed["rows"]] == [
        row["sequence"] for row in graph_rows
    ]
    sql_fact = {
        key: value for key, value in sql_rows[0].items() if key != "workOrderId"
    }
    assert all(
        all(row[key] == value for key, value in sql_fact.items())
        for row in composed["rows"]
    )
    assert result["final_answer"] == f"COMPOSED: {composed}"
