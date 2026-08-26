"""이름으로 검색 가능한 엔티티 타입의 조회 정보를 구성한다."""

import logging
import re
from dataclasses import dataclass

from agents.cypher.schema.models import GraphSchema

logger = logging.getLogger(__name__)

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_COLUMN_PATTERN = re.compile(rf"^{_IDENTIFIER}$")
_TABLE_PATTERN = re.compile(rf"^{_IDENTIFIER}\.{_IDENTIFIER}$")


@dataclass(frozen=True)
class NamedEntityType:
    """이름으로 검색 가능한 엔티티 하나의 조회 정보를 담는다."""

    entity_type: str
    table: str
    id_column: str
    name_column: str
    id_field: str
    name_field: str
    aliases: tuple[str, ...] = ()


_SQL_NAMED_ENTITY_TYPES = (
    NamedEntityType(
        entity_type="productCategory",
        table="production.productcategory",
        id_column="productcategoryid",
        name_column="name",
        id_field="productCategoryId",
        name_field="productCategoryName",
        aliases=("제품 분류",),
    ),
)


def list_named_entity_types(schema: GraphSchema) -> list[NamedEntityType]:
    """name 속성을 가진 노드를 엔티티 타입 목록으로 변환한다."""
    entity_types: list[NamedEntityType] = []

    for node_name, node in schema.nodes.items():
        if "name" not in node.properties:
            continue
        if node.source is None:
            logger.warning(
                "list_named_entity_types: node=%r source 없음, 건너뜀", node_name
            )
            continue
        if node.unique_key is None:
            logger.warning(
                "list_named_entity_types: node=%r uniqueKey 없음, 건너뜀", node_name
            )
            continue
        if node.unique_key not in node.properties:
            logger.warning(
                "list_named_entity_types: node=%r uniqueKey=%r 속성 없음, 건너뜀",
                node_name,
                node.unique_key,
            )
            continue

        id_source_column = node.properties[node.unique_key].source_column
        name_source_column = node.properties["name"].source_column
        if id_source_column is None:
            logger.warning(
                "list_named_entity_types: node=%r uniqueKey=%r sourceColumn 없음, "
                "건너뜀",
                node_name,
                node.unique_key,
            )
            continue
        if name_source_column is None:
            logger.warning(
                "list_named_entity_types: node=%r name.sourceColumn 없음, 건너뜀",
                node_name,
            )
            continue

        table = f"{node.source.schema_name}.{node.source.table}"
        if not _TABLE_PATTERN.match(table):
            logger.warning(
                "list_named_entity_types: node=%r table=%r 식별자 형식이 아님, 건너뜀",
                node_name,
                table,
            )
            continue
        if not _COLUMN_PATTERN.match(id_source_column) or not _COLUMN_PATTERN.match(
            name_source_column
        ):
            logger.warning(
                "list_named_entity_types: node=%r 컬럼명이 식별자 형식이 아님, 건너뜀",
                node_name,
            )
            continue

        entity_type = node_name[0].lower() + node_name[1:]

        entity_types.append(
            NamedEntityType(
                entity_type=entity_type,
                table=table,
                id_column=id_source_column,
                name_column=name_source_column,
                id_field=node.unique_key,
                name_field=f"{entity_type}Name",
                aliases=tuple(node.aliases),
            )
        )

    return entity_types


def list_resolvable_entity_types(schema: GraphSchema) -> list[NamedEntityType]:
    """그래프와 SQL에서 이름으로 확정할 수 있는 엔티티 타입을 반환한다."""
    entity_types = list_named_entity_types(schema)
    existing_types = {entity.entity_type for entity in entity_types}
    entity_types.extend(
        entity
        for entity in _SQL_NAMED_ENTITY_TYPES
        if entity.entity_type not in existing_types
    )
    return entity_types
