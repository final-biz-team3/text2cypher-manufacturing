"""Text-to-Cypher 프롬프트 구성 동작을 테스트한다."""

import json

import pytest

from agents.cypher.prompt import build_cypher_prompt
from agents.cypher.schema.models import GraphQueryPolicy


def test_build_cypher_prompt_adds_neo4j_policy_and_dynamic_context() -> None:
    """Cypher 고유 규칙과 요청별 그래프 스키마·업무 문맥을 포함한다."""
    messages = build_cypher_prompt(
        query="이 부품을 사용하는 완제품을 알려줘.",
        entity={"productId": 492},
        schema_text="(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        query_policy=GraphQueryPolicy(
            bomAsOfDate="2014-08-08",
            bomMaxDepth=4,
        ),
        business_rules=["같은 완제품의 서로 다른 경로는 별도로 보존한다."],
        required_outputs=["componentId", "minDepth"],
    )

    system_content = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "Neo4j Cypher" in system_content
    assert "관계 방향" in system_content
    assert "RETURN 절" in system_content
    assert "lowerCamelCase" in system_content
    assert "한국어 표시명 대신" in system_content
    assert "(:Product)-[:REQUIRES_COMPONENT]->(:Product)" in system_content
    assert "상위 조립품에서 하위 부품 방향" in system_content
    assert "부품의 사용처는 역방향" in system_content
    assert "완제품의 하위 부품" in system_content
    assert "anchor에서 destination 방향" in system_content
    assert "reverse(nodes(path))로 부품→완제품 순서" in system_content
    assert "BOM 가변 길이 경로는 최대 4단계" in system_content
    assert "[:REQUIRES_COMPONENT*1..4]" in system_content
    assert "상한 없는 가변 경로" in system_content
    assert "Cypher 25 전용 quantified path" in system_content
    assert "2014-08-08 기준" in system_content
    assert "date('2014-08-08') 형태만" in system_content
    assert "DATE '2014-08-08' 형태는 사용하지" in system_content
    assert "startDate <= 기준일" in system_content
    assert "기준일 < endDate" in system_content
    assert "MATCH 직후 relationships(path)" in system_content
    assert "length(path)" in system_content
    assert "size()는 list 또는 string" in system_content
    assert "WITH projection" in system_content
    assert "nodes(path)와 relationships(path)는 expression" in system_content
    assert "전체 pattern에 path를 할당" in system_content
    assert "quantityPerAssembly 결과는 경로 순서대로" in system_content
    assert "부족량 계산은 composer" in system_content
    assert "sellableFinishedGood = true" in system_content
    assert "시작·도착 Product의 ID·이름" in system_content
    assert "전체 Product ID·이름 경로" in system_content
    assert "Product node 자체가 아니라 productId 값으로 비교" in system_content
    assert "NOT expression IN list" in system_content
    assert "min(length(pathA)) AS minDepthA" in system_content
    assert "min(length(pathB)) AS minDepthB" in system_content
    assert "pathA와 pathB를 같은 MATCH 절에 두지 않고" in system_content
    assert "계산하지 않은 depth alias" in system_content
    assert "OPTIONAL MATCH" in system_content
    assert "ORDER BY에서 사용하는 계산 alias" in system_content
    assert "깊이, 도착 Product ID, 전체 ID 경로 순" in system_content
    assert "- 같은 완제품의 서로 다른 경로는 별도로 보존한다." in system_content
    assert "Required output aliases:" in system_content
    assert "- componentId" in system_content
    assert "- minDepth" in system_content
    assert "Cypher만 반환" in system_content
    assert "CALL과 APOC" in system_content
    assert "MATCH, OPTIONAL MATCH 또는 UNWIND" in system_content
    assert "추가 alias를 반환하지" in system_content
    assert (
        "ALL(node IN nodes(path) WHERE single(other IN nodes(path) WHERE "
        "other.productId = node.productId))" in system_content
    )
    assert "endpoint와 anchor를 모든 WITH" in system_content
    assert "min(length(path)) AS minDepth" in system_content
    assert "가변 관계 대괄호 안에는 변수명을 두지 않는다" in system_content
    assert "finishedProductId는 finished/root anchor" in system_content
    assert "[node IN reverse(nodes(path)) | node.productId]" in system_content
    assert "nodes(reverse(path))는 사용하지 않는다" in system_content
    assert "지정된 공급업체는 supplier.active = true" in system_content
    assert "작업지시·라우팅 공정 질문의 숫자는 WorkOrder.workOrderId" in system_content
    assert "ORDER BY에는 RETURN의 alias를 철자 그대로" in system_content
    assert "집계값 정렬 뒤에 반환된 identity ID alias를 ASC" in system_content
    assert "deterministic tie-break를 적용한 다음 LIMIT" in system_content
    assert "전각 또는 CJK 문장부호" in system_content
    assert json.loads(messages[1]["content"]) == {
        "query": "이 부품을 사용하는 완제품을 알려줘.",
        "entity": {"productId": 492},
    }


@pytest.mark.parametrize(
    ("query", "entity", "required_outputs", "input_bindings"),
    [
        (
            "부품 Ink Azure에서 역방향으로 영향을 받는 완제품 경로를 찾아줘.",
            {"productId": 8101, "productName": "Ink Azure"},
            ["componentId", "finishedProductId", "depth", "pathProductIds"],
            None,
        ),
        (
            "완제품 Comet Frame에서 모든 부품 경로와 경로별 수량 배열을 찾아줘.",
            {"productId": 8201, "productName": "Comet Frame"},
            ["componentId", "depth", "pathProductIds", "quantityPerAssembly"],
            None,
        ),
        (
            "완제품 Cedar Bike와 Quartz Bike의 공통 부품과 각각의 최소 깊이를 찾아줘.",
            [
                {"productId": 8301, "productName": "Cedar Bike"},
                {"productId": 8302, "productName": "Quartz Bike"},
            ],
            ["componentId", "leftMinDepth", "rightMinDepth"],
            None,
        ),
        (
            "공급업체 North Mill이 공급하는 부품이 쓰이는 완제품을 찾아줘.",
            {"supplierId": 8401, "supplierName": "North Mill"},
            ["supplierId", "componentId", "finishedProductId"],
            None,
        ),
        (
            "작업지시 8501의 공정 순서와 작업장을 찾아줘.",
            {"workOrderId": 8501},
            ["workOrderId", "sequence", "locationId"],
            None,
        ),
        (
            "앞 단계 부품들에서 도달 가능한 완제품을 찾아줘.",
            None,
            ["componentId", "finishedProductId"],
            {"componentIds": [8601, 8602]},
        ),
    ],
    ids=(
        "reverse-bom-impact",
        "forward-bom-quantities",
        "common-components-min-depth",
        "supplier-component-finished-product",
        "work-order-operation-location",
        "bound-id-array",
    ),
)
def test_cypher_prompt_keeps_synthetic_query_family_contracts(
    query: str,
    entity: object | None,
    required_outputs: list[str],
    input_bindings: dict[str, list[int]] | None,
) -> None:
    """대표 family의 질문·entity·alias·binding이 손실 없이 prompt에 남는다."""
    messages = build_cypher_prompt(
        query=query,
        entity=entity,
        schema_text=(
            "(:WorkOrder)-[:HAS_OPERATION]->(:Operation)-[:AT_LOCATION]->(:Location)\n"
            "(:Product)-[:REQUIRES_COMPONENT]->(:Product)\n"
            "(:Supplier)-[:SUPPLIES]->(:Product)"
        ),
        query_policy=GraphQueryPolicy(
            bomAsOfDate="2014-08-08",
            bomMaxDepth=4,
        ),
        required_outputs=required_outputs,
        input_bindings=input_bindings,
    )

    system_content = messages[0]["content"]
    user_content = json.loads(messages[1]["content"])
    expected_context = {"query": query, "entity": entity}
    if input_bindings is not None:
        expected_context["inputBindings"] = input_bindings
    assert user_content == expected_context
    assert all(f"- {alias}" in system_content for alias in required_outputs)
    if input_bindings is not None:
        assert "Input bindings" in system_content
        assert user_content["inputBindings"] == {"componentIds": [8601, 8602]}
