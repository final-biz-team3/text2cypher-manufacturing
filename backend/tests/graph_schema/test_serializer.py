"""그래프 스키마의 프롬프트용 텍스트 직렬화 동작을 테스트한다."""

from pathlib import Path

from graph_schema.loader import load_graph_schema
from graph_schema.models import GraphSchema
from graph_schema.serializer import serialize_graph_schema

PROJECT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "schema" / "graph_schema.yaml"
)


def test_serialize_graph_schema_follows_neo4j_text2cypher_format() -> None:
    """노드, 관계 속성과 관계 방향을 Neo4j 기본 형식으로 직렬화한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Supplier": {
                    "properties": {
                        "name": {"type": "STRING"},
                    },
                },
                "Product": {
                    "properties": {
                        "productId": {"type": "INTEGER"},
                        "name": {"type": "STRING"},
                    },
                },
            },
            "relationships": {
                "SUPPLIES": {
                    "from": "Supplier",
                    "to": "Product",
                    "properties": {
                        "standardPrice": {"type": "FLOAT"},
                    },
                },
            },
        }
    )

    schema_text = serialize_graph_schema(schema)

    assert schema_text == """Node properties:
Supplier {name: STRING}
Product {productId: INTEGER, name: STRING}
Relationship properties:
SUPPLIES {standardPrice: FLOAT}
The relationships:
(:Supplier)-[:SUPPLIES]->(:Product)"""


def test_serialize_graph_schema_formats_empty_properties_with_braces() -> None:
    """속성이 없는 노드와 관계는 빈 중괄호로 표현한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Supplier": {"properties": {}},
                "Product": {"properties": {}},
            },
            "relationships": {
                "SUPPLIES": {
                    "from": "Supplier",
                    "to": "Product",
                    "properties": {},
                },
            },
        }
    )

    schema_text = serialize_graph_schema(schema)

    assert schema_text == """Node properties:
Supplier {}
Product {}
Relationship properties:
SUPPLIES {}
The relationships:
(:Supplier)-[:SUPPLIES]->(:Product)"""


def test_serialize_graph_schema_keeps_sections_without_relationships() -> None:
    """관계가 없어도 공식 형식의 세 영역 제목을 유지한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "properties": {
                        "name": {"type": "STRING"},
                    },
                },
            },
            "relationships": {},
        }
    )

    schema_text = serialize_graph_schema(schema)

    assert schema_text == """Node properties:
Product {name: STRING}
Relationship properties:
The relationships:"""


def test_serialize_graph_schema_serializes_project_yaml() -> None:
    """프로젝트 기준 YAML을 프롬프트용 스키마 문자열로 변환한다."""
    schema = load_graph_schema(PROJECT_SCHEMA_PATH)

    schema_text = serialize_graph_schema(schema)

    assert schema_text.startswith(
        "Node properties:\nProduct {productId: INTEGER, name: STRING"
    )
    assert "REQUIRES_COMPONENT {bomId: INTEGER" in schema_text
    assert "(:Supplier)-[:SUPPLIES]->(:Product)" in schema_text
    assert "(:Product)-[:REQUIRES_COMPONENT]->(:Product)" in schema_text
    assert "sourceColumn" not in schema_text
    assert not schema_text.endswith("\n")
