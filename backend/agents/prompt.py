"""SQL과 Cypher Agent가 공유하는 프롬프트 메시지 구조를 만든다."""

import json
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def _serialize_context_value(value: Any) -> str:
    """DB 결과에 포함되는 JSON 비지원 scalar를 손실 없는 문자열로 바꾼다."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    # neo4j.time.Date/Time/DateTime/Duration은 표준 datetime 타입을 상속하지
    # 않지만 모두 iso_format()을 제공한다. shared prompt가 neo4j 모듈에 직접
    # 의존하지 않도록 해당 프로토콜만 사용한다.
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        formatted = iso_format()
        if isinstance(formatted, str):
            return formatted

    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def build_prompt_messages(
    *,
    instructions: str,
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
    """언어별 지침과 동적 질의 문맥을 system/user 메시지로 조립한다.
    previous_query·previous_error가 함께 있으면 self-correction 재시도용
    피드백 섹션을 system 메시지에 추가한다."""
    system_sections = [instructions.strip()]
    if source_scope:
        system_sections.append(
            "의미 결정 기준:\n"
            "- 요청한 사실, filter, result grain, 계산 및 출력 의미는 원 질문을 "
            "기준으로 한다.\n"
            "- source scope는 해당 source가 담당하는 실행 범위만 좁힌다. 원 질문에서 "
            "도출되지 않은 filter, output, grain, calculation 또는 relationship "
            "property를 추가해서는 안 된다.\n"
            "- required output alias와 business rule은 명시적인 실행 계약이며 source "
            "scope의 충돌하는 문구보다 우선한다."
        )
    system_sections.append(f"물리 schema:\n{schema_text.strip()}")

    if semantic_context.strip():
        system_sections.append("의미 출력 catalog:\n" + semantic_context.strip())

    if business_rules:
        formatted_rules = "\n".join(f"- {rule}" for rule in business_rules)
        system_sections.append(f"업무 규칙:\n{formatted_rules}")

    if required_outputs:
        formatted_outputs = "\n".join(f"- {field}" for field in required_outputs)
        system_sections.append(
            "필수 출력 alias:\n"
            f"{formatted_outputs}\n"
            "위 모든 필드를 정확한 alias로 반환한다."
        )

    if input_bindings:
        system_sections.append(
            "Input binding은 선행 query가 반환한 값이다. 각 배열의 모든 값을 정확한 "
            "filter로 사용한다. 같은 선행 결과에서 생성된 배열은 행 index로 정렬되어 "
            "있으며 중복을 포함할 수 있다."
        )

    if previous_query and previous_error is not None:
        system_sections.append(
            "이전 시도가 실패했다. 아래 문제를 수정하고 같은 문제를 피하는 보정된 "
            "query를 생성한다:\n"
            f"이전 query:\n{previous_query}\n"
            f"오류:\n{previous_error}"
        )

    user_context: dict[str, Any] = {"query": query, "entity": entity}
    if source_scope:
        user_context["sourceScope"] = source_scope
    if input_bindings:
        user_context["inputBindings"] = input_bindings
    user_content = json.dumps(
        user_context,
        ensure_ascii=False,
        default=_serialize_context_value,
    )

    return [
        {"role": "system", "content": "\n\n".join(system_sections)},
        {"role": "user", "content": user_content},
    ]
