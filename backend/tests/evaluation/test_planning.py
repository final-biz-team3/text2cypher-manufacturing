"""route DAG, 정렬된 binding 및 formal transform의 구조 계약을 테스트한다."""

import json
from functools import lru_cache
from pathlib import Path
from typing import cast

import pytest

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.output_catalog import build_output_catalog
from orchestrator.planning import (
    Subquery,
    derive_tool_plan,
    parse_route_draft,
    route_draft_json_schema,
    validate_result_transform,
    validate_subqueries,
)
from orchestrator.semantic_catalog import QuerySemanticCatalog, ToolName

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@lru_cache
def _catalog() -> QuerySemanticCatalog:
    return build_output_catalog(
        load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml"),
        load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml"),
    )


def _route(subqueries: list[dict], transform: dict | None = None) -> str:
    return json.dumps(
        {"subqueries": subqueries, "resultTransform": transform},
        ensure_ascii=False,
    )


def _draft(
    subquery_id: str,
    tool: str,
    *,
    depends_on: list[str] | None = None,
    join_keys: list[str] | None = None,
    bindings: list[dict] | None = None,
) -> dict:
    return {
        "id": subquery_id,
        "tool": tool,
        "question": f"{tool} responsibility",
        "dependsOn": depends_on or [],
        "joinKeys": join_keys or [],
        "inputBindings": bindings or [],
    }


def test_route_schema_omits_tool_plan_and_exposes_aligned_binding_array() -> None:
    schema = route_draft_json_schema(
        _catalog().shared_join_aliases,
        catalog=_catalog(),
    )

    assert "tool_plan" not in schema["properties"]
    assert "uniqueItems" not in json.dumps(schema)
    binding_schema = schema["properties"]["subqueries"]["items"]["properties"][
        "inputBindings"
    ]
    assert binding_schema["type"] == "array"
    assert set(binding_schema["items"]["required"]) == {
        "target",
        "sourceSubqueryId",
        "sourceOutput",
    }
    source_outputs = binding_schema["items"]["properties"]["sourceOutput"]["enum"]
    assert "componentId" in source_outputs
    assert "quantityPerAssembly" in source_outputs


@pytest.mark.parametrize("tool", ["sql", "graph"])
def test_single_source_route_derives_tool_plan(tool: str) -> None:
    parsed = parse_route_draft(
        _route([_draft(f"{tool}_query", tool)]),
        "question",
        catalog=_catalog(),
        shared_join_aliases=_catalog().shared_join_aliases,
    )

    assert parsed["tool_plan"] == [tool]
    assert "inputBindings" not in parsed["subqueries"][0]


def test_independent_hybrid_keeps_model_order_and_has_no_bindings() -> None:
    parsed = parse_route_draft(
        _route([_draft("sql_facts", "sql"), _draft("graph_paths", "graph")]),
        "question",
        catalog=_catalog(),
        shared_join_aliases=_catalog().shared_join_aliases,
    )

    assert parsed["tool_plan"] == ["sql", "graph"]
    assert all(not item["dependsOn"] for item in parsed["subqueries"])


def test_dependent_hybrid_derives_topological_order_and_compiles_many_bindings() -> (
    None
):
    consumer = _draft(
        "sql_consumer",
        "sql",
        depends_on=["graph_producer"],
        bindings=[
            {
                "target": "componentIds",
                "sourceSubqueryId": "graph_producer",
                "sourceOutput": "componentId",
            },
            {
                "target": "quantities",
                "sourceSubqueryId": "graph_producer",
                "sourceOutput": "quantityPerAssembly",
            },
        ],
    )
    producer = _draft("graph_producer", "graph")

    parsed = parse_route_draft(
        _route([consumer, producer]),
        "question",
        catalog=_catalog(),
        shared_join_aliases=_catalog().shared_join_aliases,
    )

    assert parsed["tool_plan"] == ["graph", "sql"]
    assert [item["id"] for item in parsed["subqueries"]] == [
        "graph_producer",
        "sql_consumer",
    ]
    assert parsed["subqueries"][1]["inputBindings"] == {
        "componentIds": "graph_producer.componentId",
        "quantities": "graph_producer.quantityPerAssembly",
    }
    assert all(item["joinKeys"] == [] for item in parsed["subqueries"])


def test_binding_target_must_be_safe_and_unique() -> None:
    bindings = [
        {
            "target": "unsafe-name",
            "sourceSubqueryId": "graph_base",
            "sourceOutput": "componentId",
        }
    ]

    with pytest.raises(ValueError, match="invalid values"):
        parse_route_draft(
            _route(
                [
                    _draft("graph_base", "graph"),
                    _draft(
                        "sql_next",
                        "sql",
                        depends_on=["graph_base"],
                        bindings=bindings,
                    ),
                ]
            ),
            "question",
            catalog=_catalog(),
        )

    bindings[0]["target"] = "values"
    bindings.append(dict(bindings[0]))
    with pytest.raises(ValueError, match="duplicated"):
        parse_route_draft(
            _route(
                [
                    _draft("graph_base", "graph"),
                    _draft(
                        "sql_next",
                        "sql",
                        depends_on=["graph_base"],
                        bindings=bindings,
                    ),
                ]
            ),
            "question",
            catalog=_catalog(),
        )


def test_binding_source_must_belong_to_the_producer_catalog() -> None:
    with pytest.raises(ValueError, match="not owned by graph producer"):
        parse_route_draft(
            _route(
                [
                    _draft("graph_base", "graph"),
                    _draft(
                        "sql_next",
                        "sql",
                        depends_on=["graph_base"],
                        bindings=[
                            {
                                "target": "values",
                                "sourceSubqueryId": "graph_base",
                                "sourceOutput": "standardCost",
                            }
                        ],
                    ),
                ]
            ),
            "question",
            catalog=_catalog(),
        )


def test_binding_does_not_create_or_require_composition_join_keys() -> None:
    parsed = parse_route_draft(
        _route(
            [
                _draft("graph_base", "graph"),
                _draft(
                    "sql_next",
                    "sql",
                    depends_on=["graph_base"],
                    bindings=[
                        {
                            "target": "names",
                            "sourceSubqueryId": "graph_base",
                            "sourceOutput": "componentName",
                        }
                    ],
                ),
            ]
        ),
        "question",
        catalog=_catalog(),
    )

    assert parsed["subqueries"][0]["joinKeys"] == []
    assert parsed["subqueries"][1]["joinKeys"] == []


@pytest.mark.parametrize(
    ("subqueries", "message"),
    [
        (
            [_draft("sql_one", "sql"), _draft("sql_two", "sql")],
            "one subquery per source",
        ),
        (
            [
                _draft(
                    "sql_one",
                    "sql",
                    depends_on=["graph_two"],
                    bindings=[
                        {
                            "target": "values",
                            "sourceSubqueryId": "graph_two",
                            "sourceOutput": "productId",
                        }
                    ],
                ),
                _draft(
                    "graph_two",
                    "graph",
                    depends_on=["sql_one"],
                    bindings=[
                        {
                            "target": "values",
                            "sourceSubqueryId": "sql_one",
                            "sourceOutput": "productId",
                        }
                    ],
                ),
            ],
            "cyclic",
        ),
    ],
)
def test_capability_boundary_rejects_duplicate_source_and_cycles(
    subqueries: list[dict], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_route_draft(
            _route(subqueries),
            "question",
            catalog=_catalog(),
        )


def test_hybrid_join_keys_are_shared_identity_and_ordered_equally() -> None:
    with pytest.raises(ValueError, match="shared identities"):
        parse_route_draft(
            _route(
                [
                    _draft("graph_base", "graph", join_keys=["categoryId"]),
                    _draft("sql_next", "sql", join_keys=["categoryId"]),
                ]
            ),
            "question",
            catalog=_catalog(),
            shared_join_aliases=_catalog().shared_join_aliases,
        )

    with pytest.raises(ValueError, match="same ordered aliases"):
        parse_route_draft(
            _route(
                [
                    _draft(
                        "graph_base",
                        "graph",
                        join_keys=["componentId", "productId"],
                    ),
                    _draft(
                        "sql_next",
                        "sql",
                        join_keys=["productId", "componentId"],
                    ),
                ]
            ),
            "question",
            catalog=_catalog(),
            shared_join_aliases=_catalog().shared_join_aliases,
        )


def _shortage_route(production_qty: int | float = 10) -> str:
    return _route(
        [
            _draft("graph_bom", "graph", join_keys=["componentId"]),
            _draft(
                "sql_stock",
                "sql",
                depends_on=["graph_bom"],
                join_keys=["componentId"],
                bindings=[
                    {
                        "target": "componentIds",
                        "sourceSubqueryId": "graph_bom",
                        "sourceOutput": "componentId",
                    }
                ],
            ),
        ],
        {"type": "bom_shortage_v1", "productionQty": production_qty},
    )


def test_bom_shortage_route_and_execution_contract_come_from_catalog() -> None:
    parsed = parse_route_draft(
        _shortage_route(),
        "완제품을 10개 생산",
        catalog=_catalog(),
        shared_join_aliases=_catalog().shared_join_aliases,
    )
    spec = _catalog().transform("bom_shortage_v1")
    execution = []
    for item in parsed["subqueries"]:
        tool = cast(ToolName, item["tool"])
        execution.append(
            {
                **item,
                "requiredOutputs": list(spec.required_outputs[tool]),
            }
        )
    validated = validate_subqueries(execution)

    assert validate_result_transform(
        parsed["resultTransform"], validated, catalog=_catalog()
    ) == {"type": "bom_shortage_v1", "productionQty": 10}
    assert spec.output_scale == 6


def test_bom_shortage_quantity_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="does not match"):
        parse_route_draft(
            _shortage_route(12),
            "완제품을 10개 생산",
            catalog=_catalog(),
            shared_join_aliases=_catalog().shared_join_aliases,
        )


def test_legacy_array_tool_plan_and_silent_join_recovery_are_rejected() -> None:
    with pytest.raises(ValueError, match="only subqueries"):
        parse_route_draft('["sql"]', "question", catalog=_catalog())

    raw = json.loads(_shortage_route())
    raw["subqueries"][0]["joinKeys"] = []
    raw["subqueries"][1]["joinKeys"] = []
    with pytest.raises(ValueError, match="join key"):
        parse_route_draft(
            json.dumps(raw),
            "완제품을 10개 생산",
            catalog=_catalog(),
        )


def test_route_and_execution_parsers_reject_unknown_fields() -> None:
    route = _draft("sql_query", "sql")
    route["requiredOutputs"] = ["productId"]
    with pytest.raises(ValueError, match="route boundary fields"):
        parse_route_draft(_route([route]), "question", catalog=_catalog())

    execution = {
        **_draft("sql_query", "sql"),
        "requiredOutputs": ["productId"],
        "unexpected": True,
    }
    execution.pop("inputBindings")
    with pytest.raises(ValueError, match="unknown execution fields"):
        validate_subqueries([execution])


def test_derive_tool_plan_is_a_pure_dag_projection() -> None:
    execution: list[Subquery] = [
        {
            "id": "graph_first",
            "tool": "graph",
            "question": "graph",
            "dependsOn": [],
            "requiredOutputs": ["productId"],
            "joinKeys": [],
        },
        {
            "id": "sql_second",
            "tool": "sql",
            "question": "sql",
            "dependsOn": ["graph_first"],
            "requiredOutputs": ["productId"],
            "joinKeys": [],
            "inputBindings": {"ids": "graph_first.productId"},
        },
    ]

    assert derive_tool_plan(execution) == ["graph", "sql"]
