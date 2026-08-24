"""질의 문맥으로부터 PostgreSQL 쿼리를 생성한다."""

from collections.abc import Sequence
from typing import Any

from agents.generator import generate_query
from agents.sql.prompt import build_sql_prompt


def generate_sql(
    openai_client: Any,
    *,
    query: str,
    entity: dict[str, object] | None,
    schema_text: str,
    business_rules: Sequence[str] = (),
) -> str:
    """SQL 프롬프트를 구성해 LLM이 생성한 PostgreSQL 문을 반환한다."""
    messages = build_sql_prompt(
        query=query,
        entity=entity,
        schema_text=schema_text,
        business_rules=business_rules,
    )
    return generate_query(openai_client, messages)
