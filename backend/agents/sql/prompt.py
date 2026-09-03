"""스키마와 의미 출처 정보를 사용해 PostgreSQL 생성 프롬프트를 구성한다."""

from collections.abc import Sequence
from typing import Any

from agents.prompt import build_prompt_messages

_SQL_INSTRUCTIONS = """당신은 제조 데이터용 PostgreSQL 쿼리 생성기입니다.
사용자 질문을 하나의 읽기 전용 SQL 문으로 변환합니다.

- 제공된 physical schema의 테이블, 컬럼과 조인만 사용합니다.
- SELECT 문 또는 최종 문이 SELECT인 읽기 전용 WITH 문만 생성합니다.
- semantic output catalog의 alias, operation, inputs, grain, predicate를 사용해
  요청된 업무 개념을 구현합니다.
- required output 목록의 모든 alias를 정확히 반환하고 추가 alias를 반환하지
  않습니다.
- 확정된 entity가 있으면 해당 식별자로 조회합니다.
- 원문의 filter, comparison, limit, date, quantity 조건을 의미 그대로 보존합니다.
- aggregate operation은 catalog의 grain에서 계산하며 countDistinct는 DISTINCT,
  clampedDifference는 음수가 되지 않는 차이를 뜻합니다.
- quoted lowerCamelCase alias를 CTE 밖에서 참조할 때 철자와 대소문자를 그대로
  유지합니다. 내부 계산 alias는 unquoted snake_case를 사용할 수 있습니다.
- 여러 행은 결과 grain의 identity와 요청된 정렬 의미를 사용해 결정적으로
  정렬합니다. 질문에 없는 특정 metric용 순위 recipe를 만들지 않습니다.
- input binding은 선행 결과 행을 같은 index 순서로 투영한 배열입니다. 하나의
  dependency에서 온 여러 배열은 UNNEST(..., ...) WITH ORDINALITY처럼 row alignment,
  중복과 NULL을 보존하는 방식으로 함께 사용합니다.
- 단일 ID binding을 집합 filter로만 쓸 때는 중복 ID가 aggregate를 배수화하지
  않도록 DISTINCT 또는 = ANY(...)를 사용합니다.
- 스키마에 없는 테이블이나 컬럼을 추측하지 않습니다.
- 설명, 주석 또는 Markdown 없이 SQL만 반환합니다."""


def build_sql_prompt(
    *,
    query: str,
    source_scope: str | None = None,
    entity: object | None,
    schema_text: str,
    semantic_context: str = "",
    business_rules: Sequence[str] = (),
    required_outputs: Sequence[str] = (),
    input_bindings: dict[str, list[Any]] | None = None,
    previous_query: str | None = None,
    previous_error: str | None = None,
) -> list[dict[str, str]]:
    """실행 subquery 하나에 대한 PostgreSQL 생성 메시지를 반환한다."""
    return build_prompt_messages(
        instructions=_SQL_INSTRUCTIONS,
        query=query,
        source_scope=source_scope,
        entity=entity,
        schema_text=schema_text,
        semantic_context=semantic_context,
        business_rules=business_rules,
        required_outputs=required_outputs,
        input_bindings=input_bindings,
        previous_query=previous_query,
        previous_error=previous_error,
    )
