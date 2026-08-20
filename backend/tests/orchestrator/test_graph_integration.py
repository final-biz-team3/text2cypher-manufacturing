"""RQ01~04(SQL), RQ12(GRAPH) 5개 질의가 resolve_entity -> route_query를
실제 OpenAI/PostgreSQL로 올바르게 통과하는지 검증한다.

query_parameters.json의 fixture(entities.pricedProduct 등)를 그대로 쓴다.
"""

import pytest
from dotenv import load_dotenv

# core.openai_client / core.postgres는 os.getenv()로 환경변수를 직접 읽으며
# load_dotenv()를 호출하지 않는다(backend/main.py만 호출한다). 이 테스트는
# main.py를 import하지 않으므로 여기서 직접 .env를 로드해야 한다.
load_dotenv()

from core.openai_client import get_openai_client  # noqa: E402
from core.postgres import get_connection  # noqa: E402
from orchestrator.graph import build_orchestrator_graph  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def graph():
    openai_client = get_openai_client()
    postgres_connection = get_connection()
    return build_orchestrator_graph(openai_client, postgres_connection)


def test_rq01_priced_product_routes_to_sql(graph) -> None:
    """RQ01: 정가·표준원가 조회는 productId 956로 확정되고 sql로 라우팅된다."""
    result = graph.invoke(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["tool_plan"] == ["sql"]


def test_rq02_multi_location_product_routes_to_sql(graph) -> None:
    """RQ02: 재고 위치 조회는 productId 747로 확정되고 sql로 라우팅된다."""
    result = graph.invoke(
        {"query": "HL Mountain Frame - Black, 38의 재고 위치와 위치별 수량을 알려줘."}
    )

    assert result["entity"] == {
        "productId": 747,
        "productName": "HL Mountain Frame - Black, 38",
    }
    assert result["tool_plan"] == ["sql"]


def test_rq03_active_supplier_count_routes_to_sql_without_entity(graph) -> None:
    """RQ03: 특정 제품을 지칭하지 않는 집계 질의는 entity=None, sql로 라우팅된다."""
    result = graph.invoke({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]


def test_rq04_purchased_product_count_routes_to_sql_without_entity(graph) -> None:
    """RQ04: 외부구매 부품 수 집계도 entity=None, sql로 라우팅된다."""
    result = graph.invoke({"query": "외부에서 구매하는 부품 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]


def test_rq12_component_usage_routes_to_graph(graph) -> None:
    """RQ12: 부품 사용처를 4단계까지 묻는 질의는 productId 492로 확정되고
    graph로 라우팅된다."""
    result = graph.invoke(
        {"query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘."}
    )

    assert result["entity"] == {"productId": 492, "productName": "Paint - Black"}
    assert result["tool_plan"] == ["graph"]
