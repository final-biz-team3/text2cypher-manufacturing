"""Resolve explicitly extracted names through exact and similar database lookup."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from agents.cypher.schema.models import GraphSchema
from orchestrator.entity_types import NamedEntityType, list_resolvable_entity_types
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.3
_DEFAULT_CANDIDATE_LIMIT = 5

_SYSTEM_PROMPT = (
    "사용자에게 답변하거나 추가 자료를 요청하지 않는다. 도구에 정의된 entity "
    "종류 중 질문에 고유 이름이 명시된 경우에만 extract_entity를 호출한다. "
    "종류, 상태, 수량, 범위를 나타내는 일반 표현은 고유 이름이 아니므로 호출하지 "
    "않는다. 이름이 entity 종류 표현 없이 나타나도 조회 범위를 한정하면 추출한다. "
    "질문에 서로 다른 고유 이름이 여러 개 있으면 각 이름마다 한 번 호출하고 질문에 "
    "등장한 순서를 유지한다. 쉼표를 포함한 색상·크기·모델 표기는 이름의 일부로 "
    "그대로 유지한다. 숫자 ID만 있는 표현은 이름으로 추출하지 않는다."
)


class EntityExtractionError(ValueError):
    """The model invoked the extraction boundary with an invalid call or shape."""


@dataclass(frozen=True)
class EntityResolutionSettings:
    similarity_threshold: float
    candidate_limit: int


def load_entity_resolution_settings() -> EntityResolutionSettings:
    """Load and range-check operational lookup settings from the environment."""
    raw_threshold = os.getenv(
        "ENTITY_SIMILARITY_THRESHOLD", str(_DEFAULT_SIMILARITY_THRESHOLD)
    )
    raw_limit = os.getenv("ENTITY_CANDIDATE_LIMIT", str(_DEFAULT_CANDIDATE_LIMIT))
    try:
        threshold = float(raw_threshold)
    except ValueError as exc:
        raise ValueError("ENTITY_SIMILARITY_THRESHOLD must be a number") from exc
    try:
        candidate_limit = int(raw_limit)
    except ValueError as exc:
        raise ValueError("ENTITY_CANDIDATE_LIMIT must be an integer") from exc
    if not 0 <= threshold <= 1:
        raise ValueError("ENTITY_SIMILARITY_THRESHOLD must be between 0 and 1")
    if not 1 <= candidate_limit <= 100:
        raise ValueError("ENTITY_CANDIDATE_LIMIT must be between 1 and 100")
    return EntityResolutionSettings(threshold, candidate_limit)


def _build_extract_entity_tool(entity_types: list[NamedEntityType]) -> dict[str, Any]:
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
                "질문에서 특정 대상을 지칭하는 고유 이름과 종류를 추출한다. "
                "특정 이름이 없으면 호출하지 않는다."
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
                        "minLength": 1,
                        "description": "질문에 등장한 고유 이름 문자열 그대로",
                    },
                },
                "required": ["entityType", "entityName"],
                "additionalProperties": False,
            },
        },
    }


async def _extract_entities(
    query: str,
    openai_client: Any,
    extract_tool: dict[str, Any],
    allowed_types: frozenset[str],
) -> list[tuple[str, str]]:
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
            raise EntityExtractionError(
                f"unsupported entity extraction tool: {tool_call.function.name!r}"
            )
        try:
            arguments = json.loads(tool_call.function.arguments)
        except (json.JSONDecodeError, TypeError) as exc:
            raise EntityExtractionError(
                "extract_entity arguments must be valid JSON"
            ) from exc
        if not isinstance(arguments, dict) or set(arguments) != {
            "entityType",
            "entityName",
        }:
            raise EntityExtractionError(
                "extract_entity arguments have an invalid shape"
            )
        entity_type = arguments["entityType"]
        entity_name = arguments["entityName"]
        if (
            not isinstance(entity_type, str)
            or entity_type not in allowed_types
            or not isinstance(entity_name, str)
            or not entity_name.strip()
        ):
            raise EntityExtractionError("extract_entity arguments have invalid values")
        extraction = (entity_type, entity_name.strip())
        if extraction not in extractions:
            extractions.append(extraction)
    return extractions


def _entity_type_config(
    entity_type: str, entity_types: list[NamedEntityType]
) -> NamedEntityType:
    for config in entity_types:
        if config.entity_type == entity_type:
            return config
    raise EntityExtractionError(f"unknown entity type: {entity_type!r}")


def _normalized_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _is_exact_type_alias(name: str, type_aliases: frozenset[str]) -> bool:
    return _normalized_label(name) in type_aliases


def _strip_edge_type_alias(name: str, config: NamedEntityType) -> str:
    """Strip only a whitespace-delimited exact type prefix or suffix."""
    normalized = name.strip()
    folded = normalized.casefold()
    for alias in sorted((*config.aliases, config.entity_type), key=len, reverse=True):
        alias_folded = alias.casefold()
        if folded.startswith(f"{alias_folded} "):
            return normalized[len(alias) :].strip()
        if folded.endswith(f" {alias_folded}"):
            return normalized[: -len(alias)].strip()
    return normalized


async def _find_entity_by_name(
    config: NamedEntityType,
    name: str,
    pool: AsyncConnectionPool,
) -> dict[str, Any] | None:
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
    settings: EntityResolutionSettings,
) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        try:
            cursor = await conn.execute(
                f"SELECT {config.id_column}, {config.name_column}, "
                f"similarity({config.name_column}, %s) AS score "
                f"FROM {config.table} "
                f"WHERE similarity({config.name_column}, %s) >= %s "
                f"ORDER BY score DESC LIMIT %s",
                (
                    name,
                    name,
                    settings.similarity_threshold,
                    settings.candidate_limit,
                ),
            )
        except psycopg.errors.UndefinedFunction:
            await conn.rollback()
            logger.warning("resolve_entity: pg_trgm unavailable; no similar candidates")
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
    if not isinstance(confirmed_entity, dict):
        return None
    for config in entity_types:
        if set(confirmed_entity) != {config.id_field, config.name_field}:
            continue
        identifier = confirmed_entity[config.id_field]
        name = confirmed_entity[config.name_field]
        if (
            isinstance(identifier, int)
            and not isinstance(identifier, bool)
            and isinstance(name, str)
            and bool(name)
        ):
            return config
    return None


def _normalize_confirmed_entities(confirmed_entity: Any) -> list[dict[str, Any]]:
    if confirmed_entity is None:
        return []
    if isinstance(confirmed_entity, dict):
        return [confirmed_entity]
    if isinstance(confirmed_entity, list):
        return [item for item in confirmed_entity if isinstance(item, dict)]
    return []


def _collapse_entities(
    entities: list[dict[str, Any]],
) -> dict[str, Any] | list[dict[str, Any]] | None:
    if not entities:
        return None
    if len(entities) == 1:
        return entities[0]
    return entities


async def _confirmed_entity_exists(
    confirmed_entity: dict[str, Any],
    config: NamedEntityType,
    pool: AsyncConnectionPool,
) -> bool:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT 1 FROM {config.table} "
            f"WHERE {config.id_column} = %s AND {config.name_column} = %s",
            (confirmed_entity[config.id_field], confirmed_entity[config.name_field]),
        )
        row = await cursor.fetchone()
    return row is not None


def _append_unique(target: list[dict[str, Any]], values: list[dict[str, Any]]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def make_resolve_entity_node(
    openai_client: Any,
    pool: Any,
    graph_schema: GraphSchema,
    *,
    settings: EntityResolutionSettings | None = None,
) -> Callable[[OrchestratorState], Any]:
    """Create a resolver whose only semantic input is explicit extraction output."""
    resolution_settings = settings or load_entity_resolution_settings()
    entity_types = list_resolvable_entity_types(graph_schema)
    allowed_types = frozenset(config.entity_type for config in entity_types)
    type_aliases = frozenset(
        _normalized_label(alias)
        for aliases in (
            *((entity.entity_type, *entity.aliases) for entity in entity_types),
            *(node.aliases for node in graph_schema.nodes.values()),
        )
        for alias in aliases
    )
    extract_tool = _build_extract_entity_tool(entity_types)

    async def resolve_entity(state: OrchestratorState) -> dict[str, Any]:
        valid_confirmed: list[dict[str, Any]] = []
        for candidate in _normalize_confirmed_entities(state.get("confirmed_entity")):
            config = _confirmed_entity_config(candidate, entity_types)
            if config is not None and await _confirmed_entity_exists(
                candidate, config, pool
            ):
                _append_unique(valid_confirmed, [candidate])
            else:
                logger.warning(
                    "resolve_entity: invalid confirmed entity ignored: %r", candidate
                )

        extractions = await _extract_entities(
            state["query"], openai_client, extract_tool, allowed_types
        )
        lookups: list[tuple[str, str, NamedEntityType]] = []
        for entity_type, extracted_name in extractions:
            config = _entity_type_config(entity_type, entity_types)
            if _is_exact_type_alias(extracted_name, type_aliases):
                continue
            lookup_name = _strip_edge_type_alias(extracted_name, config)
            if not lookup_name or _is_exact_type_alias(lookup_name, type_aliases):
                continue
            lookups.append((entity_type, lookup_name, config))

        if not lookups:
            result = _collapse_entities(valid_confirmed)
            logger.info("resolve_entity: query=%r -> entity=%s", state["query"], result)
            return {"entity": result}

        exact_results = await asyncio.gather(
            *(_find_entity_by_name(config, name, pool) for _, name, config in lookups)
        )
        missing_indices = [
            index for index, result in enumerate(exact_results) if result is None
        ]
        candidates_by_index: dict[int, list[dict[str, Any]]] = {}
        if missing_indices:
            candidate_groups = await asyncio.gather(
                *(
                    _find_similar_entities(
                        lookups[index][2],
                        lookups[index][1],
                        pool,
                        resolution_settings,
                    )
                    for index in missing_indices
                )
            )
            candidates_by_index = dict(
                zip(missing_indices, candidate_groups, strict=True)
            )

        # Every lookup has completed. Fail in question order so a successful claim
        # can never hide another explicit unresolved claim.
        for index in missing_indices:
            entity_type, entity_name, _ = lookups[index]
            item_candidates = candidates_by_index[index]
            if item_candidates:
                logger.info(
                    "resolve_entity: type=%r name=%r -> ambiguous (%d candidates)",
                    entity_type,
                    entity_name,
                    len(item_candidates),
                )
                raise EntityAmbiguousError(item_candidates)
            logger.info(
                "resolve_entity: type=%r name=%r -> not found",
                entity_type,
                entity_name,
            )
            raise EntityNotFoundError(entity_name)

        resolved = list(valid_confirmed)
        _append_unique(
            resolved,
            [result for result in exact_results if result is not None],
        )
        result = _collapse_entities(resolved)
        logger.info("resolve_entity: query=%r -> entity=%s", state["query"], result)
        return {"entity": result}

    return resolve_entity


__all__ = [
    "EntityExtractionError",
    "EntityResolutionSettings",
    "load_entity_resolution_settings",
    "make_resolve_entity_node",
]
