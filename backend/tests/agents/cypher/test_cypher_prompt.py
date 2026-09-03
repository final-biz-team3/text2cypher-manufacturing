"""Neo4j prompt provenance and graph-policy contract tests."""

import json

from agents.cypher.prompt import build_cypher_prompt
from agents.cypher.schema.models import GraphQueryPolicy


def _policy() -> GraphQueryPolicy:
    return GraphQueryPolicy(bomAsOfDate="2014-08-08", bomMaxDepth=4)


def test_cypher_prompt_separates_graph_policy_from_semantic_context() -> None:
    messages = build_cypher_prompt(
        query="가상 부품의 조립 관계를 알려줘.",
        entity={"productId": 4201},
        schema_text="(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        query_policy=_policy(),
        semantic_context=(
            "minDepth | kind=derived | operation=minimumPathLength | "
            "grain=componentId"
        ),
        business_rules=["선택한 anchor를 유지한다."],
        required_outputs=["componentId", "minDepth"],
    )

    system = messages[0]["content"]
    assert "Neo4j 5" in system
    assert "읽기 전용" in system
    assert "(:Product)-[:REQUIRES_COMPONENT]->(:Product)" in system
    assert "Semantic output catalog" in system
    assert "operation=minimumPathLength" in system
    assert "상위 조립품에서 하위 부품" in system
    assert "1..4" in system
    assert "date('2014-08-08')" in system
    assert "path node uniqueness" in system
    assert "독립적인 BOM 가변 경로는 각각 별도의 MATCH 절" in system
    assert "relationship uniqueness가 경로 사이에도 적용" in system
    assert "destination grain으로 먼저 집계" in system
    assert "nodes(path)는 MATCH에 작성한 시작점에서 끝점 순서" in system
    assert "projection에 reverse" in system
    assert "minimumPathLength" in system
    assert "- 선택한 anchor를 유지한다." in system
    assert "- componentId" in system
    assert "- minDepth" in system
    assert json.loads(messages[1]["content"]) == {
        "query": "가상 부품의 조립 관계를 알려줘.",
        "entity": {"productId": 4201},
    }


def test_cypher_prompt_keeps_aligned_input_rows() -> None:
    messages = build_cypher_prompt(
        query="선행 행의 값으로 관계를 조회해줘.",
        entity=None,
        schema_text="(:Product {productId: INTEGER})",
        query_policy=_policy(),
        required_outputs=["productId"],
        input_bindings={
            "componentIds": [4301, 4301, None],
            "quantities": [2, 3, 1],
        },
    )

    system = messages[0]["content"]
    user = json.loads(messages[1]["content"])
    assert "같은 row index" in system
    assert "중복과" in system
    assert "NULL" in system
    assert user["inputBindings"] == {
        "componentIds": [4301, 4301, None],
        "quantities": [2, 3, 1],
    }


def test_cypher_prompt_contains_no_query_family_completion_recipe() -> None:
    system = build_cypher_prompt(
        query="서로 다른 두 anchor의 공통 destination을 알려줘.",
        entity=None,
        schema_text="(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        query_policy=_policy(),
        semantic_context="minDepthA | operation=minimumPathLength",
    )[0]["content"]

    assert "min(length(pathA)) AS minDepthA" not in system
    assert "pathA와 pathB를 같은 MATCH 절" not in system
    assert "작업지시·라우팅 공정 질문의 숫자" not in system
    assert "지정된 공급업체는 supplier.active" not in system
