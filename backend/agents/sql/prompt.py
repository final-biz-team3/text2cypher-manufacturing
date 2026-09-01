"""제조 데이터 질문을 PostgreSQL로 변환하기 위한 프롬프트를 구성한다."""

from collections.abc import Sequence
from typing import Any

from agents.prompt import build_prompt_messages

_SQL_INSTRUCTIONS = """당신은 제조 데이터용 PostgreSQL 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 SQL 문으로 변환합니다.

- 제공된 스키마의 테이블, 컬럼과 조인만 사용합니다.
- SELECT 문 또는 최종 문이 SELECT인 읽기 전용 WITH 문만 생성합니다.
- 확정된 entity가 있으면 해당 식별자로 조회하고 결과에 ID·이름을 포함합니다.
- 결과에는 질문에서 요청한 값을 포함합니다.
- 결과 alias는 한국어 표시명 대신 스키마 식별자 또는 의미가 분명한 영어
  lowerCamelCase를 사용합니다. 상위 N건은 사용자가 순위 번호를 요구한 경우에만
  별도 rank 컬럼을 만듭니다.
- ORDER BY에서 반환 alias를 재사용하면 "totalRejectedQty"처럼 SELECT와 같은
  대소문자를 유지해 double quote로 감쌉니다.
- derived table이나 CTE의 quoted lowerCamelCase 열도 i."actualStock"처럼 exact
  quote로 참조합니다. 가능하면 내부 alias는 actual_stock 같은 unquoted snake_case를
  쓰고 required alias는 최종 SELECT에서만 "actualStock"으로 지정합니다.
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
    '질문이 "부족한" 제품만 요구하면 집계 후 실제 재고가 safetystocklevel보다 '
    "작은 행만 반환한다.",
    "totalRejectedQty는 SUM(rejectedqty)로 계산한 반려 수량 합계이며 구매주문 "
    "건수가 아니다. required output에 totalRejectedQty가 있으면 공급업체별 "
    "SUM(rejectedqty)를 내림차순으로 정렬하고 supplier ID를 오름차순 tie-break로 "
    "사용한다. purchaseorderid 건수를 별도로 계산하거나 정렬 기준으로 쓰지 않는다.",
    "inputBindings가 있으면 그 ID 배열이 이 subquery의 전체 대상이다. 모든 고유 ID를 "
    "보존하고 선행 단계의 관계를 SQL에서 다시 탐색하거나 makeflag 등으로 행을 "
    "제거하지 않는다. binding 배열의 중복은 선행 결과의 행 multiplicity이지 SQL "
    "집계 대상을 반복하라는 뜻이 아니다. 단일 ID 배열을 필터로 사용할 때는 "
    "= ANY(...) 또는 SELECT DISTINCT로 ID를 집합화한 뒤 조인한다. entity별 한 행을 "
    "반환하는 집계에서 WITH ORDINALITY의 순번을 GROUP BY해 중복 ID 결과를 만들지 "
    "않는다.",
)


def build_sql_prompt(
    *,
    query: str,
    entity: object | None,
    schema_text: str,
    business_rules: Sequence[str] = (),
    required_outputs: Sequence[str] = (),
    input_bindings: dict[str, list[Any]] | None = None,
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
        required_outputs=required_outputs,
        input_bindings=input_bindings,
        previous_query=previous_query,
        previous_error=previous_error,
    )
