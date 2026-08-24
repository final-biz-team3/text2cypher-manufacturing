"""질의 문맥으로부터 Neo4j Cypher 쿼리를 생성한다."""

from collections.abc import Sequence
from typing import Any

from agents.cypher.prompt import build_cypher_prompt
from agents.cypher.schema.models import GraphQueryPolicy
from agents.generator import generate_query


def generate_cypher(
    openai_client: Any,
    *,
    query: str,
    entity: dict[str, object] | None,
    schema_text: str,
    query_policy: GraphQueryPolicy,
    business_rules: Sequence[str] = (),
) -> str:
    """Cypher 프롬프트를 구성해 LLM이 생성한 Neo4j 문을 반환한다."""
    messages = build_cypher_prompt(
        query=query,
        entity=entity,
        schema_text=schema_text,
        query_policy=query_policy,
        business_rules=business_rules,
    )
    return generate_query(openai_client, messages)
