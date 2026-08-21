"""제조 데이터 질문을 Neo4j Cypher로 변환하기 위한 프롬프트를 구성한다."""

from collections.abc import Sequence

from agents.prompt import build_prompt_messages

_CYPHER_INSTRUCTIONS = """당신은 제조 데이터용 Neo4j Cypher 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 Cypher 문으로 변환합니다.

- 제공된 스키마의 노드, 관계와 속성만 사용합니다.
- 관계 방향을 제공된 스키마와 동일하게 사용합니다.
- 확정된 entity가 있으면 해당 식별자를 우선 사용합니다.
- 제공된 업무 규칙이 있으면 쿼리에 반영합니다.
- CREATE, MERGE, SET, DELETE, REMOVE 같은 쓰기 절을 사용하지 않습니다.
- 스키마에 없는 노드, 관계 또는 속성을 추측하지 않습니다.
- 설명, 주석 또는 Markdown 없이 Cypher만 반환합니다."""


def build_cypher_prompt(
    *,
    query: str,
    entity: dict[str, object] | None,
    schema_text: str,
    business_rules: Sequence[str] = (),
) -> list[dict[str, str]]:
    """현재 질의 문맥을 포함한 Neo4j Cypher 생성 메시지를 반환한다."""
    return build_prompt_messages(
        instructions=_CYPHER_INSTRUCTIONS,
        query=query,
        entity=entity,
        schema_text=schema_text,
        business_rules=business_rules,
    )
