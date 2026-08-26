"""제조 데이터 질문을 PostgreSQL로 변환하기 위한 프롬프트를 구성한다."""

from collections.abc import Sequence

from agents.prompt import build_prompt_messages

_SQL_INSTRUCTIONS = """당신은 제조 데이터용 PostgreSQL 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 SQL 문으로 변환합니다.

- 제공된 스키마의 테이블, 컬럼과 조인만 사용합니다.
- SELECT 문 또는 최종 문이 SELECT인 읽기 전용 WITH 문만 생성합니다.
- 확정된 entity가 있으면 해당 식별자로 조회하고 결과에 ID·이름을 포함합니다.
- 결과에는 질문에서 요청한 값을 포함합니다.
- 여러 행을 반환하면 관련 식별자를 기준으로 일관되게 정렬합니다.
- 제공된 업무 규칙이 있으면 쿼리에 반영합니다.
- 스키마에 없는 테이블이나 컬럼을 추측하지 않습니다.
- 설명, 주석 또는 Markdown 없이 SQL만 반환합니다."""

_SQL_DOMAIN_RULES = (
    '"외부 구매 부품"은 production.product.makeflag = false인 제품이다.',
    '"위치별 재고 수량"은 제품·위치 식별정보와 shelf·bin별 원본 quantity를 '
    "합산하지 않고 locationid, shelf, bin 순으로 반환한다.",
    '"실제 재고"는 제품을 기준으로 productinventory를 LEFT JOIN한 뒤 '
    "COALESCE(SUM(quantity), 0)으로 계산하고, "
    '"부족 수량"은 GREATEST(safetystocklevel - 실제 재고, 0)이다.',
)


def build_sql_prompt(
    *,
    query: str,
    entity: dict[str, object] | None,
    schema_text: str,
    business_rules: Sequence[str] = (),
    previous_query: str | None = None,
    previous_error: str | None = None,
) -> list[dict[str, str]]:
    """현재 질의 문맥을 포함한 PostgreSQL 생성 메시지를 반환한다."""
    return build_prompt_messages(
        instructions=_SQL_INSTRUCTIONS,
        query=query,
        entity=entity,
        schema_text=schema_text,
        business_rules=(*_SQL_DOMAIN_RULES, *business_rules),
        previous_query=previous_query,
        previous_error=previous_error,
    )
