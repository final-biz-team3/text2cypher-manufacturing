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
    system_sections = [
        instructions.strip(),
        f"Schema:\n{schema_text.strip()}",
    ]

    if semantic_context.strip():
        system_sections.append("Semantic output catalog:\n" + semantic_context.strip())

    if business_rules:
        formatted_rules = "\n".join(f"- {rule}" for rule in business_rules)
        system_sections.append(f"Business rules:\n{formatted_rules}")

    if required_outputs:
        formatted_outputs = "\n".join(f"- {field}" for field in required_outputs)
        system_sections.append(
            "Required output aliases:\n"
            f"{formatted_outputs}\n"
            "Return every field above using the exact alias."
        )

    if input_bindings:
        system_sections.append(
            "Input bindings are values returned by prerequisite queries. "
            "Use every value in each array as an exact filter. Arrays produced "
            "from the same prerequisite result are aligned by row index and "
            "may contain duplicates."
        )

    if previous_query and previous_error is not None:
        system_sections.append(
            "Previous attempt failed. Fix the issue below and generate a "
            "corrected query that avoids the same problem:\n"
            f"Previous query:\n{previous_query}\n"
            f"Error:\n{previous_error}"
        )

    user_context: dict[str, Any] = {"query": query, "entity": entity}
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
