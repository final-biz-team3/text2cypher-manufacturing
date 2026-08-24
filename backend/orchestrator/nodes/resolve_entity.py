import json
import logging
import os
from collections.abc import Callable
from typing import Any

import psycopg

from agents.cypher.schema.models import GraphSchema
from orchestrator.entity_types import NamedEntityType, list_named_entity_types
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.3
_MAX_CANDIDATES = 5

_SYSTEM_PROMPT = (
    "사용자 질의에 특정 대상을 지칭하는 이름이 있으면 "
    "extract_entity를 호출한다. 없으면 아무 도구도 호출하지 않는다. "
    "이름에 쉼표로 이어지는 색상·크기 등 수식어가 있으면 잘라내지 않고 "
    "쉼표 이후 부분까지 이름 전체를 통째로 추출한다."
)


def _build_extract_entity_tool(entity_types: list[NamedEntityType]) -> dict:
    """엔티티 타입 목록으로 Function Calling 도구 정의를 만든다."""
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


def _extract_entity(
    query: str, openai_client: Any, extract_tool: dict
) -> tuple[str, str] | None:
    """LLM Function Calling으로 질의에서 엔티티 타입과 이름을 추출한다."""
    response = openai_client.chat.completions.create(
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
        return None
    arguments = json.loads(tool_calls[0].function.arguments)
    try:
        return arguments["entityType"], arguments["entityName"]
    except KeyError:
        logger.warning(
            "resolve_entity: extract_entity 인자 누락 arguments=%r", arguments
        )
        return None


def _entity_type_config(
    entity_type: str, entity_types: list[NamedEntityType]
) -> NamedEntityType:
    """엔티티 타입 이름으로 조회 설정을 찾는다."""
    for config in entity_types:
        if config.entity_type == entity_type:
            return config
    raise ValueError(f"Unknown entity type: {entity_type}")


def _find_entity_by_name(
    config: NamedEntityType,
    name: str,
    postgres_connection: Any,
) -> dict | None:
    """엔티티 타입별 테이블·컬럼으로 이름을 정확 일치 조회한다."""
    cursor = postgres_connection.execute(
        f"SELECT {config.id_column}, {config.name_column} "
        f"FROM {config.table} WHERE {config.name_column} = %s",
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {config.id_field: row[0], config.name_field: row[1]}


def _find_similar_entities(
    config: NamedEntityType,
    name: str,
    postgres_connection: Any,
) -> list[dict]:
    """엔티티 타입별 테이블·컬럼으로 유사한 이름을 유사도 순으로 조회한다.
    pg_trgm을 쓸 수 없으면 롤백 후 후보 없음으로 처리한다."""
    try:
        cursor = postgres_connection.execute(
            f"SELECT {config.id_column}, {config.name_column}, "
            f"similarity({config.name_column}, %s) AS score "
            f"FROM {config.table} "
            f"WHERE similarity({config.name_column}, %s) >= %s "
            f"ORDER BY score DESC LIMIT %s",
            (name, name, _SIMILARITY_THRESHOLD, _MAX_CANDIDATES),
        )
    except psycopg.errors.UndefinedFunction:
        postgres_connection.rollback()
        logger.warning(
            "resolve_entity: pg_trgm 유사도 검색을 사용할 수 없어 후보 없음으로 처리"
        )
        return []
    return [
        {
            "id": row[0],
            "name": row[1],
            "entityType": config.entity_type,
            "score": row[2],
            "entity": {config.id_field: row[0], config.name_field: row[1]},
        }
        for row in cursor.fetchall()
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


def _confirmed_entity_exists(
    confirmed_entity: dict, config: NamedEntityType, postgres_connection: Any
) -> bool:
    """confirmed_entity의 id·name이 실제 DB 행과 일치하는지 확인한다."""
    cursor = postgres_connection.execute(
        f"SELECT 1 FROM {config.table} "
        f"WHERE {config.id_column} = %s AND {config.name_column} = %s",
        (confirmed_entity[config.id_field], confirmed_entity[config.name_field]),
    )
    return cursor.fetchone() is not None


def make_resolve_entity_node(
    openai_client: Any, postgres_connection: Any, graph_schema: GraphSchema
) -> Callable[[OrchestratorState], dict]:
    """OpenAI/PostgreSQL 클라이언트와 그래프 스키마를 주입받은 resolve_entity 노드를 만든다."""
    entity_types = list_named_entity_types(graph_schema)
    if not entity_types:
        raise ValueError(
            "그래프 스키마에 이름으로 검색 가능한 엔티티 타입이 하나도 없습니다."
        )
    extract_tool = _build_extract_entity_tool(entity_types)

    def resolve_entity(state: OrchestratorState) -> dict:
        confirmed_entity = state.get("confirmed_entity")
        if confirmed_entity is not None:
            confirmed_config = _confirmed_entity_config(confirmed_entity, entity_types)
            if confirmed_config is not None and _confirmed_entity_exists(
                confirmed_entity, confirmed_config, postgres_connection
            ):
                logger.info(
                    "resolve_entity: query=%r -> confirmed_entity=%s (재진입)",
                    state["query"],
                    confirmed_entity,
                )
                return {"entity": confirmed_entity}
            logger.warning(
                "resolve_entity: query=%r -> confirmed_entity=%r 검증 실패 "
                "(무시하고 재추출)",
                state["query"],
                confirmed_entity,
            )

        extraction = _extract_entity(state["query"], openai_client, extract_tool)
        if extraction is None:
            logger.info(
                "resolve_entity: query=%r -> entity=None (대상 미언급)", state["query"]
            )
            return {"entity": None}

        entity_type, entity_name = extraction
        try:
            config = _entity_type_config(entity_type, entity_types)
        except ValueError:
            logger.warning(
                "resolve_entity: query=%r -> 알 수 없는 entityType=%r (entity=None 처리)",
                state["query"],
                entity_type,
            )
            return {"entity": None}

        entity = _find_entity_by_name(config, entity_name, postgres_connection)
        if entity is None:
            candidates = _find_similar_entities(
                config, entity_name, postgres_connection
            )
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

        logger.info("resolve_entity: query=%r -> entity=%s", state["query"], entity)
        return {"entity": entity}

    return resolve_entity
