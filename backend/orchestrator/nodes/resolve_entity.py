import json
import logging
import os
from collections.abc import Callable
from typing import Any

from orchestrator.errors import EntityNotFoundError
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

# LLM이 제품명 추출 시 호출할 함수 정의 (OpenAI Function Calling)
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


# LLM Function Calling으로 질의에서 제품명을 추출
# 도구 호출이 없으면(대상 제품이 없는 질의면) None 반환
def _extract_product_name(query: str, openai_client: Any) -> str | None:
    response = openai_client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        tools=[_EXTRACT_PRODUCT_NAME_TOOL],
        reasoning_effort="none",
    )
    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        return None
    arguments = json.loads(tool_calls[0].function.arguments)
    return arguments["productName"]


# 제품명으로 PostgreSQL production.product를 정확 일치 조회
# 찾지 못하면 None 반환 (호출부에서 EntityNotFoundError로 변환)
def _find_product_by_name(name: str, postgres_connection: Any) -> dict | None:
    cursor = postgres_connection.execute(
        "SELECT productid, name FROM production.product WHERE name = %s",
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"productId": row[0], "productName": row[1]}


# OpenAI/PostgreSQL 클라이언트를 주입받은 resolve_entity 노드 함수를 생성
def make_resolve_entity_node(
    openai_client: Any, postgres_connection: Any
) -> Callable[[OrchestratorState], dict]:
    def resolve_entity(state: OrchestratorState) -> dict:
        # 질의에서 제품명 추출 시도 (없으면 엔티티 확정 없이 통과)
        product_name = _extract_product_name(state["query"], openai_client)
        if product_name is None:
            logger.info(
                "resolve_entity: query=%r -> entity=None (제품 미언급)", state["query"]
            )
            return {"entity": None}

        # 추출된 제품명을 DB에서 조회, 없으면 예외
        entity = _find_product_by_name(product_name, postgres_connection)
        if entity is None:
            logger.info(
                "resolve_entity: query=%r -> productName=%r 조회 실패 (EntityNotFoundError)",
                state["query"],
                product_name,
            )
            raise EntityNotFoundError()

        logger.info("resolve_entity: query=%r -> entity=%s", state["query"], entity)
        return {"entity": entity}

    return resolve_entity
