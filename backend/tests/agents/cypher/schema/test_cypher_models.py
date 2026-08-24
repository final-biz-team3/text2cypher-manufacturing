"""그래프 스키마 입력 모델의 검증 동작을 테스트한다."""

import pytest
from pydantic import ValidationError

from agents.cypher.schema.models import GraphSchema


def test_graph_schema_maps_valid_yaml_relationship_fields() -> None:
    """YAML 관계 필드를 Python 모델의 필드명으로 변환한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Supplier": {
                    "aliases": ["공급업체", "업체"],
                    "properties": {
                        "supplierId": {"type": "INTEGER"},
                    },
                },
                "Product": {
                    "properties": {
                        "name": {"type": "STRING"},
                    },
                },
            },
            "relationships": {
                "SUPPLIES": {
                    "from": "Supplier",
                    "to": "Product",
                    "aliases": ["공급 관계"],
                    "properties": {
                        "standardPrice": {
                            "type": "FLOAT",
                            "aliases": ["표준 가격"],
                        },
                    },
                },
            },
        }
    )

    relationship = schema.relationships["SUPPLIES"]

    assert relationship.from_node == "Supplier"
    assert relationship.to_node == "Product"
    assert relationship.aliases == ["공급 관계"]
    assert schema.nodes["Supplier"].aliases == ["공급업체", "업체"]
    assert relationship.properties["standardPrice"].data_type == "FLOAT"
    assert relationship.properties["standardPrice"].aliases == ["표준 가격"]


def test_graph_schema_maps_query_policy_and_ignores_other_metadata() -> None:
    """BOM 쿼리 정책은 매핑하고 나머지 스키마 메타데이터는 무시한다."""
    schema = GraphSchema.model_validate(
        {
            "meta": {
                "bomAsOfDate": "2014-08-08",
                "bomMaxDepth": 4,
                "nodeCount": 1,
            },
            "nodes": {
                "Product": {
                    "group": "master",
                    "uniqueKey": "productId",
                    "properties": {
                        "productId": {
                            "type": "INTEGER",
                            "sourceColumn": "제품ID",
                            "nullable": False,
                        },
                    },
                },
            },
            "relationships": {},
        }
    )

    assert schema.query_policy is not None
    assert schema.query_policy.bom_as_of_date == "2014-08-08"
    assert schema.query_policy.bom_max_depth == 4
    assert schema.model_dump() == {
        "nodes": {
            "Product": {
                "aliases": [],
                "properties": {
                    "productId": {
                        "data_type": "INTEGER",
                        "aliases": [],
                    },
                },
            },
        },
        "relationships": {},
    }


@pytest.mark.parametrize(
    "meta",
    [
        pytest.param(
            {"bomAsOfDate": "2014-13-40", "bomMaxDepth": 4},
            id="invalid-date",
        ),
        pytest.param(
            {"bomAsOfDate": "2014-08-08", "bomMaxDepth": 0},
            id="invalid-depth",
        ),
    ],
)
def test_graph_schema_rejects_invalid_bom_query_policy(meta: dict[str, object]) -> None:
    """BOM 기준일이 잘못됐거나 최대 깊이가 1보다 작으면 거부한다."""
    with pytest.raises(ValidationError):
        GraphSchema.model_validate(
            {
                "meta": meta,
                "nodes": {"Product": {"properties": {}}},
                "relationships": {},
            }
        )


def test_graph_schema_rejects_empty_nodes() -> None:
    """노드가 하나도 없는 그래프 스키마는 거부한다."""
    with pytest.raises(ValidationError):
        GraphSchema.model_validate(
            {
                "nodes": {},
                "relationships": {},
            }
        )


def test_property_schema_rejects_unsupported_data_type() -> None:
    """프로젝트에서 지원하지 않는 속성 데이터 타입은 거부한다."""
    with pytest.raises(ValidationError):
        GraphSchema.model_validate(
            {
                "nodes": {
                    "Product": {
                        "properties": {
                            "name": {"type": "TEXT"},
                        },
                    },
                },
                "relationships": {},
            }
        )


def test_graph_schema_rejects_aliases_that_are_not_a_list() -> None:
    """alias는 하나뿐이어도 문자열 목록으로만 입력받는다."""
    with pytest.raises(ValidationError):
        GraphSchema.model_validate(
            {
                "nodes": {
                    "Product": {
                        "aliases": "제품",
                        "properties": {},
                    },
                },
                "relationships": {},
            }
        )


@pytest.mark.parametrize(
    ("nodes", "relationship", "error_message"),
    [
        pytest.param(
            {"Product": {"properties": {}}},
            {
                "from": "Supplier",
                "to": "Product",
                "properties": {},
            },
            "unknown source node 'Supplier'",
            id="unknown-source-node",
        ),
        pytest.param(
            {"Supplier": {"properties": {}}},
            {
                "from": "Supplier",
                "to": "Product",
                "properties": {},
            },
            "unknown target node 'Product'",
            id="unknown-target-node",
        ),
    ],
)
def test_graph_schema_rejects_relationship_referencing_unknown_node(
    nodes: dict[str, object],
    relationship: dict[str, object],
    error_message: str,
) -> None:
    """관계가 존재하지 않는 시작점 또는 도착점 노드를 참조하면 거부한다."""
    with pytest.raises(ValidationError, match=error_message):
        GraphSchema.model_validate(
            {
                "nodes": nodes,
                "relationships": {
                    "SUPPLIES": relationship,
                },
            }
        )


def test_node_schema_accepts_empty_properties() -> None:
    """속성이 없는 노드는 빈 속성 모음으로 허용한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {"properties": {}},
            },
            "relationships": {},
        }
    )

    assert schema.nodes["Product"].properties == {}


def test_relationship_schema_accepts_empty_properties() -> None:
    """속성이 없는 관계는 빈 속성 모음으로 허용한다."""
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

    assert schema.relationships["SUPPLIES"].properties == {}


def test_graph_schema_accepts_empty_relationships() -> None:
    """관계가 없는 스키마는 빈 관계 모음으로 허용한다."""
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

    assert schema.relationships == {}


def test_graph_schema_rejects_missing_relationships_field() -> None:
    """관계 필드 자체가 누락된 그래프 스키마는 거부한다."""
    with pytest.raises(ValidationError):
        GraphSchema.model_validate(
            {
                "nodes": {
                    "Product": {"properties": {}},
                },
            }
        )


def test_node_schema_captures_source_and_unique_key_for_internal_use() -> None:
    """source·uniqueKey·sourceColumn을 파싱하되 model_dump에는 포함하지 않는다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "source": {"schema": "production", "table": "product"},
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                    },
                },
            },
            "relationships": {},
        }
    )

    node = schema.nodes["Product"]
    assert node.unique_key == "productId"
    assert node.source is not None
    assert node.source.schema_name == "production"
    assert node.source.table == "product"
    assert node.properties["productId"].source_column == "productid"
    assert "unique_key" not in schema.model_dump()["nodes"]["Product"]
    assert "source" not in schema.model_dump()["nodes"]["Product"]
