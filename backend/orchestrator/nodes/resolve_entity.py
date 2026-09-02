import asyncio
import json
import logging
import os
import re
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
    "'Fasteners 제품 몇 개'처럼 분류명이 제품 앞에 오면 productCategory로 추출한다. "
    "이름에 쉼표로 이어지는 색상·크기 등 수식어가 있으면 잘라내지 않고 "
    "쉼표 이후 부분까지 이름 전체를 통째로 추출한다. 질의에 서로 다른 고유 "
    "이름이 여러 개 있으면 이름마다 extract_entity를 한 번씩 호출하고 질문에 "
    "등장한 순서를 유지한다. 'Orion Frame과 Alloy Sheet 사이 경로'처럼 두 이름이 "
    "관계의 양 끝이면 product 호출을 두 번 만든다."
    "숫자 ID는 이름으로 추출하지 않는다."
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
            "strict": True,
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
                            "(예: 'Aurora Frame, 52')."
                        ),
                    },
                },
                "required": ["entityType", "entityName"],
                "additionalProperties": False,
            },
        },
    }


async def _extract_entities(
    query: str, openai_client: Any, extract_tool: dict
) -> tuple[list[tuple[str, str]], bool]:
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
        return [], False

    extractions: list[tuple[str, str]] = []
    for tool_call in tool_calls:
        if tool_call.function.name != "extract_entity":
            logger.warning(
                "resolve_entity: 알 수 없는 tool call %r 무시",
                tool_call.function.name,
            )
            continue
        try:
            arguments = json.loads(tool_call.function.arguments)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "resolve_entity: extract_entity 인자 JSON 오류 %r",
                tool_call.function.arguments,
            )
            continue
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
    return extractions, True


def _entity_type_config(
    entity_type: str, entity_types: list[NamedEntityType]
) -> NamedEntityType:
    """엔티티 타입 이름으로 조회 설정을 찾는다."""
    for config in entity_types:
        if config.entity_type == entity_type:
            return config
    raise ValueError(f"Unknown entity type: {entity_type}")


def _is_entity_type_alias(name: str, type_aliases: tuple[str, ...]) -> bool:
    """추출된 이름이 고유 이름이 아닌 엔티티 종류 표현인지 확인한다."""
    normalized_name = " ".join(name.casefold().split())
    return any(
        normalized_name == " ".join(alias.casefold().split()) for alias in type_aliases
    )


def _strip_edge_type_alias(name: str, config: NamedEntityType) -> tuple[str, bool]:
    normalized = name.strip()
    folded = normalized.casefold()
    for alias in sorted(config.aliases, key=len, reverse=True):
        alias_folded = alias.casefold()
        if folded.startswith(f"{alias_folded} "):
            return normalized[len(alias) :].strip(), True
        if folded.endswith(f" {alias_folded}"):
            return normalized[: -len(alias)].strip(), True
    return normalized, False


def _is_generic_descriptor(name: str) -> bool:
    folded = name.casefold()
    return any(
        marker in folded
        for marker in (
            "전체",
            "모든",
            "현재",
            "활성",
            "같은",
            "공통",
            "가장",
            "제일",
            "많이",
            "안 ",
            "끝난",
            "등록",
            "다섯",
            "열 ",
            " 개",
            " 건",
            "top ",
        )
    )


def _is_non_name_identifier(name: str, aliases: tuple[str, ...]) -> bool:
    normalized = " ".join(name.casefold().split())
    if re.fullmatch(r"#?\d+", normalized):
        return True
    return any(
        normalized.startswith(f"{alias} ") or normalized.endswith(f" {alias}")
        for alias in (" ".join(value.casefold().split()) for value in aliases)
    )


def _is_type_alias_fragment(name: str, config: NamedEntityType) -> bool:
    normalized = " ".join(name.casefold().split())
    if len(normalized) < 2:
        return False
    return any(
        alias.startswith(normalized) or normalized in alias.split()
        for alias in (" ".join(item.casefold().split()) for item in config.aliases)
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


async def _find_leading_category_modifier(
    query: str,
    entity_types: list[NamedEntityType],
    pool: AsyncConnectionPool,
) -> dict | None:
    config = next(
        (entity for entity in entity_types if entity.entity_type == "productCategory"),
        None,
    )
    if config is None:
        return None

    normalized = query.strip()
    if not any(
        marker in normalized.casefold()
        for marker in ("제품", "품목", "분류", "카테고리", "category")
    ):
        return None

    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT {config.id_column}, {config.name_column} "
            f"FROM {config.table} "
            f"WHERE lower(%s) LIKE lower({config.name_column}) || '%%' "
            f"ORDER BY char_length({config.name_column}) DESC LIMIT 1",
            (normalized,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None

    name = str(row[1])
    if not normalized.casefold().startswith(name.casefold()):
        return None
    suffix = normalized[len(name) :].lstrip().casefold()
    if not suffix.startswith(("제품", "품목", "분류", "카테고리", "category")):
        return None
    return {config.id_field: row[0], config.name_field: row[1]}


async def _find_leading_name(
    query: str,
    config: NamedEntityType,
    pool: AsyncConnectionPool,
) -> tuple[dict, str] | None:
    normalized = query.strip()
    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT {config.id_column}, {config.name_column} "
            f"FROM {config.table} "
            f"WHERE lower(%s) LIKE lower({config.name_column}) || '%%' "
            f"ORDER BY char_length({config.name_column}) DESC LIMIT 1",
            (normalized,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None

    name = str(row[1])
    if not normalized.casefold().startswith(name.casefold()):
        return None
    return ({config.id_field: row[0], config.name_field: row[1]}, name)


async def _find_leading_named_fact_entity(
    query: str,
    entity_types: list[NamedEntityType],
    pool: AsyncConnectionPool,
) -> dict | None:
    markers = {
        "product": (
            "재고",
            "부족",
            "원가",
            "가격",
            "정가",
            "색상",
            "부품",
            "경로",
            "생산",
            "stock",
            "inventory",
            "cost",
            "price",
            "shortage",
            "component",
            "path",
        ),
        "supplier": (
            "공급",
            "부품",
            "완제품",
            "경로",
            "영향",
            "재고",
            "supplier",
            "vendor",
        ),
    }
    folded = query.casefold()

    def has_name_prefix(config: NamedEntityType) -> bool:
        fact_markers = markers[config.entity_type]
        offsets = [folded.find(marker) for marker in fact_markers if marker in folded]
        if not offsets:
            return False
        prefix = folded[: min(offsets)].strip(" ,.:;!?()[]{}")
        if not prefix or prefix.startswith(
            ("그 ", "해당 ", "현재 ", "활성 ", "전체 ", "모든 ", "각 ")
        ):
            return False
        return not any(alias.casefold() in prefix for alias in config.aliases)

    configs = [
        config
        for config in entity_types
        if config.entity_type in markers and has_name_prefix(config)
    ]
    if not configs:
        return None
    matches = await asyncio.gather(
        *(_find_leading_name(query, config, pool) for config in configs)
    )
    found = [match for match in matches if match is not None]
    if not found:
        return None
    longest_length = max(len(match[1]) for match in found)
    longest = [match for match in found if len(match[1]) == longest_length]
    if len(longest) != 1:
        return None
    return longest[0][0]


async def _find_product_relation_endpoints(
    query: str,
    entity_types: list[NamedEntityType],
    pool: AsyncConnectionPool,
) -> list[dict]:
    folded = query.casefold()
    if not any(
        marker in folded
        for marker in (
            "사이",
            "공통",
            "연결",
            "모두 들어",
            "둘 다",
            "between",
            "both",
            "common",
        )
    ):
        return []
    config = next(
        (entity for entity in entity_types if entity.entity_type == "product"), None
    )
    if config is None:
        return []

    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT {config.id_column}, {config.name_column} "
            f"FROM {config.table} "
            f"WHERE strpos(lower(%s), lower({config.name_column})) > 0 "
            f"ORDER BY char_length({config.name_column}) DESC LIMIT 10",
            (query,),
        )
        rows = await cursor.fetchall()

    selected: list[tuple[int, int, dict]] = []
    for product_id, product_name in rows:
        name = str(product_name)
        start = folded.find(name.casefold())
        if start < 0:
            continue
        end = start + len(name)
        if any(
            start < other_end and other_start < end
            for other_start, other_end, _ in selected
        ):
            continue
        selected.append(
            (
                start,
                end,
                {config.id_field: product_id, config.name_field: product_name},
            )
        )
    if len(selected) < 2:
        return []
    return [entity for _, _, entity in sorted(selected)]


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


def _normalize_confirmed_entities(confirmed_entity: Any) -> list[dict]:
    """confirmed_entity(dict/list/None)를 항상 리스트 형태로 통일한다."""
    if confirmed_entity is None:
        return []
    if isinstance(confirmed_entity, dict):
        return [confirmed_entity]
    if isinstance(confirmed_entity, list):
        return [item for item in confirmed_entity if isinstance(item, dict)]
    return []


def _collapse_entities(entities: list[dict]) -> dict | list[dict] | None:
    """엔티티 리스트를 개수에 따라 entity 필드 shape(dict/list/None)으로 접는다."""
    if not entities:
        return None
    if len(entities) == 1:
        return entities[0]
    return entities


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
    type_aliases = tuple(
        dict.fromkeys(
            alias
            for aliases in (
                *(entity.aliases for entity in entity_types),
                *(node.aliases for node in graph_schema.nodes.values()),
            )
            for alias in aliases
        )
    )
    identifier_only_aliases = tuple(
        alias
        for node in graph_schema.nodes.values()
        if "name" not in node.properties
        for alias in node.aliases
    )
    extract_tool = _build_extract_entity_tool(entity_types)

    async def resolve_entity(state: OrchestratorState) -> dict:
        raw_confirmed_entities = _normalize_confirmed_entities(
            state.get("confirmed_entity")
        )
        valid_confirmed: list[dict] = []
        valid_confirmed_types: set[str] = set()
        for candidate_entity in raw_confirmed_entities:
            config = _confirmed_entity_config(candidate_entity, entity_types)
            if config is not None and await _confirmed_entity_exists(
                candidate_entity, config, pool
            ):
                logger.info(
                    "resolve_entity: query=%r -> confirmed_entity=%s 검증 완료 "
                    "(나머지 엔티티 재추출)",
                    state["query"],
                    candidate_entity,
                )
                valid_confirmed.append(candidate_entity)
                valid_confirmed_types.add(config.entity_type)
            else:
                logger.warning(
                    "resolve_entity: query=%r -> confirmed_entity=%r 검증 실패 "
                    "(무시하고 재추출)",
                    state["query"],
                    candidate_entity,
                )

        extractions, had_tool_calls = await _extract_entities(
            state["query"], openai_client, extract_tool
        )
        if not extractions:
            if not had_tool_calls:
                category, relation_products = await asyncio.gather(
                    _find_leading_category_modifier(state["query"], entity_types, pool),
                    _find_product_relation_endpoints(
                        state["query"], entity_types, pool
                    ),
                )
            else:
                category = None
                relation_products = await _find_product_relation_endpoints(
                    state["query"], entity_types, pool
                )
            if relation_products:
                return {"entity": _collapse_entities(relation_products)}
            fallback = category or await _find_leading_named_fact_entity(
                state["query"], entity_types, pool
            )
            if fallback is not None and fallback not in valid_confirmed:
                valid_confirmed.append(fallback)
            if valid_confirmed:
                return {"entity": _collapse_entities(valid_confirmed)}
            logger.info(
                "resolve_entity: query=%r -> entity=None (대상 미언급)", state["query"]
            )
            return {"entity": None}

        lookups: list[tuple[str, str, NamedEntityType, bool, bool]] = []
        for entity_type, entity_name in extractions:
            if _is_non_name_identifier(entity_name, identifier_only_aliases):
                continue
            if _is_entity_type_alias(entity_name, type_aliases):
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

            if entity_type == "productCategory" and not any(
                marker in state["query"].casefold()
                for marker in (
                    "제품",
                    "품목",
                    "분류",
                    "카테고리",
                    "product",
                    "category",
                )
            ):
                continue

            lookup_name, had_type_label = _strip_edge_type_alias(entity_name, config)
            if _is_entity_type_alias(lookup_name, type_aliases):
                continue
            lookups.append(
                (
                    entity_type,
                    lookup_name,
                    config,
                    had_type_label and _is_generic_descriptor(lookup_name),
                    _is_type_alias_fragment(lookup_name, config),
                )
            )

        if not lookups:
            fallback = await _find_leading_named_fact_entity(
                state["query"], entity_types, pool
            )
            if fallback is not None and fallback not in valid_confirmed:
                valid_confirmed.append(fallback)
            return {"entity": _collapse_entities(valid_confirmed)}

        found_entities = await asyncio.gather(
            *(
                _find_entity_by_name(config, name, pool)
                for _, name, config, _, _ in lookups
            )
        )
        relation_products = await _find_product_relation_endpoints(
            state["query"], entity_types, pool
        )
        missing_indices = [
            index for index, entity in enumerate(found_entities) if entity is None
        ]
        candidates_by_index: dict[int, list[dict]] = {}
        leading_fallback: dict | None = None
        if missing_indices:
            candidates_task = asyncio.gather(
                *(
                    _find_similar_entities(lookups[index][2], lookups[index][1], pool)
                    for index in missing_indices
                )
            )
            if len(lookups) == 1:
                candidates_list, leading_fallback = await asyncio.gather(
                    candidates_task,
                    _find_leading_named_fact_entity(state["query"], entity_types, pool),
                )
            else:
                candidates_list = await candidates_task
            candidates_by_index = dict(
                zip(missing_indices, candidates_list, strict=True)
            )

        resolved: list[dict] = []
        explicit_lookup_count = sum(
            not status_descriptor for _, _, _, status_descriptor, _ in lookups
        )
        for index, (
            entity_type,
            entity_name,
            _config,
            status_descriptor,
            type_alias_fragment,
        ) in enumerate(lookups):
            entity = found_entities[index]
            if entity is None:
                if leading_fallback is not None:
                    if leading_fallback not in resolved:
                        resolved.append(leading_fallback)
                    continue
                if status_descriptor:
                    continue
                candidates = candidates_by_index[index]
                confirmed_candidate = next(
                    (
                        candidate["entity"]
                        for candidate in candidates
                        if candidate["entity"] in valid_confirmed
                    ),
                    None,
                )
                if confirmed_candidate is not None:
                    if confirmed_candidate not in resolved:
                        resolved.append(confirmed_candidate)
                    continue
                if type_alias_fragment and entity_type in valid_confirmed_types:
                    continue
                has_other_concrete_entity = bool(
                    any(
                        confirmed_type != entity_type
                        for confirmed_type in valid_confirmed_types
                    )
                    or relation_products
                    or explicit_lookup_count > 1
                )
                if type_alias_fragment and not has_other_concrete_entity:
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

        if relation_products:
            resolved = relation_products + [
                entity for entity in resolved if "productId" not in entity
            ]

        result: dict | list[dict] | None
        if not resolved:
            result = _collapse_entities(valid_confirmed)
        elif len(resolved) == 1:
            result = resolved[0]
        else:
            result = resolved
        logger.info("resolve_entity: query=%r -> entity=%s", state["query"], result)
        return {"entity": result}

    return resolve_entity
