"""그래프 스키마에서 이름으로 검색 가능한 엔티티 타입 목록을 만든다."""

from dataclasses import dataclass

from agents.cypher.schema.models import GraphSchema


@dataclass(frozen=True)
class NamedEntityType:
    """이름으로 검색 가능한 엔티티 하나의 조회 정보를 담는다."""

    entity_type: str
    table: str
    id_column: str
    name_column: str
    id_field: str
    name_field: str


def list_named_entity_types(schema: GraphSchema) -> list[NamedEntityType]:
    """name 속성을 가진 노드를 엔티티 타입 목록으로 변환한다."""
    entity_types: list[NamedEntityType] = []

    for node_name, node in schema.nodes.items():
        if "name" not in node.properties:
            continue
        if node.source is None or node.unique_key is None:
            continue
        if node.unique_key not in node.properties:
            continue

        id_source_column = node.properties[node.unique_key].source_column
        name_source_column = node.properties["name"].source_column
        if id_source_column is None or name_source_column is None:
            continue

        entity_type = node_name[0].lower() + node_name[1:]

        entity_types.append(
            NamedEntityType(
                entity_type=entity_type,
                table=f"{node.source.schema_name}.{node.source.table}",
                id_column=id_source_column,
                name_column=name_source_column,
                id_field=node.unique_key,
                name_field=f"{entity_type}Name",
            )
        )

    return entity_types
