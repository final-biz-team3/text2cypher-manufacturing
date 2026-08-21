"""그래프 스키마를 Neo4j Text2Cypher용 문자열로 직렬화한다.

Neo4j GraphRAG ``format_schema()``의 기본 출력 구조를 따라 노드 속성,
관계 속성 및 관계 연결 구조를 세 영역으로 구분한다. 한글 업무 용어가 있으면
물리 스키마 문법과 섞이지 않도록 마지막에 별도의 alias 영역을 덧붙인다.

Node properties:
Product {productId: INTEGER, name: STRING}

Relationship properties:
SUPPLIES {standardPrice: FLOAT}

The relationships:
(:Supplier)-[:SUPPLIES]->(:Product)

Aliases:
Node Product: 제품, 부품, 완제품
Node property Product.productId: 제품 ID, 부품 ID, 완제품 ID
Relationship SUPPLIES: 부품 공급 관계, 부품을 공급함

각 영역의 노드, 관계 및 속성은 ``GraphSchema``에 저장된 순서대로 출력하며
별도로 정렬하지 않는다.
"""

from agents.cypher.schema.models import GraphSchema, PropertySchema


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


def _format_aliases(schema: GraphSchema) -> list[str]:
    """물리 스키마와 구분되는 사용자 용어 alias 영역을 만든다."""
    lines: list[str] = []

    for node_name, node in schema.nodes.items():
        if node.aliases:
            lines.append(f"Node {node_name}: {', '.join(node.aliases)}")

        for property_name, property_schema in node.properties.items():
            if property_schema.aliases:
                lines.append(
                    f"Node property {node_name}.{property_name}: "
                    f"{', '.join(property_schema.aliases)}"
                )

    for relationship_name, relationship in schema.relationships.items():
        if relationship.aliases:
            lines.append(
                f"Relationship {relationship_name}: "
                f"{', '.join(relationship.aliases)}"
            )

        for property_name, property_schema in relationship.properties.items():
            if property_schema.aliases:
                lines.append(
                    f"Relationship property {relationship_name}.{property_name}: "
                    f"{', '.join(property_schema.aliases)}"
                )

    return lines


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

    alias_lines = _format_aliases(schema)
    if alias_lines:
        lines.append("Aliases:")
        lines.extend(alias_lines)

    return "\n".join(lines)
