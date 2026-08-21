"""제조 데이터 질문을 PostgreSQL로 변환하기 위한 프롬프트를 구성한다."""

from collections.abc import Sequence

from agents.prompt import build_prompt_messages

_SQL_INSTRUCTIONS = """당신은 제조 데이터용 PostgreSQL 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 SQL 문으로 변환합니다.

- 제공된 스키마의 테이블, 컬럼과 조인만 사용합니다.
- SELECT 문 또는 최종 문이 SELECT인 읽기 전용 WITH 문만 생성합니다.
- 확정된 entity가 있으면 해당 식별자를 우선 사용합니다.
- 제공된 업무 규칙이 있으면 쿼리에 반영합니다.
- 스키마에 없는 테이블이나 컬럼을 추측하지 않습니다.
- 설명, 주석 또는 Markdown 없이 SQL만 반환합니다."""


def build_sql_prompt(
    *,
    query: str,
    entity: dict[str, object] | None,
    schema_text: str,
    business_rules: Sequence[str] = (),
) -> list[dict[str, str]]:
    """현재 질의 문맥을 포함한 PostgreSQL 생성 메시지를 반환한다."""
    return build_prompt_messages(
        instructions=_SQL_INSTRUCTIONS,
        query=query,
        entity=entity,
        schema_text=schema_text,
        business_rules=business_rules,
    )
