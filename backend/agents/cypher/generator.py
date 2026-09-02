"""질의 문맥으로부터 Neo4j Cypher 쿼리를 생성한다."""

from collections.abc import Sequence
from typing import Any

from agents.cypher.prompt import build_cypher_prompt
from agents.cypher.schema.models import GraphQueryPolicy
from agents.generator import (
    DEFAULT_REASONING_EFFORT,
    ReasoningEffort,
    generate_query,
)


async def generate_cypher(
    openai_client: Any,
    *,
    query: str,
    source_scope: str | None = None,
    entity: object | None,
    schema_text: str,
    query_policy: GraphQueryPolicy,
    semantic_context: str = "",
    business_rules: Sequence[str] = (),
    required_outputs: Sequence[str] = (),
    input_bindings: dict[str, list[Any]] | None = None,
    previous_query: str | None = None,
    previous_error: str | None = None,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
) -> str:
    """Cypher 프롬프트를 구성해 LLM이 생성한 Neo4j 문을 반환한다.
    previous_query·previous_error는 이전 시도가 실패했을 때 self-correction
    재시도용 피드백으로 전달한다."""
    messages = build_cypher_prompt(
        query=query,
        source_scope=source_scope,
        entity=entity,
        schema_text=schema_text,
        query_policy=query_policy,
        semantic_context=semantic_context,
        business_rules=business_rules,
        required_outputs=required_outputs,
        input_bindings=input_bindings,
        previous_query=previous_query,
        previous_error=previous_error,
    )
    return await generate_query(
        openai_client, messages, reasoning_effort=reasoning_effort
    )
