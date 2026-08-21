"""SQL과 Cypher Agent가 공유하는 프롬프트 메시지 구조를 만든다."""

import json
from collections.abc import Sequence


def build_prompt_messages(
    *,
    instructions: str,
    query: str,
    entity: dict[str, object] | None,
    schema_text: str,
    business_rules: Sequence[str] = (),
) -> list[dict[str, str]]:
    """언어별 지침과 동적 질의 문맥을 system/user 메시지로 조립한다."""
    system_sections = [
        instructions.strip(),
        f"Schema:\n{schema_text.strip()}",
    ]

    if business_rules:
        formatted_rules = "\n".join(f"- {rule}" for rule in business_rules)
        system_sections.append(f"Business rules:\n{formatted_rules}")

    user_content = json.dumps(
        {"query": query, "entity": entity},
        ensure_ascii=False,
    )

    return [
        {"role": "system", "content": "\n\n".join(system_sections)},
        {"role": "user", "content": user_content},
    ]
