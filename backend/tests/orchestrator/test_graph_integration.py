"""RQ01·RQ02·RQ08·RQ12·RQ13이 엔티티 확정과 라우팅을 거쳐
SQL 또는 Cypher로 생성되는지 실제 OpenAI/PostgreSQL로 검증한다.
생성된 쿼리를 DB에 실행하지는 않는다.

fixture 값(productId 956/747/492 등)은 pr-16의 query_parameters.json
(entities.pricedProduct 등)에서 가져왔다.
"""

import re

import pytest
from dotenv import load_dotenv

# core.openai_client / core.postgres는 os.getenv()로 환경변수를 직접 읽으며
# load_dotenv()를 호출하지 않는다(backend/main.py만 호출한다). 이 테스트는
# main.py를 import하지 않으므로 여기서 직접 .env를 로드해야 한다.
load_dotenv()

from core.openai_client import get_openai_client  # noqa: E402
from core.postgres import bootstrap_postgres, get_pool, open_pool  # noqa: E402
from orchestrator.graph import build_orchestrator_graph  # noqa: E402
from tests.orchestrator.cypher_assertions import (  # noqa: E402
    has_product_id_path_uniqueness_guard,
)

pytestmark = pytest.mark.integration


def _normalized(query: str) -> str:
    unquoted = query.lower().replace('"', "").replace("`", "")
    return " ".join(unquoted.split())


def _compact(query: str) -> str:
    return re.sub(r"\s+", "", _normalized(query))


def _assert_contains(query: str | None, *parts: str) -> None:
    assert query is not None
    normalized = _normalized(query)
    for part in parts:
        assert part.lower() in normalized


def _assert_relationship_direction(
    query: str | None,
    *,
    product_id: int,
    reverse: bool,
) -> None:
    """질문의 시작 Product를 기준으로 BOM 탐색 방향과 깊이를 확인한다."""
    assert query is not None
    compact = _compact(query)
    node = r"\((?P<{}>[a-z_]\w*):product(?:\{{[^}}]*\}})?\)"
    relationship = r"\[[^]]*:requires_component\*1\.\.4[^]]*\]"
    forward_pattern = (
        node.format("left") + r"-" + relationship + r"->" + node.format("right")
    )
    reverse_pattern = (
        node.format("left") + r"<-" + relationship + r"-" + node.format("right")
    )

    match = re.search(forward_pattern, compact)
    if match:
        parent_variable = match.group("left")
        component_variable = match.group("right")
    else:
        match = re.search(reverse_pattern, compact)
        assert match
        parent_variable = match.group("right")
        component_variable = match.group("left")

    expected_start = component_variable if reverse else parent_variable
    normalized = _normalized(query)
    uses_property_map = bool(
        re.search(
            rf"\(\s*{re.escape(expected_start)}\s*:\s*product\s*\{{"
            rf"[^}}]*\bproductid\s*:\s*{product_id}\b",
            normalized,
        )
    )
    uses_where_equality = bool(
        re.search(
            rf"\b{re.escape(expected_start)}\s*\.\s*productid\s*=\s*"
            rf"{product_id}\b",
            normalized,
        )
    )
    assert uses_property_map or uses_where_equality


def _assert_bom_path_contract(query: str | None) -> None:
    """BOM 경로가 기준일·경로 반환·결정적 정렬 계약을 포함하는지 확인한다."""
    assert query is not None
    _assert_contains(
        query,
        "nodes(",
        "productid",
        "name",
        "startdate",
        "enddate",
        "order by",
    )
    compact = _compact(query)
    reference_date = "date('2014-08-08')"
    assert reference_date in compact
    assert re.search(
        rf"[a-z_]\w*\.startdate<={re.escape(reference_date)}",
        compact,
    )
    assert re.search(
        rf"[a-z_]\w*\.enddateisnullor{re.escape(reference_date)}"
        r"<[a-z_]\w*\.enddate",
        compact,
    )
    uses_path_relationships = bool(re.search(r"all\([^)]*inrelationships\(", compact))
    relationship_binding = re.search(
        r"\[(?P<name>[a-z_]\w*):requires_component\*1\.\.4",
        compact,
    )
    uses_bound_relationships = bool(
        relationship_binding
        and re.search(
            rf"all\([a-z_]\w*in{relationship_binding.group('name')}where",
            compact,
        )
    )
    assert uses_path_relationships or uses_bound_relationships
    assert re.search(r"length\([^)]+\)asdepth", compact)


def _assert_no_repeated_product_in_path(query: str | None) -> None:
    """한 경로 안에서 동일 Product를 다시 방문하지 않는 조건을 확인한다."""
    assert query is not None
    assert has_product_id_path_uniqueness_guard(query)


def _assert_start_and_destination_fields(query: str | None) -> None:
    """질문별 alias 이름과 무관하게 시작·도착 Product 필드를 확인한다."""
    assert query is not None
    compact = _compact(query)
    product_id_aliases = set(re.findall(r"as([a-z_]*productid)\b", compact))
    product_name_aliases = set(re.findall(r"as([a-z_]*productname)\b", compact))
    product_id_aliases.discard("productidpath")
    product_name_aliases.discard("productnamepath")
    assert len(product_id_aliases) >= 2
    assert len(product_name_aliases) >= 2


def _assert_cypher_product_filter(query: str | None, product_id: int) -> None:
    """Product 시작 조건이 속성 맵 또는 WHERE 동등식으로 적용됐는지 확인한다."""
    assert query is not None
    compact = _compact(query)
    uses_property_map = f"productid:{product_id}" in compact
    uses_where_equality = bool(
        re.search(rf"[a-z_]\w*\.productid={product_id}(?!\d)", compact)
    )
    assert uses_property_map or uses_where_equality


@pytest.fixture
async def graph():
    openai_client = get_openai_client()
    await bootstrap_postgres()
    await open_pool()
    return build_orchestrator_graph(openai_client, get_pool())


async def test_rq01_priced_product_routes_to_sql(graph) -> None:
    """RQ01: 정가·표준원가 조회는 productId 956로 확정되고 sql로 라우팅된다."""
    result = await graph.ainvoke(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["tool_plan"] == ["sql"]
    _assert_contains(
        result["sql_query"],
        "select",
        "production.product",
        "productid",
        "name",
        "listprice",
        "standardcost",
        "956",
    )
    compact_sql = _compact(result["sql_query"])
    assert re.search(r"(?:[a-z_]\w*\.)?productid=956(?!\d)", compact_sql)
    assert result["cypher_query"] is None


async def test_rq02_multi_location_product_routes_to_sql(graph) -> None:
    """RQ02: 재고 위치 조회는 productId 747로 확정되고 sql로 라우팅된다."""
    result = await graph.ainvoke(
        {"query": "HL Mountain Frame - Black, 38의 재고 위치와 위치별 수량을 알려줘."}
    )

    assert result["entity"] == {
        "productId": 747,
        "productName": "HL Mountain Frame - Black, 38",
    }
    assert result["tool_plan"] == ["sql"]
    _assert_contains(
        result["sql_query"],
        "select",
        "production.product",
        "production.productinventory",
        "production.location",
        "productid",
        "name",
        "locationid",
        "shelf",
        "bin",
        "quantity",
        "747",
        "order by",
    )
    normalized_sql = _normalized(result["sql_query"])
    assert "sum(" not in normalized_sql
    assert "group by" not in normalized_sql
    compact_sql = _compact(result["sql_query"])
    assert re.search(r"(?:[a-z_]\w*\.)?productid=747(?!\d)", compact_sql)
    order_by = compact_sql.split("orderby", maxsplit=1)[1]
    assert (
        order_by.index("locationid") < order_by.index("shelf") < order_by.index("bin")
    )
    assert result["cypher_query"] is None


async def test_rq08_stock_shortage_routes_to_sql(graph) -> None:
    """RQ08: 안전·실제·부족 재고 질의를 계산 SQL로 생성한다."""
    result = await graph.ainvoke(
        {"query": "제품 Paint - Black의 안전재고, 실제 재고와 부족 수량을 알려줘."}
    )

    assert result["entity"] == {"productId": 492, "productName": "Paint - Black"}
    assert result["tool_plan"] == ["sql"]
    _assert_contains(
        result["sql_query"],
        "production.productinventory",
        "productid",
        "name",
        "safetystocklevel",
        "left join",
        "coalesce",
        "sum",
        "quantity",
        "greatest",
        "492",
    )
    compact_sql = _compact(result["sql_query"])
    assert re.search(r"(?:[a-z_]\w*\.)?productid=492(?!\d)", compact_sql)
    assert re.search(
        r"coalesce\(sum\([^)]*quantity[^)]*\),0\)",
        compact_sql,
    )
    shortage_expression = compact_sql[compact_sql.index("greatest(") :]
    assert "safetystocklevel-" in shortage_expression
    shortage_rhs = shortage_expression.split("safetystocklevel-", maxsplit=1)[1]
    subtracts_actual_stock = shortage_rhs.startswith("coalesce(sum(") or bool(
        re.match(r"[a-z0-9_.]*(?:actual|stock)[a-z0-9_.]*", shortage_rhs)
    )
    assert subtracts_actual_stock
    if shortage_rhs.startswith("coalesce(sum("):
        assert shortage_expression.count(",0)") >= 2
    else:
        assert ",0)" in shortage_expression
    assert result["cypher_query"] is None


async def test_rq12_component_usage_routes_to_graph(graph) -> None:
    """RQ12: 부품 사용처를 4단계까지 묻는 질의는 productId 492로 확정되고
    graph로 라우팅된다."""
    result = await graph.ainvoke(
        {"query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘."}
    )

    assert result["entity"] == {"productId": 492, "productName": "Paint - Black"}
    assert result["tool_plan"] == ["graph"]
    assert result["sql_query"] is None
    _assert_contains(
        result["cypher_query"],
        "match",
        "requires_component",
        "return",
        "492",
        "4",
    )
    _assert_relationship_direction(result["cypher_query"], product_id=492, reverse=True)
    _assert_cypher_product_filter(result["cypher_query"], 492)
    _assert_bom_path_contract(result["cypher_query"])
    _assert_start_and_destination_fields(result["cypher_query"])
    _assert_contains(result["cypher_query"], "sellablefinishedgood", "true")


async def test_rq13_finished_product_components_route_to_graph(graph) -> None:
    """RQ13: 완제품의 하위 부품을 최대 4단계 Cypher로 생성한다."""
    result = await graph.ainvoke(
        {
            "query": (
                "완제품 HL Road Frame - Black, 58의 하위 부품을 "
                "최대 4단계까지 계층 구조로 알려줘."
            )
        }
    )

    assert result["entity"] == {
        "productId": 680,
        "productName": "HL Road Frame - Black, 58",
    }
    assert result["tool_plan"] == ["graph"]
    assert result["sql_query"] is None
    _assert_contains(
        result["cypher_query"],
        "match",
        "requires_component",
        "return",
        "680",
        "4",
    )
    _assert_relationship_direction(
        result["cypher_query"], product_id=680, reverse=False
    )
    _assert_cypher_product_filter(result["cypher_query"], 680)
    _assert_bom_path_contract(result["cypher_query"])
    _assert_no_repeated_product_in_path(result["cypher_query"])
    _assert_start_and_destination_fields(result["cypher_query"])
