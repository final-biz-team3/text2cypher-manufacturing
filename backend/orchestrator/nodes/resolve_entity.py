"""명시적으로 추출한 이름을 DB의 정확·유사 조회로 식별한다."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from agents.cypher.schema.models import GraphSchema
from core.observability.model_calls import observe_model_call
from orchestrator.entity_types import NamedEntityType, list_resolvable_entity_types
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.3
_DEFAULT_CANDIDATE_LIMIT = 5

# 한국어 질문에 포함된 영문 DB 이름 후보를 equality 조회에 사용한다.
_ASCII_NAME_CANDIDATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \t,.'&()+/_-]*")

_SYSTEM_PROMPT = (
    "사용자에게 답변하거나 추가 자료를 요청하지 않는다. 도구에 정의된 entity "
    "종류 중 질문에 고유 이름이 명시된 경우에만 extract_entities를 호출한다. "
    "종류, 상태, 수량, 범위를 나타내는 일반 표현은 고유 이름이 아니므로 호출하지 "
    "않는다. 이름이 entity 종류 표현 없이 나타나도 조회 범위를 한정하면 추출한다. "
    "질문에 서로 다른 고유 이름이 여러 개 있으면 모두 entities 배열에 넣고 질문에 "
    "등장한 순서를 유지한다. 쉼표를 포함한 색상·크기·모델 표기는 이름의 일부로 "
    "그대로 유지한다. 숫자 ID만 있는 표현은 이름으로 추출하지 않는다."
)


class EntityExtractionError(ValueError):
    """모델이 잘못된 호출 또는 형식으로 추출 경계를 호출했음을 나타낸다."""


@dataclass(frozen=True)
class EntityResolutionSettings:
    similarity_threshold: float
    candidate_limit: int


def load_entity_resolution_settings() -> EntityResolutionSettings:
    """환경에서 조회 설정을 읽고 허용 범위를 검사한다."""
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
            "name": "extract_entities",
            "strict": True,
            "description": (
                "질문에서 특정 대상을 지칭하는 모든 고유 이름과 종류를 "
                "등장 순서대로 한 번에 추출한다. "
                "특정 이름이 없으면 호출하지 않는다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "entityType": {
                                    "type": "string",
                                    "enum": [
                                        entity.entity_type for entity in entity_types
                                    ],
                                    "description": type_descriptions,
                                },
                                "entityName": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": (
                                        "질문에 등장한 고유 이름 문자열 그대로"
                                    ),
                                },
                            },
                            "required": ["entityType", "entityName"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["entities"],
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
    model = os.environ["OPENAI_MODEL"]
    response = await observe_model_call(
        "resolve_entity",
        model,
        openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            tools=[extract_tool],
            reasoning_effort="none",
        ),
    )
    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        return []

    extractions: list[tuple[str, str]] = []
    for tool_call in tool_calls:
        if tool_call.function.name != "extract_entities":
            raise EntityExtractionError(
                f"unsupported entity extraction tool: {tool_call.function.name!r}"
            )
        try:
            arguments = json.loads(tool_call.function.arguments)
        except (json.JSONDecodeError, TypeError) as exc:
            raise EntityExtractionError(
                "extract_entities arguments must be valid JSON"
            ) from exc
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"entities"}
            or not isinstance(arguments["entities"], list)
        ):
            raise EntityExtractionError(
                "extract_entities arguments have an invalid shape"
            )
        for item in arguments["entities"]:
            if not isinstance(item, dict) or set(item) != {
                "entityType",
                "entityName",
            }:
                raise EntityExtractionError(
                    "extract_entities items have an invalid shape"
                )
            entity_type = item["entityType"]
            entity_name = item["entityName"]
            if (
                not isinstance(entity_type, str)
                or entity_type not in allowed_types
                or not isinstance(entity_name, str)
                or not entity_name.strip()
            ):
                raise EntityExtractionError(
                    "extract_entities arguments have invalid values"
                )
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


def _literal_name_candidates(query: str) -> tuple[str, ...]:
    """질문의 영문 이름 후보와 문장부호 제거 변형을 반환한다."""
    candidates: list[str] = []
    for match in _ASCII_NAME_CANDIDATE.finditer(query):
        value = match.group().strip()
        if not value or not any(character.isalpha() for character in value):
            continue
        variants = (value, value.rstrip(".,;:!?"))
        for candidate in variants:
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return tuple(candidates)


def _is_ascii_word_character(value: str) -> bool:
    return value.isascii() and (value.isalnum() or value == "_")


def _literal_name_spans(query: str, name: str) -> list[tuple[int, int]]:
    """질문에 포함된 DB 이름의 완전한 token 범위를 반환한다.

    ASCII 단어 경계만 강제한다. 다른 영단어 안에 포함된 짧은 영문 이름은
    거부하면서도 영문 제품명이나 카테고리명 뒤에 한국어 조사가 공백 없이
    붙는 경우는 허용한다.
    """
    if not name:
        return []
    folded_query = query.casefold()
    folded_name = name.casefold()
    spans: list[tuple[int, int]] = []
    offset = 0
    while True:
        start = folded_query.find(folded_name, offset)
        if start < 0:
            return spans
        end = start + len(folded_name)
        starts_inside_word = (
            start > 0
            and _is_ascii_word_character(folded_name[0])
            and _is_ascii_word_character(folded_query[start - 1])
        )
        ends_inside_word = (
            end < len(folded_query)
            and _is_ascii_word_character(folded_name[-1])
            and _is_ascii_word_character(folded_query[end])
        )
        if not starts_inside_word and not ends_inside_word:
            spans.append((start, end))
        offset = start + 1


@dataclass(frozen=True)
class _LiteralEntityMatch:
    start: int
    end: int
    config: NamedEntityType
    entity: dict[str, Any]


async def _find_entities_by_names(
    config: NamedEntityType,
    names: tuple[str, ...],
    pool: AsyncConnectionPool,
) -> dict[str, list[dict[str, Any]]]:
    """후보 이름만 equality로 조회해 이름별 엔티티 목록을 반환한다."""
    if not names:
        return {}
    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT {config.id_column}, {config.name_column} "
            f"FROM {config.table} "
            f"WHERE {config.name_column} = ANY(%s)",
            (list(names),),
        )
        rows = await cursor.fetchall()

    entities_by_name: dict[str, list[dict[str, Any]]] = {}
    for identifier, name in rows:
        if not isinstance(name, str):
            continue
        entity = {config.id_field: identifier, config.name_field: name}
        entities_by_name.setdefault(name, []).append(entity)
    return entities_by_name


def _literal_matches(
    query: str,
    candidate_names: tuple[str, ...],
    config: NamedEntityType,
    entities_by_name: dict[str, list[dict[str, Any]]],
) -> list[_LiteralEntityMatch]:
    """equality 조회 결과를 질문상의 literal span과 다시 연결한다."""
    candidates = set(candidate_names)
    matches: list[_LiteralEntityMatch] = []
    for name, entities in entities_by_name.items():
        if name not in candidates:
            continue
        spans = _literal_name_spans(query, name)
        for entity in entities:
            matches.extend(
                _LiteralEntityMatch(start, end, config, entity) for start, end in spans
            )
    return matches


def _select_literal_entities(
    matches: list[_LiteralEntityMatch],
) -> tuple[list[dict[str, Any]], bool]:
    """겹치지 않는 가장 긴 literal 이름을 질문 순서대로 선택한다.

    서로 다른 엔티티 타입이 같은 범위를 공유하면 DB 텍스트만으로 의미 역할을
    결정할 수 없으므로 LLM의 판단에 맡긴다.
    """
    grouped: dict[tuple[int, int], list[_LiteralEntityMatch]] = {}
    for match in matches:
        grouped.setdefault((match.start, match.end), []).append(match)

    occupied: list[tuple[int, int]] = []
    selected: list[_LiteralEntityMatch] = []
    has_ambiguous_span = False
    for (start, end), candidates in sorted(
        grouped.items(),
        key=lambda item: (-(item[0][1] - item[0][0]), item[0][0]),
    ):
        overlaps = any(
            start < occupied_end and occupied_start < end
            for occupied_start, occupied_end in occupied
        )
        if overlaps:
            continue
        occupied.append((start, end))
        identities = {
            (candidate.config.entity_type, tuple(candidate.entity.items()))
            for candidate in candidates
        }
        if len(identities) != 1:
            has_ambiguous_span = True
            continue
        selected.append(candidates[0])

    selected.sort(key=lambda match: match.start)
    entities: list[dict[str, Any]] = []
    _append_unique(entities, [match.entity for match in selected])
    return entities, has_ambiguous_span


def _is_exact_type_alias(name: str, type_aliases: frozenset[str]) -> bool:
    return _normalized_label(name) in type_aliases


def _strip_edge_type_alias(name: str, config: NamedEntityType) -> str:
    """공백으로 구분된 정확한 타입 접두사 또는 접미사만 제거한다."""
    normalized = name.strip()
    folded = normalized.casefold()
    for alias in sorted((*config.aliases, config.entity_type), key=len, reverse=True):
        alias_folded = alias.casefold()
        if folded.startswith(f"{alias_folded} "):
            return normalized[len(alias) :].strip()
        if folded.endswith(f" {alias_folded}"):
            return normalized[: -len(alias)].strip()
    return normalized


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


@dataclass(frozen=True)
class _ConfirmedEntity:
    entity: dict[str, Any]
    # 이 확정값이 어떤 모호함 질문(entity_name, 원문 그대로의 추출 이름)에
    # 대한 응답인지 보여주는 상관관계 키. EntityAmbiguousError.lookup_name을
    # 클라이언트가 그대로 되돌려보낸 값이다.
    for_name: str
    config: NamedEntityType


def _parse_confirmed_entity(
    raw: Any, entity_types: list[NamedEntityType]
) -> _ConfirmedEntity | None:
    """{"entity": {...}, "forName": "..."} 형태의 wire item을 검증한다.
    entity 자체는 알려진 엔티티 타입의 id/name 필드 조합과 일치해야 하고,
    forName은 비어있지 않은 문자열이어야 한다."""
    if not isinstance(raw, dict) or set(raw) != {"entity", "forName"}:
        return None
    for_name = raw["forName"]
    if not isinstance(for_name, str) or not for_name:
        return None
    config = _confirmed_entity_config(raw["entity"], entity_types)
    if config is None:
        return None
    return _ConfirmedEntity(entity=raw["entity"], for_name=for_name, config=config)


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


def _confirmed_entity_covers_lookup(
    valid_confirmed: list[_ConfirmedEntity],
    entity_type: str,
    entity_name: str,
) -> bool:
    """확정 엔티티가 같은 타입과 정확히 같은 표현(entity_name)에 대한 답으로
    확정된 경우에만 아직 해결되지 않은 조회를 충족한다고 판단한다. 텍스트
    유사도만으로는 충족하지 않는다.

    유사도만으로는 사용자가 같은 모호성 질문에서 후보를 다시 선택한 경우와
    우연히 비슷하게 생긴 새로운 대상을 언급한 경우를 구분할 수 없다. 예를 들어
    이미 확정된 "Mountain-100 Black, 38"은 새로운 "Mountain-100" 언급에도
    정상적인 상위 유사도 후보다. 이때 확정 엔티티가 잘못된 제품을 대신하지 않도록
    후보를 제시할 때 사용한 원래 조회 문자열과 정확히 일치하도록 요구한다.
    PR #55 리뷰에서 확인한 문제다."""
    return any(
        confirmed.for_name == entity_name
        and confirmed.config.entity_type == entity_type
        for confirmed in valid_confirmed
    )


def _append_unique(target: list[dict[str, Any]], values: list[dict[str, Any]]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _sort_entities_by_question(
    query: str, entities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """여러 경로에서 합친 엔티티를 질문 등장 순서로 안정화한다."""
    folded_query = query.casefold()

    def position(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, entity = item
        name = next(
            (
                value
                for key, value in entity.items()
                if key.endswith("Name") and isinstance(value, str)
            ),
            "",
        )
        found = folded_query.find(name.casefold()) if name else -1
        return (found if found >= 0 else len(query) + index, index)

    return [entity for _, entity in sorted(enumerate(entities), key=position)]


def make_resolve_entity_node(
    openai_client: Any,
    pool: Any,
    graph_schema: GraphSchema,
    *,
    settings: EntityResolutionSettings | None = None,
) -> Callable[[OrchestratorState], Any]:
    """명시적 추출 결과만 의미 입력으로 사용하는 resolver를 생성한다."""
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
        valid_confirmed: list[_ConfirmedEntity] = []
        for raw in _normalize_confirmed_entities(state.get("confirmed_entity")):
            parsed = _parse_confirmed_entity(raw, entity_types)
            if parsed is not None and await _confirmed_entity_exists(
                parsed.entity, parsed.config, pool
            ):
                if not any(c.entity == parsed.entity for c in valid_confirmed):
                    valid_confirmed.append(parsed)
            else:
                logger.warning(
                    "resolve_entity: invalid confirmed entity ignored: %r", raw
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

        literal_names = tuple(
            name
            for name in _literal_name_candidates(state["query"])
            if not _is_exact_type_alias(name, type_aliases)
        )
        names_by_type: dict[str, tuple[str, ...]] = {}
        for config in entity_types:
            names = list(literal_names)
            for _, lookup_name, lookup_config in lookups:
                if lookup_config.entity_type == config.entity_type:
                    names.append(lookup_name)
            names_by_type[config.entity_type] = tuple(dict.fromkeys(names))

        queried_configs = [
            config for config in entity_types if names_by_type[config.entity_type]
        ]
        lookup_groups = await asyncio.gather(
            *(
                _find_entities_by_names(config, names_by_type[config.entity_type], pool)
                for config in queried_configs
            )
        )
        entities_by_type = {
            config.entity_type: group
            for config, group in zip(queried_configs, lookup_groups, strict=True)
        }
        literal_matches = [
            match
            for config in queried_configs
            for match in _literal_matches(
                state["query"],
                literal_names,
                config,
                entities_by_type[config.entity_type],
            )
        ]
        literal_entities, has_ambiguous_literal = _select_literal_entities(
            literal_matches
        )
        if has_ambiguous_literal:
            logger.info(
                "resolve_entity: query=%r -> ambiguous literal span; "
                "using extracted entity types",
                state["query"],
            )

        if not lookups and not literal_entities:
            result = _collapse_entities([c.entity for c in valid_confirmed])
            logger.info("resolve_entity: query=%r -> entity=%s", state["query"], result)
            return {"entity": result}

        exact_results = [
            next(
                iter(entities_by_type.get(config.entity_type, {}).get(lookup_name, [])),
                None,
            )
            for _, lookup_name, config in lookups
        ]
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

        # 모든 조회가 끝났으면 질문 순서대로 실패를 판정한다. 성공한 조회가 다른
        # 명시적 미해결 조회를 가리지 못하게 하되, 이전에 확정한 엔티티로 이미
        # 해결된 조회는 제외한다. client는 확정된 이름으로 질문을 다시 쓰지 않고
        # 원래 표현과 confirmed_entity를 함께 다시 보낼 수 있다.
        for index in missing_indices:
            entity_type, entity_name, config = lookups[index]
            if _confirmed_entity_covers_lookup(
                valid_confirmed, entity_type, entity_name
            ):
                continue
            item_candidates = candidates_by_index[index]
            if item_candidates:
                logger.info(
                    "resolve_entity: type=%r name=%r -> ambiguous (%d candidates)",
                    entity_type,
                    entity_name,
                    len(item_candidates),
                )
                raise EntityAmbiguousError(item_candidates, lookup_name=entity_name)
            logger.info(
                "resolve_entity: type=%r name=%r -> not found",
                entity_type,
                entity_name,
            )
            raise EntityNotFoundError(entity_name)

        discovered: list[dict[str, Any]] = []
        _append_unique(
            discovered,
            [result for result in exact_results if result is not None],
        )
        _append_unique(discovered, literal_entities)
        resolved = [c.entity for c in valid_confirmed]
        _append_unique(resolved, _sort_entities_by_question(state["query"], discovered))
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
