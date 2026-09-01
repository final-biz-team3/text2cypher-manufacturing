"""Declarative manufacturing ontology and compiled catalog contracts."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.output_catalog import DEFAULT_ONTOLOGY_PATH, build_output_catalog
from orchestrator.semantic_catalog import (
    ManufacturingOntology,
    build_query_semantic_catalog,
    load_manufacturing_ontology,
    load_query_semantic_catalog,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SQL_SCHEMA = load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml")
GRAPH_SCHEMA = load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml")


def _ontology_data() -> dict:
    return load_manufacturing_ontology(DEFAULT_ONTOLOGY_PATH).model_dump(by_alias=True)


def _compile(data: dict):
    return build_query_semantic_catalog(
        SQL_SCHEMA,
        GRAPH_SCHEMA,
        ManufacturingOntology.model_validate(data),
    )


def test_package_loader_and_explicit_loader_have_the_same_fingerprint() -> None:
    production = build_output_catalog(SQL_SCHEMA, GRAPH_SCHEMA)
    evaluation = load_query_semantic_catalog(
        sql_schema=SQL_SCHEMA,
        graph_schema=GRAPH_SCHEMA,
        ontology_path=DEFAULT_ONTOLOGY_PATH,
    )

    assert production.fingerprint == evaluation.fingerprint
    assert production.ontology_version == "manufacturing-v1"
    assert "sellableFinishedGood" in production.allowed_aliases("graph")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["businessConcepts"][1].update(
            conceptId=data["businessConcepts"][0]["conceptId"]
        ),
        lambda data: data["outputRoles"][1].update(
            roleId=data["outputRoles"][0]["roleId"]
        ),
        lambda data: data["entityRoles"][1].update(
            roleId=data["entityRoles"][0]["roleId"]
        ),
    ],
)
def test_duplicate_semantic_ids_are_rejected(mutation) -> None:
    data = _ontology_data()
    mutation(data)

    with pytest.raises(ValidationError, match="must be unique"):
        ManufacturingOntology.model_validate(data)


def test_empty_normalized_term_is_rejected() -> None:
    data = _ontology_data()
    data["businessConcepts"][0]["terms"] = ["   "]

    with pytest.raises(ValidationError):
        ManufacturingOntology.model_validate(data)


def test_empty_entity_role_catalog_is_rejected() -> None:
    data = _ontology_data()
    data["entityRoles"] = []

    with pytest.raises(ValidationError):
        ManufacturingOntology.model_validate(data)


def test_canonical_and_alternative_term_must_be_locally_unique() -> None:
    data = _ontology_data()
    concept = data["businessConcepts"][0]
    concept["terms"] = [concept["canonical"].upper()]

    with pytest.raises(ValidationError, match="alternative terms"):
        ManufacturingOntology.model_validate(data)


def test_same_alternative_term_can_describe_multiple_concepts() -> None:
    data = _ontology_data()
    data["businessConcepts"][0]["terms"].append("synthetic shared term")
    data["businessConcepts"][1]["terms"].append("synthetic shared term")

    catalog = _compile(data)

    assert (
        "synthetic shared term" in catalog.by_tool["sql"]["activeSupplierCount"].terms
    )
    assert (
        "synthetic shared term" in catalog.by_tool["sql"]["purchasedProductCount"].terms
    )


def test_physical_alias_collision_is_rejected() -> None:
    data = _ontology_data()
    data["outputRoles"][0]["alias"] = "productId"

    with pytest.raises(ValueError, match="collides"):
        _compile(data)


@pytest.mark.parametrize("field", ["inputs", "grain"])
def test_unknown_concept_references_are_rejected(field: str) -> None:
    data = _ontology_data()
    data["businessConcepts"][0][field] = ["unknownAlias"]

    with pytest.raises(ValueError, match="unavailable"):
        _compile(data)


def test_concept_source_mismatch_is_rejected() -> None:
    data = _ontology_data()
    actual_stock = next(
        item for item in data["businessConcepts"] if item["alias"] == "actualStock"
    )
    actual_stock["sources"] = ["graph"]

    with pytest.raises(ValueError, match="unavailable from graph"):
        _compile(data)


def test_invalid_operation_is_rejected_by_the_strict_model() -> None:
    data = _ontology_data()
    data["businessConcepts"][0]["operation"] = "queryTemplate"

    with pytest.raises(ValidationError):
        ManufacturingOntology.model_validate(data)


def test_unknown_role_mapping_path_is_rejected() -> None:
    data = _ontology_data()
    data["outputRoles"][0]["mappings"]["sql"] = ["production.product.not_a_column"]

    with pytest.raises(ValueError, match="unknown sql paths"):
        _compile(data)


def test_entity_identity_projection_requires_owned_identity_and_name_aliases() -> None:
    data = _ontology_data()
    product = next(item for item in data["entityRoles"] if item["roleId"] == "product")
    product["identityProjection"]["sql"]["keys"] = ["listPrice"]

    with pytest.raises(ValueError, match="non-identity"):
        _compile(data)

    data = _ontology_data()
    product = next(item for item in data["entityRoles"] if item["roleId"] == "product")
    product["identityProjection"]["sql"]["labels"] = ["actualStock"]

    with pytest.raises(ValueError, match="non-display"):
        _compile(data)


def test_entity_role_projection_is_source_scoped_and_described() -> None:
    catalog = build_output_catalog(SQL_SCHEMA, GRAPH_SCHEMA)

    assert catalog.identity_projection("product", "sql").display_aliases == (
        "productId",
        "productName",
    )
    assert catalog.by_tool["sql"]["productName"].value_type == "name"
    assert "category" in catalog.allowed_entity_roles("sql")
    assert "category" not in catalog.allowed_entity_roles("graph")
    assert "keys=productId" in catalog.describe_entity_roles("sql")


def test_alternative_term_order_does_not_change_compiled_semantics() -> None:
    original = _ontology_data()
    reordered = deepcopy(original)
    for role in reordered["outputRoles"]:
        role["terms"].reverse()
    for role in reordered["entityRoles"]:
        role["terms"].reverse()
    for concept in reordered["businessConcepts"]:
        concept["terms"].reverse()

    first = _compile(original)
    second = _compile(reordered)

    assert first.fingerprint == second.fingerprint
    assert first.by_tool == second.by_tool


def test_catalog_description_exposes_provenance_and_operation() -> None:
    description = build_output_catalog(SQL_SCHEMA, GRAPH_SCHEMA).describe("sql")

    assert "actualStock" in description
    assert "kind=aggregate" in description
    assert "valueType=identity" in description
    assert "operation=sum" in description
    assert "inputs=quantity" in description
    assert "paths=production.product.productid" in description
    assert "owners=production.product" in description


def test_compiled_catalog_mappings_are_immutable() -> None:
    catalog = build_output_catalog(SQL_SCHEMA, GRAPH_SCHEMA)
    cast_catalog: Any = catalog

    with pytest.raises(TypeError):
        cast_catalog.by_tool["sql"]["newAlias"] = object()
    with pytest.raises(TypeError):
        cast_catalog.transforms["newTransform"] = object()
