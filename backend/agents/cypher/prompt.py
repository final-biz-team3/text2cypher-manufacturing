"""제조 데이터 질문을 Neo4j Cypher로 변환하기 위한 프롬프트를 구성한다."""

from collections.abc import Sequence

from agents.cypher.schema.models import GraphQueryPolicy
from agents.prompt import build_prompt_messages

_CYPHER_INSTRUCTIONS = """당신은 제조 데이터용 Neo4j Cypher 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 Cypher 문으로 변환합니다.

- 제공된 스키마의 노드, 관계와 속성만 사용합니다.
- 관계 방향을 제공된 스키마와 동일하게 사용합니다.
- 결과를 반환하는 RETURN 절을 포함합니다.
- RETURN alias는 한국어 표시명 대신 속성명 또는 의미가 분명한 영어
  lowerCamelCase를 사용합니다.
- 확정된 entity가 있으면 해당 식별자를 우선 사용합니다.
- 제공된 업무 규칙이 있으면 쿼리에 반영합니다.
- CREATE, MERGE, SET, DELETE, REMOVE 같은 쓰기 절을 사용하지 않습니다.
- 스키마에 없는 노드, 관계 또는 속성을 추측하지 않습니다.
- 설명, 주석 또는 Markdown 없이 Cypher만 반환합니다."""


def _build_cypher_domain_rules(query_policy: GraphQueryPolicy) -> tuple[str, ...]:
    """그래프 스키마의 BOM 정책을 REQUIRES_COMPONENT 생성 규칙으로 변환한다."""
    return (
        "REQUIRES_COMPONENT는 상위 조립품에서 하위 부품 방향이므로, "
        "부품의 사용처는 역방향으로, 완제품의 하위 부품은 정방향으로 탐색한다.",
        f"BOM 가변 길이 경로는 최대 {query_policy.bom_max_depth}단계이며, "
        f"질문에 1~{query_policy.bom_max_depth} 범위의 깊이가 명시되면 그 값을 "
        "사용한다.",
        "BOM 경로의 모든 REQUIRES_COMPONENT 관계는 "
        f"{query_policy.bom_as_of_date} 기준으로 startDate <= 기준일이고 "
        "endDate가 없거나 기준일 < endDate여야 한다.",
        "REQUIRES_COMPONENT 탐색에서 완제품을 반환하면 "
        "sellableFinishedGood = true로 제한한다. 해당 계층·경로 결과에는 "
        "시작·도착 Product의 ID·이름, 깊이와 전체 Product ID·이름 경로를 "
        "포함하고 서로 다른 경로를 합치지 않는다. 하위 부품 계층에서는 한 "
        "경로에 같은 productId가 반복되지 않도록 경로 노드의 productId 목록을 "
        "기준으로 중복을 검사하며, productId 값과 Node 목록을 직접 비교하지 "
        "않는다. 여러 경로는 깊이, 도착 Product ID, 전체 ID 경로 순으로 "
        "정렬한다.",
    )


def build_cypher_prompt(
    *,
    query: str,
    entity: object | None,
    schema_text: str,
    query_policy: GraphQueryPolicy,
    business_rules: Sequence[str] = (),
    required_outputs: Sequence[str] = (),
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
        previous_query=previous_query,
        previous_error=previous_error,
    )
