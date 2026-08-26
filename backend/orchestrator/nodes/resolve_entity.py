import json
import logging
import os
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from agents.cypher.schema.models import GraphSchema
from orchestrator.entity_types import NamedEntityType, list_resolvable_entity_types
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.3
_MAX_CANDIDATES = 5

_SYSTEM_PROMPT = (
    "사용자에게 답변하거나 추가 자료를 요청하지 않는다. 도구에 정의된 엔티티 "
    "종류 중 질의에 고유 이름이 명시된 경우에만 extract_entity를 호출한다. "
    "종류나 조건을 나타내는 일반 표현만 있으면 호출하지 않는다. 고유 이름은 "
    "엔티티 종류 표현 없이 나타날 수 있으며, 그 이름이 조회·집계 범위를 "
    "한정하면 extract_entity를 호출한다. 예를 들어 'A에 포함된 대상 수'처럼 "
    "이름 A가 범위를 한정하면 A를 추출한다. "
    "이름에 쉼표로 이어지는 색상·크기 등 수식어가 있으면 잘라내지 않고 "
    "쉼표 이후 부분까지 이름 전체를 통째로 추출한다. 질의에 서로 다른 고유 "
    "이름이 여러 개 있으면 이름마다 extract_entity를 한 번씩 호출하고 질문에 "
    "등장한 순서를 유지한다."
)


def _build_extract_entity_tool(entity_types: list[NamedEntityType]) -> dict:
    """엔티티 타입 목록으로 Function Calling 도구 정의를 만든다."""
    type_descriptions = "; ".join(
        f"{entity.entity_type}: {', '.join(entity.aliases)}"
        for entity in entity_types
        if entity.aliases
    )
    return {
        "type": "function",
        "function": {
            "name": "extract_entity",
            "description": (
                "자연어 질의에서 특정 대상을 지칭하는 이름과 그 종류를 추출한다. "
                "질의가 특정 대상을 가리키지 않으면 호출하지 않는다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entityType": {
                        "type": "string",
                        "enum": [entity.entity_type for entity in entity_types],
                        "description": type_descriptions,
                    },
                    "entityName": {
                        "type": "string",
                        "description": (
                            "질의에 등장하는 이름 문자열 그대로. 쉼표로 이어지는 "
                            "색상·크기 등 수식어가 있으면 잘라내지 말고 쉼표 이후 "
                            "부분까지 포함해 통째로 추출한다 "
                            "(예: 'Touring-1000 Yellow, 54')."
                        ),
                    },
                },
                "required": ["entityType", "entityName"],
            },
        },
    }


async def _extract_entities(
    query: str, openai_client: Any, extract_tool: dict
) -> list[tuple[str, str]]:
    """LLM Function Calling으로 질의의 모든 이름 엔티티를 추출한다."""
    response = await openai_client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        tools=[extract_tool],
        reasoning_effort="none",
    )
    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        return []

    extractions: list[tuple[str, str]] = []
    for tool_call in tool_calls:
        if tool_call.function.name != "extract_entity":
            logger.warning(
                "resolve_entity: 알 수 없는 tool call %r 무시",
                tool_call.function.name,
            )
            continue
        arguments = json.loads(tool_call.function.arguments)
        if not isinstance(arguments, dict):
            logger.warning(
                "resolve_entity: extract_entity 인자 형식 오류 %r", arguments
            )
            continue

        entity_type = arguments.get("entityType")
        entity_name = arguments.get("entityName")
        if not isinstance(entity_type, str) or not isinstance(entity_name, str):
            logger.warning(
                "resolve_entity: extract_entity 인자 형식 오류 %r", arguments
            )
            continue
        extraction = (entity_type, entity_name)
        if extraction not in extractions:
            extractions.append(extraction)
    return extractions


def _entity_type_config(
    entity_type: str, entity_types: list[NamedEntityType]
) -> NamedEntityType:
    """엔티티 타입 이름으로 조회 설정을 찾는다."""
    for config in entity_types:
        if config.entity_type == entity_type:
            return config
    raise ValueError(f"Unknown entity type: {entity_type}")


def _is_entity_type_alias(name: str, entity_types: list[NamedEntityType]) -> bool:
    """추출된 이름이 고유 이름이 아닌 엔티티 종류 표현인지 확인한다."""
    normalized_name = " ".join(name.casefold().split())
    return any(
        normalized_name == " ".join(alias.casefold().split())
        for entity in entity_types
        for alias in entity.aliases
    )


async def _find_entity_by_name(
    config: NamedEntityType,
    name: str,
    pool: AsyncConnectionPool,
) -> dict | None:
    """엔티티 타입별 테이블·컬럼으로 이름을 정확 일치 조회한다.
    이 함수(와 아래 조회 함수들)는 여기서 명시적으로 commit/rollback을
    호출하지 않는다 - pool.connection()이 `async with conn:`으로 커넥션을
    감싸 블록을 정상 종료할 때 자동으로 commit한다(psycopg 표준 동작).
    SELECT뿐이라 commit이든 rollback이든 결과에 차이가 없다."""
    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT {config.id_column}, {config.name_column} "
            f"FROM {config.table} WHERE {config.name_column} = %s",
            (name,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {config.id_field: row[0], config.name_field: row[1]}


async def _find_similar_entities(
    config: NamedEntityType,
    name: str,
    pool: AsyncConnectionPool,
) -> list[dict]:
    """엔티티 타입별 테이블·컬럼으로 유사한 이름을 유사도 순으로 조회한다.
    pg_trgm을 쓸 수 없으면 롤백 후 후보 없음으로 처리한다."""
    async with pool.connection() as conn:
        try:
            cursor = await conn.execute(
                f"SELECT {config.id_column}, {config.name_column}, "
                f"similarity({config.name_column}, %s) AS score "
                f"FROM {config.table} "
                f"WHERE similarity({config.name_column}, %s) >= %s "
                f"ORDER BY score DESC LIMIT %s",
                (name, name, _SIMILARITY_THRESHOLD, _MAX_CANDIDATES),
            )
        except psycopg.errors.UndefinedFunction:
            await conn.rollback()
            logger.warning(
                "resolve_entity: pg_trgm 유사도 검색을 사용할 수 없어 후보 없음으로 처리"
            )
            return []
        rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "entityType": config.entity_type,
            "score": row[2],
            "entity": {config.id_field: row[0], config.name_field: row[1]},
        }
        for row in rows
    ]


def _confirmed_entity_config(
    confirmed_entity: Any, entity_types: list[NamedEntityType]
) -> NamedEntityType | None:
    """confirmed_entity의 shape과 일치하는 엔티티 타입 설정을 찾는다."""
    if not isinstance(confirmed_entity, dict):
        return None

    for config in entity_types:
        if set(confirmed_entity) != {config.id_field, config.name_field}:
            continue
        id_value = confirmed_entity[config.id_field]
        name_value = confirmed_entity[config.name_field]
        if (
            isinstance(id_value, int)
            and not isinstance(id_value, bool)
            and isinstance(name_value, str)
        ):
            return config

    return None


async def _confirmed_entity_exists(
    confirmed_entity: dict, config: NamedEntityType, pool: AsyncConnectionPool
) -> bool:
    """confirmed_entity의 id·name이 실제 DB 행과 일치하는지 확인한다."""
    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT 1 FROM {config.table} "
            f"WHERE {config.id_column} = %s AND {config.name_column} = %s",
            (confirmed_entity[config.id_field], confirmed_entity[config.name_field]),
        )
        row = await cursor.fetchone()
    return row is not None


def make_resolve_entity_node(
    openai_client: Any, pool: Any, graph_schema: GraphSchema
) -> Callable[[OrchestratorState], Any]:
    """OpenAI 클라이언트/PostgreSQL 풀/그래프 스키마를 주입받은 resolve_entity 노드를 만든다."""
    entity_types = list_resolvable_entity_types(graph_schema)
    extract_tool = _build_extract_entity_tool(entity_types)

    async def resolve_entity(state: OrchestratorState) -> dict:
        confirmed_entity = state.get("confirmed_entity")
        confirmed_config: NamedEntityType | None = None
        if confirmed_entity is not None:
            confirmed_config = _confirmed_entity_config(confirmed_entity, entity_types)
            if confirmed_config is not None and await _confirmed_entity_exists(
                confirmed_entity, confirmed_config, pool
            ):
                logger.info(
                    "resolve_entity: query=%r -> confirmed_entity=%s 검증 완료 "
                    "(나머지 엔티티 재추출)",
                    state["query"],
                    confirmed_entity,
                )
            else:
                logger.warning(
                    "resolve_entity: query=%r -> confirmed_entity=%r 검증 실패 "
                    "(무시하고 재추출)",
                    state["query"],
                    confirmed_entity,
                )
                confirmed_entity = None
                confirmed_config = None

        extractions = await _extract_entities(state["query"], openai_client, extract_tool)
        if not extractions:
            if confirmed_entity is not None:
                return {"entity": confirmed_entity}
            logger.info(
                "resolve_entity: query=%r -> entity=None (대상 미언급)", state["query"]
            )
            return {"entity": None}

        resolved: list[dict] = []
        for entity_type, entity_name in extractions:
            if _is_entity_type_alias(entity_name, entity_types):
                logger.info(
                    "resolve_entity: query=%r -> entityName=%r 종류 표현이므로 무시",
                    state["query"],
                    entity_name,
                )
                continue

            try:
                config = _entity_type_config(entity_type, entity_types)
            except ValueError:
                logger.warning(
                    "resolve_entity: query=%r -> 알 수 없는 entityType=%r (무시)",
                    state["query"],
                    entity_type,
                )
                continue

            entity = await _find_entity_by_name(config, entity_name, pool)
            if entity is None:
                candidates = await _find_similar_entities(config, entity_name, pool)
                confirmed_candidate = next(
                    (
                        candidate["entity"]
                        for candidate in candidates
                        if confirmed_config == config
                        and candidate["entity"] == confirmed_entity
                    ),
                    None,
                )
                if confirmed_candidate is not None:
                    if confirmed_candidate not in resolved:
                        resolved.append(confirmed_candidate)
                    continue
                if candidates:
                    logger.info(
                        "resolve_entity: query=%r -> entityName=%r 후보 %d개 "
                        "(EntityAmbiguousError)",
                        state["query"],
                        entity_name,
                        len(candidates),
                    )
                    raise EntityAmbiguousError(candidates)

                logger.info(
                    "resolve_entity: query=%r -> entityType=%r entityName=%r 조회 실패 "
                    "(EntityNotFoundError)",
                    state["query"],
                    entity_type,
                    entity_name,
                )
                raise EntityNotFoundError()
            if entity not in resolved:
                resolved.append(entity)

        result: dict | list[dict] | None
        if not resolved:
            result = confirmed_entity
        elif len(resolved) == 1:
            result = resolved[0]
        else:
            result = resolved
        logger.info("resolve_entity: query=%r -> entity=%s", state["query"], result)
        return {"entity": result}

    return resolve_entity
