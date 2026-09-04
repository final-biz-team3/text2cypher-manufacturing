"""선언형 제조 ontology와 컴파일된 catalog 계약을 테스트한다."""

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
    assert "bom-component-usage" in production.result_shapes


def test_result_shape_matches_narrow_terms_and_excludes_entity_names_and_numbers() -> (
    None
):
    catalog = build_output_catalog(SQL_SCHEMA, GRAPH_SCHEMA)

    matched = catalog.match_result_shape_sources(
        "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘",
        {"productId": 680, "productName": "Paint - Black"},
    )
    broad = catalog.match_result_shape_sources("제품을 알려줘", None)
    masked = catalog.match_result_shape_sources(
        "공급 영향 완제품 123의 color",
        {"productId": 123, "productName": "공급 영향 완제품 123"},
    )

    assert set(matched) == {"graph"}
    assert matched["graph"].row_grain == ("pathProductIds",)
    assert matched["graph"].result_invariant == "bom_path_v1"
    assert not broad
    assert not masked


def test_result_shape_ambiguity_fails_open() -> None:
    data = _ontology_data()
    existing = next(
        shape
        for shape in data["resultShapes"]
        if shape["shapeId"] == "workplace-products"
    )
    duplicate = deepcopy(existing)
    duplicate["shapeId"] = "ambiguous-workplace-products"
    duplicate["sources"]["graph"]["rowGrain"] = ["locationId"]
    data["resultShapes"].append(duplicate)

    matched = _compile(data).match_result_shape_sources(
        "Frame Forming 작업장을 거친 제품을 알려줘", None
    )

    assert not matched


def test_result_shape_references_are_source_validated_and_fingerprinted() -> None:
    data = _ontology_data()
    changed = deepcopy(data)
    changed["resultShapes"][0]["sources"]["graph"]["requiredOutputs"].append(
        "quantityPerAssembly"
    )
    changed["resultShapes"][0]["sources"]["graph"]["completionGroups"][0].append(
        "quantityPerAssembly"
    )

    assert _compile(data).fingerprint != _compile(changed).fingerprint

    changed["resultShapes"][0]["sources"]["graph"]["rowGrain"] = ["notAnAlias"]
    with pytest.raises(ValueError, match="unavailable from graph"):
        _compile(changed)


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


@pytest.mark.parametrize(
    ("alias", "inputs"),
    [
        ("priceCostGap", []),
        ("priceCostGap", ["listPrice"]),
        ("priceCostGap", ["listPrice", "standardCost", "productId"]),
        ("actualStock", []),
        ("depth", ["productId"]),
    ],
)
def test_operation_input_arity_is_validated(alias: str, inputs: list[str]) -> None:
    data = _ontology_data()
    concept = next(item for item in data["businessConcepts"] if item["alias"] == alias)
    concept["inputs"] = inputs

    with pytest.raises(ValidationError, match=r"requires .* input"):
        ManufacturingOntology.model_validate(data)


def test_concept_kind_and_operation_must_agree() -> None:
    data = _ontology_data()
    concept = next(
        item for item in data["businessConcepts"] if item["alias"] == "priceCostGap"
    )
    concept["kind"] = "aggregate"

    with pytest.raises(ValidationError, match="cannot use operation"):
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


def test_entity_identity_projection_rejects_unrelated_label_owner() -> None:
    data = _ontology_data()
    product = next(item for item in data["entityRoles"] if item["roleId"] == "product")
    product["identityProjection"]["sql"]["labels"] = ["supplierName"]

    with pytest.raises(ValueError, match="labels unrelated"):
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


def test_relationship_product_roles_are_graph_scoped() -> None:
    catalog = build_output_catalog(SQL_SCHEMA, GRAPH_SCHEMA)

    assert "product" in catalog.allowed_entity_roles("sql")
    assert "finishedProduct" not in catalog.allowed_entity_roles("sql")
    assert "rootProduct" not in catalog.allowed_entity_roles("sql")
    assert "finishedProductId" not in catalog.allowed_aliases("sql")
    assert "rootProductId" not in catalog.allowed_aliases("sql")
    assert "finishedProduct" in catalog.allowed_entity_roles("graph")
    assert "rootProduct" in catalog.allowed_entity_roles("graph")


def test_root_product_means_hierarchy_root_not_every_traversal_start() -> None:
    catalog = build_output_catalog(SQL_SCHEMA, GRAPH_SCHEMA)

    assert catalog.by_tool["graph"]["rootProductId"].canonical == (
        "BOM 계층 루트 제품 식별자"
    )
    assert "탐색 시작" not in catalog.by_tool["graph"]["rootProductId"].canonical
    assert "계층이나 부품 트리 자체" in catalog.entity_roles["rootProduct"].canonical
    assert "지정된 두 끝점 경로" in (catalog.entity_roles["finishedProduct"].canonical)


def test_graph_traversal_recipes_are_not_part_of_the_ontology_contract() -> None:
    data = _ontology_data()
    data["graphTraversals"] = [
        {
            "relationship": "REQUIRES_COMPONENT",
            "roles": ["finishedProductA", "component"],
            "direction": "forward",
            "evidenceByMode": {"aggregate": ["minDepthA"]},
        }
    ]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ManufacturingOntology.model_validate(data)


def test_entity_role_ontology_rejects_implicit_predicates() -> None:
    data = _ontology_data()
    finished_product = next(
        item for item in data["entityRoles"] if item["roleId"] == "finishedProduct"
    )
    finished_product["predicates"] = {
        "graph": [
            {
                "field": "sellableFinishedGood",
                "operator": "equals",
                "value": True,
            }
        ]
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ManufacturingOntology.model_validate(data)


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
