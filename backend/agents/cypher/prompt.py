"""Build a Neo4j prompt from graph policy and semantic provenance."""

from collections.abc import Sequence
from typing import Any

from agents.cypher.schema.models import GraphQueryPolicy
from agents.prompt import build_prompt_messages

_CYPHER_INSTRUCTIONS = """당신은 제조 데이터용 Neo4j Cypher 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 Cypher 문으로 변환합니다.

- 제공된 physical graph schema의 노드, 관계, 방향과 속성만 사용합니다.
- semantic output catalog의 alias, operation, inputs, grain을 사용해 요청된 업무
  개념을 구현합니다.
- 결과를 반환하는 RETURN 절을 포함합니다.
- required output 목록의 모든 alias를 정확히 반환하고 추가 alias를 반환하지
  않습니다.
- 확정된 entity가 있으면 해당 식별자를 우선 사용합니다.
- 원문의 filter, comparison, limit, date, quantity 조건을 의미 그대로 보존합니다.
- CREATE, MERGE, SET, DELETE, REMOVE 같은 쓰기 절을 사용하지 않습니다.
- CALL과 APOC를 사용하지 않으며 첫 절은 MATCH, OPTIONAL MATCH 또는 UNWIND 중
  하나입니다.
- Cypher에는 전각 또는 CJK 문장부호를 쓰지 않고 Neo4j 5 문법을 사용합니다.
- input binding 배열은 선행 결과의 같은 row index에 맞춰 정렬되어 있으며 중복과
  NULL을 보존합니다. 여러 배열을 소비할 때 이 alignment를 유지합니다. 단일 ID
  배열이 집합 filter라면 UNWIND 후 WITH DISTINCT로 탐색 대상을 중복 제거합니다.
- 스키마에 없는 노드, 관계 또는 속성을 추측하지 않습니다.
- 설명, 주석 또는 Markdown 없이 Cypher만 반환합니다."""


def _build_graph_policy_rules(query_policy: GraphQueryPolicy) -> tuple[str, ...]:
    """Render only syntax, direction, snapshot, and path-integrity policy."""
    return (
        "REQUIRES_COMPONENT 방향은 상위 조립품에서 하위 부품이다. 사용처는 "
        "역방향으로, 하위 부품은 정방향으로 탐색한다.",
        f"BOM 가변 경로는 1..{query_policy.bom_max_depth} 범위이며 Neo4j 5 "
        "relationship range 문법을 사용한다. 상한 없는 경로와 quantified path "
        "문법은 사용하지 않는다.",
        "경로는 전체 pattern에 할당하고 가변 relationship list에 path 변수를 "
        "할당하지 않는다.",
        f"모든 BOM relationship은 date('{query_policy.bom_as_of_date}') 기준으로 "
        "startDate <= 기준일이고 endDate가 null이거나 기준일 < endDate여야 한다. "
        "validity predicate는 MATCH 직후 relationships(path)에 적용한다.",
        "path 깊이는 length(path)로 계산한다. size(path)는 사용하지 않는다.",
        "WITH 이후 사용할 endpoint, anchor와 계산 alias를 모든 WITH projection에 "
        "명시적으로 유지한다.",
        "path node uniqueness는 productId 값으로 검사하고 Neo4j 5의 ALL + single "
        "list predicate를 사용한다. APOC나 list index range로 대체하지 않는다.",
        "서로 다른 anchor에서 같은 destination으로 향하는 독립적인 BOM 가변 "
        "경로는 각각 별도의 MATCH 절에서 탐색하고 destination 변수로 결합한다. "
        "한 MATCH의 comma-separated graph pattern으로 합치면 Neo4j 5의 "
        "relationship uniqueness가 경로 사이에도 적용되므로 사용하지 않는다. "
        "anchor별 minimumPathLength는 destination grain으로 먼저 집계한 뒤 다음 "
        "anchor 경로를 탐색한다.",
        "nodes(path)는 MATCH에 작성한 시작점에서 끝점 순서이며 그 물리 순서가 "
        "질문의 의미 anchor에서 destination 순서와 같다고 가정하지 않는다. "
        "orderedPathProjection은 semantic entity role order를 기준으로 하고, 물리 "
        "MATCH path가 반대면 nodes(path)의 projection에 reverse를 적용한다. "
        "relationship 수량 배열도 같은 의미 방향과 index 정렬을 유지한다.",
        "minimumPathLength는 destination grain별 min(length(path))로 계산한다.",
        "ORDER BY가 계산 alias를 사용하면 최종 RETURN에도 같은 alias를 정확히 "
        "포함한다.",
    )


def build_cypher_prompt(
    *,
    query: str,
    source_scope: str | None = None,
    entity: object | None,
    schema_text: str,
    query_policy: GraphQueryPolicy,
    semantic_context: str = "",
    business_rules: Sequence[str] = (),
    required_outputs: Sequence[str] = (),
    input_bindings: dict[str, list[Any]] | None = None,
    previous_query: str | None = None,
    previous_error: str | None = None,
) -> list[dict[str, str]]:
    """Return Cypher generation messages for one execution subquery."""
    return build_prompt_messages(
        instructions=_CYPHER_INSTRUCTIONS,
        query=query,
        source_scope=source_scope,
        entity=entity,
        schema_text=schema_text,
        semantic_context=semantic_context,
        business_rules=(*_build_graph_policy_rules(query_policy), *business_rules),
        required_outputs=required_outputs,
        input_bindings=input_bindings,
        previous_query=previous_query,
        previous_error=previous_error,
    )
