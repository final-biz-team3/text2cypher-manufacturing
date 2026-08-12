"""그래프 스키마를 Neo4j Text2Cypher용 문자열로 직렬화한다.

Neo4j GraphRAG ``format_schema()``의 기본 출력 구조를 따라 노드 속성,
관계 속성 및 관계 연결 구조를 다음 세 영역으로 구분한다.

Node properties:
Product {productId: INTEGER, name: STRING}

Relationship properties:
SUPPLIES {standardPrice: FLOAT}

The relationships:
(:Supplier)-[:SUPPLIES]->(:Product)

각 영역의 노드, 관계 및 속성은 ``GraphSchema``에 저장된 순서대로 출력하며
별도로 정렬하지 않는다.
"""

from graph_schema.models import GraphSchema, PropertySchema


def _format_properties(
    name: str,
    properties: dict[str, PropertySchema],
) -> str:
    """노드 또는 관계의 속성을 Neo4j 기본 스키마 형식으로 표현한다."""
    formatted_properties = ", ".join(
        f"{property_name}: {property_schema.data_type}"
        for property_name, property_schema in properties.items()
    )

    return f"{name} {{{formatted_properties}}}"


def serialize_graph_schema(schema: GraphSchema) -> str:
    """검증된 그래프 스키마를 프롬프트용 텍스트로 직렬화한다."""
    lines = ["Node properties:"]
    lines.extend(
        _format_properties(node_name, node.properties)
        for node_name, node in schema.nodes.items()
    )

    lines.append("Relationship properties:")
    lines.extend(
        _format_properties(relationship_name, relationship.properties)
        for relationship_name, relationship in schema.relationships.items()
    )

    lines.append("The relationships:")
    lines.extend(
        f"(:{relationship.from_node})-[:{relationship_name}]->"
        f"(:{relationship.to_node})"
        for relationship_name, relationship in schema.relationships.items()
    )

    return "\n".join(lines)
