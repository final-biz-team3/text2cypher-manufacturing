"""그래프 스키마에서 이름으로 검색 가능한 엔티티 타입을 도출하는 동작을 테스트한다."""

import logging
from pathlib import Path

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphSchema
from orchestrator.entity_types import list_named_entity_types

PROJECT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "schema" / "graph_schema.yaml"
)


def test_list_named_entity_types_includes_only_nodes_with_name_and_source() -> None:
    """name 속성과 source·uniqueKey·sourceColumn이 모두 있는 노드만 포함한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "source": {"schema": "production", "table": "product"},
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "WorkOrder": {
                    "uniqueKey": "workOrderId",
                    "source": {"schema": "production", "table": "workorder"},
                    "properties": {
                        "workOrderId": {
                            "type": "INTEGER",
                            "sourceColumn": "workorderid",
                        },
                    },
                },
            },
            "relationships": {},
        }
    )

    entity_types = list_named_entity_types(schema)

    assert len(entity_types) == 1
    assert entity_types[0].entity_type == "product"
    assert entity_types[0].table == "production.product"
    assert entity_types[0].id_column == "productid"
    assert entity_types[0].name_column == "name"
    assert entity_types[0].id_field == "productId"
    assert entity_types[0].name_field == "productName"


def test_list_named_entity_types_loads_project_schema() -> None:
    """프로젝트 기준 YAML에서 이름 있는 노드 4종을 도출한다."""
    schema = load_graph_schema(PROJECT_SCHEMA_PATH)

    entity_types = {
        entity.entity_type: entity for entity in list_named_entity_types(schema)
    }

    assert set(entity_types) == {"product", "supplier", "location", "scrapReason"}
    assert entity_types["supplier"].table == "purchasing.vendor"
    assert entity_types["supplier"].id_column == "businessentityid"
    assert entity_types["supplier"].id_field == "supplierId"
    assert entity_types["scrapReason"].table == "production.scrapreason"
    assert entity_types["scrapReason"].name_field == "scrapReasonName"


def test_list_named_entity_types_logs_warning_when_node_missing_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """name은 있지만 source가 없는 노드는 건너뛰며 경고 로그를 남긴다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
            },
            "relationships": {},
        }
    )

    with caplog.at_level(logging.WARNING):
        entity_types = list_named_entity_types(schema)

    assert entity_types == []
    assert any(
        "Product" in record.getMessage() and "source" in record.getMessage()
        for record in caplog.records
    )
