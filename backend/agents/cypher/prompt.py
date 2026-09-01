"""제조 데이터 질문을 Neo4j Cypher로 변환하기 위한 프롬프트를 구성한다."""

from collections.abc import Sequence
from typing import Any

from agents.cypher.schema.models import GraphQueryPolicy
from agents.prompt import build_prompt_messages

_CYPHER_INSTRUCTIONS = """당신은 제조 데이터용 Neo4j Cypher 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 Cypher 문으로 변환합니다.

- 제공된 스키마의 노드, 관계와 속성만 사용합니다.
- 관계 방향을 제공된 스키마와 동일하게 사용합니다.
- 결과를 반환하는 RETURN 절을 포함합니다.
- RETURN alias는 한국어 표시명 대신 속성명 또는 의미가 분명한 영어
  lowerCamelCase를 사용합니다.
- required output 목록이 있으면 그 alias를 모두 정확히 반환하고 추가 alias를 반환하지
  않습니다.
- 확정된 entity가 있으면 해당 식별자를 우선 사용합니다.
- 제공된 업무 규칙이 있으면 쿼리에 반영합니다.
- CREATE, MERGE, SET, DELETE, REMOVE 같은 쓰기 절을 사용하지 않습니다.
- CALL과 APOC 프로시저를 사용하지 않으며 첫 절은 MATCH, OPTIONAL MATCH 또는 UNWIND 중 하나입니다.
- Cypher 구문에는 전각 또는 CJK 문장부호를 쓰지 않습니다.
- 스키마에 없는 노드, 관계 또는 속성을 추측하지 않습니다.
- 설명, 주석 또는 Markdown 없이 Cypher만 반환합니다."""


def _build_cypher_domain_rules(query_policy: GraphQueryPolicy) -> tuple[str, ...]:
    """그래프 스키마의 BOM 정책을 REQUIRES_COMPONENT 생성 규칙으로 변환한다."""
    return (
        "REQUIRES_COMPONENT는 상위 조립품에서 하위 부품 방향이므로, "
        "부품의 사용처는 역방향으로, 완제품의 하위 부품은 정방향으로 탐색한다. "
        "경로와 pathProductIds·quantityPerAssembly 같은 결과 배열은 모두 "
        "질문의 anchor에서 destination 방향으로 반환한다.",
        "부품에서 완제품으로 사용처를 찾을 때 MATCH path는 schema 방향인 "
        "완제품→부품으로 작성하더라도 pathProductIds와 pathProductNames는 "
        "reverse(nodes(path))로 부품→완제품 순서를 반환한다. ID 배열은 "
        "[node IN reverse(nodes(path)) | node.productId]로 만든다. reverse(path)나 "
        "nodes(reverse(path))는 사용하지 않는다.",
        f"BOM 가변 길이 경로는 최대 {query_policy.bom_max_depth}단계이며, "
        f"질문에 1~{query_policy.bom_max_depth} 범위의 깊이가 명시되면 그 값을 "
        "사용한다. Neo4j 5 호환 문법인 "
        f"[:REQUIRES_COMPONENT*1..{query_policy.bom_max_depth}]를 사용하고 "
        "상한 없는 가변 경로 또는 Cypher 25 전용 quantified path 문법은 사용하지 않는다.",
        "경로가 필요하면 MATCH path=(start)-[:REQUIRES_COMPONENT*..]->(end)처럼 "
        "전체 pattern에 path를 할당한다. [path:REQUIRES_COMPONENT*..]처럼 가변 관계 "
        "리스트에 path를 할당하지 않고 가변 관계 대괄호 안에는 변수명을 두지 않는다.",
        "BOM 경로의 모든 REQUIRES_COMPONENT 관계는 "
        f"{query_policy.bom_as_of_date} 기준으로 startDate <= 기준일이고 "
        "endDate가 없거나 기준일 < endDate여야 한다. 이 validity predicate는 "
        "MATCH 직후 relationships(path)에 적용해 다른 집계·조인보다 먼저 필터링한다. "
        f"날짜는 date('{query_policy.bom_as_of_date}') 형태만 사용하고 SQL식 "
        f"DATE '{query_policy.bom_as_of_date}' 형태는 사용하지 않는다.",
        "경로 길이는 length(path)로 계산한다. size()는 list 또는 string 길이에만 "
        "사용하며 path 자체에 사용하지 않는다.",
        "WITH 뒤에서 참조할 변수와 계산 alias는 모두 WITH projection에 포함한다. "
        "경로 endpoint와 anchor를 모든 WITH projection에서 유지한다. nodes(path)와 "
        "relationships(path)는 expression으로만 사용하고 MATCH pattern처럼 변수에 "
        "할당하지 않는다.",
        "경로 내 node uniqueness는 Product node 자체가 아니라 productId 값으로 비교하고 "
        "다음 Neo4j 5 문법을 사용한다: ALL(node IN nodes(path) WHERE single(other IN "
        "nodes(path) WHERE other.productId = node.productId)). APOC, index range, list slice나 "
        "NOT expression IN list 형태로 다시 작성하지 않는다.",
        "quantityPerAssembly 결과는 경로 순서대로 relationships(path)의 각 수량을 담은 "
        "배열이다. 이를 곱하거나 productionQty를 반영하지 않으며 부족량 계산은 composer가 "
        "담당한다.",
        "inputBindings의 단일 ID 배열은 선행 결과의 행 중복을 보존할 수 있지만 그래프 "
        "탐색 대상을 반복하라는 뜻이 아니다. ID 배열을 필터로 사용할 때는 UNWIND한 뒤 "
        "WITH DISTINCT로 ID를 집합화하고 MATCH한다.",
        "두 anchor의 공통 부품은 pathA를 MATCH한 뒤 component별 "
        "min(length(pathA)) AS minDepthA를 먼저 집계하고, 별도 MATCH 절에서 pathB를 "
        "찾아 min(length(pathB)) AS minDepthB를 집계한다. pathA와 pathB를 같은 MATCH "
        "절에 두지 않고 계산하지 않은 depth alias를 참조하지 않는다.",
        "최소 깊이만 요구되면 WITH destination, min(length(path)) AS minDepth로 destination당 "
        "한 행만 반환하고 경로 alias를 추가하지 않는다.",
        "BOM component와 공급업체를 함께 반환할 때는 OPTIONAL MATCH로 공급업체가 없는 "
        "component 행도 보존하고 active 조건은 OPTIONAL MATCH 안에 적용한다.",
        "질문에서 지정된 공급업체는 supplier.active = true로 제한한다.",
        "특정 작업장을 거친 제품은 하나의 WorkOrder에서 HAS_OPERATION, PERFORMED_AT과 "
        "PRODUCES를 연결하고 같은 WorkOrder 경로를 중복해서 만들지 않는다.",
        "작업지시·라우팅 공정 질문의 숫자는 WorkOrder.workOrderId로 해석하고 Product의 "
        "productId로 사용하지 않는다.",
        "ORDER BY에서 사용하는 계산 alias는 최종 RETURN에도 같은 alias로 포함한다. "
        "ORDER BY에는 RETURN의 alias를 철자 그대로 사용하고 새 식별자를 만들지 않는다.",
        "집계 top-N은 집계값 정렬 뒤에 반환된 identity ID alias를 ASC로 나열해 "
        "deterministic tie-break를 적용한 다음 LIMIT한다.",
        "REQUIRES_COMPONENT 탐색에서 완제품을 반환하면 "
        "sellableFinishedGood = true로 제한한다. 해당 계층·경로 결과에는 "
        "질문과 requiredOutputs가 요구한 시작·도착 Product의 ID·이름, 깊이와 전체 "
        "Product ID·이름 경로만 포함하고 서로 다른 경로를 합치지 않는다. 여러 경로는 깊이, 도착 "
        "Product ID, 전체 ID 경로 순으로 정렬한다.",
        "BOM 역할 alias를 바꾸지 않는다. finishedProductId는 finished/root anchor에서, "
        "componentId는 destination component에서 반환한다.",
    )


def build_cypher_prompt(
    *,
    query: str,
    entity: object | None,
    schema_text: str,
    query_policy: GraphQueryPolicy,
    business_rules: Sequence[str] = (),
    required_outputs: Sequence[str] = (),
    input_bindings: dict[str, list[Any]] | None = None,
    previous_query: str | None = None,
    previous_error: str | None = None,
) -> list[dict[str, str]]:
    """현재 질의 문맥을 포함한 Neo4j Cypher 생성 메시지를 반환한다."""
    return build_prompt_messages(
        instructions=_CYPHER_INSTRUCTIONS,
        query=query,
        entity=entity,
        schema_text=schema_text,
        business_rules=(*_build_cypher_domain_rules(query_policy), *business_rules),
        required_outputs=required_outputs,
        input_bindings=input_bindings,
        previous_query=previous_query,
        previous_error=previous_error,
    )
