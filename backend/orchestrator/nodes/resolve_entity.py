"""자연어 질의에서 제품 엔티티를 확정하는 resolve_entity 노드를 정의한다."""

import json
import os
from collections.abc import Callable
from typing import Any

from orchestrator.errors import EntityNotFoundError
from orchestrator.state import OrchestratorState

_EXTRACT_PRODUCT_NAME_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_product_name",
        "description": (
            "자연어 질의에서 특정 제품·부품을 지칭하는 이름을 추출한다. "
            "질의가 특정 제품/부품을 가리키지 않으면 호출하지 않는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "productName": {
                    "type": "string",
                    "description": "질의에 등장하는 제품명 문자열 그대로",
                }
            },
            "required": ["productName"],
        },
    },
}

_SYSTEM_PROMPT = (
    "사용자 질의에 특정 제품이나 부품을 지칭하는 이름이 있으면 "
    "extract_product_name을 호출한다. 없으면 아무 도구도 호출하지 않는다."
)


def _extract_product_name(query: str, openai_client: Any) -> str | None:
    response = openai_client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        tools=[_EXTRACT_PRODUCT_NAME_TOOL],
    )
    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        return None
    arguments = json.loads(tool_calls[0].function.arguments)
    return arguments["productName"]


def _find_product_by_name(name: str, postgres_connection: Any) -> dict | None:
    cursor = postgres_connection.execute(
        "SELECT productid, name FROM production.product WHERE name = %s",
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"productId": row[0], "productName": row[1]}


def make_resolve_entity_node(
    openai_client: Any, postgres_connection: Any
) -> Callable[[OrchestratorState], dict]:
    """OpenAI/PostgreSQL 클라이언트를 주입받은 resolve_entity 노드 함수를 만든다."""

    def resolve_entity(state: OrchestratorState) -> dict:
        product_name = _extract_product_name(state["query"], openai_client)
        if product_name is None:
            return {"entity": None}

        entity = _find_product_by_name(product_name, postgres_connection)
        if entity is None:
            raise EntityNotFoundError()

        return {"entity": entity}

    return resolve_entity
