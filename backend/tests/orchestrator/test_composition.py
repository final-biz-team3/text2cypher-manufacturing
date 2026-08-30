"""실행 결과의 단일·결합·분리 조합 계약을 테스트한다."""

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from orchestrator.composition import compose_results
from orchestrator.nodes.compose_results import make_compose_results_node
from orchestrator.planning import (
    BOM_SHORTAGE_GRAPH_OUTPUTS,
    BOM_SHORTAGE_SQL_OUTPUTS,
    BomShortageTransform,
    Subquery,
)
from orchestrator.state import OrchestratorState


def _step(
    subquery_id: str,
    tool: str,
    *,
    join_keys: list[str] | None = None,
) -> Subquery:
    keys = join_keys or []
    return {
        "id": subquery_id,
        "tool": tool,
        "question": "질문",
        "dependsOn": [],
        "requiredOutputs": keys,
        "joinKeys": keys,
    }


def _source(
    rows: list[Any] | None,
    *,
    error: str | None = None,
    empty_reason: Any = None,
) -> dict[str, Any]:
    return {
        "result": rows,
        "error": error,
        "attempts": [],
        "empty_reason": empty_reason,
    }


def _joined(
    left_rows: list[Any] | None,
    right_rows: list[Any] | None,
    *,
    left_keys: list[str] | None = None,
    right_keys: list[str] | None = None,
    row_limit: int = 200,
    left_error: str | None = None,
    right_error: str | None = None,
    left_empty_reason: str | None = None,
    right_empty_reason: str | None = None,
):
    canonical_keys = left_keys or ["id"]
    return compose_results(
        [
            _step("graph_base", "graph", join_keys=canonical_keys),
            _step(
                "sql_followup",
                "sql",
                join_keys=right_keys or canonical_keys,
            ),
        ],
        {
            "graph": _source(
                left_rows,
                error=left_error,
                empty_reason=left_empty_reason,
            ),
            "sql": _source(
                right_rows,
                error=right_error,
                empty_reason=right_empty_reason,
            ),
        },
        row_limit=row_limit,
    )


@pytest.mark.parametrize("tool", ["sql", "graph"])
def test_single_result_is_normalized_without_changing_rows(tool: str) -> None:
    rows = [{"value": 1}, {"value": 1}]

    result = compose_results(
        [_step("only", tool, join_keys=["value"])],
        {tool: _source(rows)},
        row_limit=200,
    )

    assert result == {
        "mode": "single",
        "rows": rows,
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 2,
        "truncated": False,
    }


@pytest.mark.parametrize("tool", ["sql", "graph"])
def test_single_result_rejects_non_dict_row(tool: str) -> None:
    result = compose_results(
        [_step("only", tool)],
        {tool: _source(["not-a-dict"])},
        row_limit=200,
    )

    assert result["mode"] == "single"
    assert result["rows"] == []
    assert "0번 행이 객체가 아닙니다" in str(result["error"])


def test_legacy_hybrid_is_kept_in_plan_order_without_cartesian_product() -> None:
    result = compose_results(
        [_step("sql_facts", "sql"), _step("graph_paths", "graph")],
        {
            "sql": _source([{"fact": 1}]),
            "graph": _source([], empty_reason="NO_DATA"),
        },
        row_limit=200,
    )

    assert result["mode"] == "separate"
    assert result["rows"] == []
    assert list(result["sections"]) == ["sql_facts", "graph_paths"]
    assert result["sections"] == {
        "sql_facts": {
            "tool": "sql",
            "rows": [{"fact": 1}],
            "empty_reason": None,
        },
        "graph_paths": {
            "tool": "graph",
            "rows": [],
            "empty_reason": "NO_DATA",
        },
    }
    assert result["empty_reason"] is None
    assert result["total_count"] == 1


def test_separate_result_rejects_non_dict_row_without_partial_sections() -> None:
    result = compose_results(
        [_step("sql_facts", "sql"), _step("graph_paths", "graph")],
        {
            "sql": _source([{"fact": 1}]),
            "graph": _source(["not-a-dict"]),
        },
        row_limit=200,
    )

    assert result["mode"] == "separate"
    assert result["rows"] == []
    assert result["sections"] == {}
    assert "graph_paths(graph)의 0번 행이 객체가 아닙니다" in str(result["error"])


def test_join_preserves_left_duplicates_for_many_to_one() -> None:
    result = _joined(
        [{"id": 7, "path": "a"}, {"id": 7, "path": "b"}],
        [{"id": 7, "stock": 30}],
    )

    assert result["rows"] == [
        {"id": 7, "path": "a", "stock": 30},
        {"id": 7, "path": "b", "stock": 30},
    ]


def test_join_preserves_right_order_for_one_to_many() -> None:
    result = _joined(
        [{"id": 7, "fact": "base"}],
        [
            {"id": 7, "sequence": 20},
            {"id": 7, "sequence": 10},
        ],
    )

    assert [row["sequence"] for row in result["rows"]] == [20, 10]


def test_join_uses_first_subquery_key_order_and_multiple_keys() -> None:
    result = _joined(
        [
            {"componentId": 7, "supplierId": 2, "path": "a"},
            {"componentId": 8, "supplierId": 2, "path": "b"},
        ],
        [
            {"supplierId": 2, "componentId": 8, "stock": 80},
            {"supplierId": 2, "componentId": 7, "stock": 70},
        ],
        left_keys=["componentId", "supplierId"],
        right_keys=["supplierId", "componentId"],
    )

    assert result["rows"] == [
        {
            "componentId": 7,
            "supplierId": 2,
            "path": "a",
            "stock": 70,
        },
        {
            "componentId": 8,
            "supplierId": 2,
            "path": "b",
            "stock": 80,
        },
    ]


def test_join_excludes_left_rows_without_a_right_match() -> None:
    result = _joined(
        [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}],
        [{"id": 2, "stock": 20}],
    )

    assert result["rows"] == [{"id": 2, "name": "two", "stock": 20}]


def test_join_rejects_followup_key_outside_left_binding_domain() -> None:
    result = _joined(
        [{"id": 1}],
        [{"id": 1, "stock": 10}, {"id": 999, "stock": 20}],
        row_limit=1,
    )

    assert result["rows"] == []
    assert "바인딩 범위를 벗어났습니다" in str(result["error"])


def test_join_rejects_invalid_key_in_right_rows() -> None:
    result = _joined([{"id": 1}], [{"id": None}])

    assert result["rows"] == []
    assert "sql_followup(sql) 0번 행" in str(result["error"])
    assert "값이 None입니다" in str(result["error"])


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"id": None}], "None"),
        ([{"id": [1, 2]}], "hashable"),
        ([{"id": {"nested": 1}}], "hashable"),
        ([{"other": 1}], "join key 'id'가 없습니다"),
        (["not-a-dict"], "객체가 아닙니다"),
    ],
)
def test_join_rejects_invalid_key_rows(rows: list[Any], message: str) -> None:
    result = _joined(rows, [{"id": 1}])

    assert result["rows"] == []
    assert message in str(result["error"])


def test_join_keeps_equal_overlapping_non_key_field_once() -> None:
    result = _joined(
        [{"id": 1, "name": "same"}],
        [{"id": 1, "name": "same", "stock": 10}],
    )

    assert result["rows"] == [{"id": 1, "name": "same", "stock": 10}]


def test_join_rejects_conflicting_field_even_after_row_limit() -> None:
    result = _joined(
        [{"id": 1, "name": "left"}],
        [
            {"id": 1, "name": "left", "stock": 10},
            {"id": 1, "name": "right", "stock": 20},
        ],
        row_limit=1,
    )

    assert result["rows"] == []
    assert "필드 'name'의 값이 충돌" in str(result["error"])


def test_join_source_error_fails_without_mutating_original_results() -> None:
    left = _source([{"id": 1}])
    right = _source(None, error="database down")
    originals = deepcopy((left, right))

    result = compose_results(
        [
            _step("graph_base", "graph", join_keys=["id"]),
            _step("sql_followup", "sql", join_keys=["id"]),
        ],
        {"graph": left, "sql": right},
        row_limit=200,
    )

    assert result["error"] is not None
    assert result["rows"] == []
    assert (left, right) == originals


def test_source_result_none_without_error_is_composition_failure() -> None:
    result = compose_results(
        [_step("only", "sql")],
        {"sql": _source(None)},
        row_limit=200,
    )

    assert result["mode"] == "single"
    assert result["rows"] == []
    assert "result가 None입니다" in str(result["error"])


def test_separate_source_error_fails_without_partial_sections() -> None:
    sql_source = _source([{"fact": 1}])
    graph_source = _source(None, error="graph failed")
    originals = deepcopy((sql_source, graph_source))

    result = compose_results(
        [_step("sql_facts", "sql"), _step("graph_paths", "graph")],
        {"sql": sql_source, "graph": graph_source},
        row_limit=200,
    )

    assert result["mode"] == "separate"
    assert result["rows"] == []
    assert result["sections"] == {}
    assert "graph failed" in str(result["error"])
    assert (sql_source, graph_source) == originals


@pytest.mark.parametrize("empty_reason", [[], {}, "UNKNOWN", 1])
def test_source_rejects_unsupported_empty_reason(empty_reason: Any) -> None:
    result = compose_results(
        [_step("only", "sql")],
        {"sql": _source([], empty_reason=empty_reason)},
        row_limit=200,
    )

    assert result["mode"] == "single"
    assert result["rows"] == []
    assert "empty_reason이 지원되지 않습니다" in str(result["error"])


@pytest.mark.parametrize(
    ("left_reason", "right_reason", "expected"),
    [
        ("NO_DATA", "NO_DATA", "NO_DATA"),
        ("INCONCLUSIVE", "NO_DATA", "INCONCLUSIVE"),
        ("NO_DATA", "INCONCLUSIVE", "INCONCLUSIVE"),
    ],
)
def test_join_empty_result_reason_priority(
    left_reason: str, right_reason: str, expected: str
) -> None:
    result = _joined(
        [],
        [],
        left_empty_reason=left_reason,
        right_empty_reason=right_reason,
    )

    assert result["empty_reason"] == expected
    assert result["total_count"] == 0
    assert result["truncated"] is False


@pytest.mark.parametrize(
    ("left_reason", "right_reason", "expected_reason"),
    [
        (None, "NO_DATA", "NO_DATA"),
        (None, "INCONCLUSIVE", "INCONCLUSIVE"),
    ],
)
def test_join_empty_right_source_uses_empty_result_contract(
    left_reason: str | None,
    right_reason: str | None,
    expected_reason: str,
) -> None:
    result = _joined(
        [{"id": 1}],
        [],
        left_empty_reason=left_reason,
        right_empty_reason=right_reason,
    )

    assert result["mode"] == "joined"
    assert result["rows"] == []
    assert result["error"] is None
    assert result["empty_reason"] == expected_reason


def test_join_rejects_nonempty_right_when_left_binding_domain_is_empty() -> None:
    result = _joined(
        [],
        [{"id": 999}],
        left_empty_reason="INCONCLUSIVE",
    )

    assert result["rows"] == []
    assert result["empty_reason"] is None
    assert "바인딩 범위를 벗어났습니다" in str(result["error"])


@pytest.mark.parametrize(
    ("left_reason", "right_reason"),
    [
        ("NO_DATA", None),
        ("INCONCLUSIVE", None),
        (None, "NO_DATA"),
        (None, "INCONCLUSIVE"),
    ],
)
def test_source_rejects_empty_reason_for_nonempty_rows(
    left_reason: str | None,
    right_reason: str | None,
) -> None:
    result = _joined(
        [{"id": 1}],
        [{"id": 1}],
        left_empty_reason=left_reason,
        right_empty_reason=right_reason,
    )

    assert result["rows"] == []
    assert result["empty_reason"] is None
    assert "result가 비어 있지 않아 empty_reason을 지정할 수 없습니다" in str(
        result["error"]
    )


def test_separate_empty_sections_use_inconclusive_priority() -> None:
    result = compose_results(
        [_step("sql_facts", "sql"), _step("graph_paths", "graph")],
        {
            "sql": _source([], empty_reason="NO_DATA"),
            "graph": _source([], empty_reason="INCONCLUSIVE"),
        },
        row_limit=200,
    )

    assert result["empty_reason"] == "INCONCLUSIVE"
    assert result["sections"]["sql_facts"]["empty_reason"] == "NO_DATA"
    assert result["sections"]["graph_paths"]["empty_reason"] == "INCONCLUSIVE"


def test_join_counts_all_rows_and_truncates_only_stored_rows() -> None:
    result = _joined(
        [{"id": 1, "path": "a"}, {"id": 1, "path": "b"}],
        [
            {"id": 1, "operation": 10},
            {"id": 1, "operation": 20},
        ],
        row_limit=3,
    )

    assert result["rows"] == [
        {"id": 1, "path": "a", "operation": 10},
        {"id": 1, "path": "a", "operation": 20},
        {"id": 1, "path": "b", "operation": 10},
    ]
    assert result["total_count"] == 4
    assert result["truncated"] is True


def test_negative_row_limit_preserves_resolved_mode() -> None:
    single = compose_results(
        [_step("only", "sql")],
        {"sql": _source([{"value": 1}])},
        row_limit=-1,
    )
    separate = compose_results(
        [_step("sql_facts", "sql"), _step("graph_paths", "graph")],
        {
            "sql": _source([{"fact": 1}]),
            "graph": _source([{"path": 1}]),
        },
        row_limit=-1,
    )
    joined = _joined([{"id": 1}], [{"id": 1}], row_limit=-1)

    assert single["mode"] == "single"
    assert separate["mode"] == "separate"
    assert joined["mode"] == "joined"
    assert all(
        result["error"] == "결과 행 상한은 0 이상이어야 합니다."
        for result in (single, separate, joined)
    )


async def test_compose_results_node_uses_injected_row_limit() -> None:
    state: OrchestratorState = {
        "query": "질의",
        "subqueries": [
            _step("graph_base", "graph", join_keys=["id"]),
            _step("sql_followup", "sql", join_keys=["id"]),
        ],
        "graph_result": _source([{"id": 1, "path": "a"}, {"id": 1, "path": "b"}]),
        "sql_result": _source([{"id": 1, "stock": 10}, {"id": 1, "stock": 20}]),
    }

    result = await make_compose_results_node(row_limit=1)(state)
    composed = result["composed_result"]

    assert composed["rows"] == [{"id": 1, "path": "a", "stock": 10}]
    assert composed["total_count"] == 4
    assert composed["truncated"] is True


def _shortage_steps() -> list[Subquery]:
    return [
        {
            "id": "graph_bom",
            "tool": "graph",
            "question": "BOM 경로",
            "dependsOn": [],
            "requiredOutputs": sorted(BOM_SHORTAGE_GRAPH_OUTPUTS),
            "joinKeys": ["componentId"],
        },
        {
            "id": "sql_stock",
            "tool": "sql",
            "question": "재고",
            "dependsOn": ["graph_bom"],
            "requiredOutputs": sorted(BOM_SHORTAGE_SQL_OUTPUTS),
            "joinKeys": ["componentId"],
            "inputBindings": {"componentIds": "graph_bom.componentId"},
        },
    ]


def _graph_shortage_row(
    component_id: int,
    component_name: str,
    path: list[int],
    quantities: list[int | float],
    *,
    supplier_id: int | None = None,
    supplier_name: str | None = None,
) -> dict[str, Any]:
    return {
        "finishedProductId": 1,
        "finishedProductName": "Finished",
        "componentId": component_id,
        "componentName": component_name,
        "depth": len(quantities),
        "pathProductIds": path,
        "quantityPerAssembly": quantities,
        "supplierId": supplier_id,
        "supplierName": supplier_name,
    }


def _shortage_transform() -> BomShortageTransform:
    return {"type": "bom_shortage_v1", "productionQty": 10}


def test_bom_shortage_deduplicates_supplier_paths_and_sums_distinct_paths() -> None:
    first_path_supplier_two = _graph_shortage_row(
        10, "External", [1, 2, 10], [2, 3], supplier_id=2, supplier_name="Second"
    )
    graph_rows = [
        first_path_supplier_two,
        deepcopy(first_path_supplier_two),
        _graph_shortage_row(
            10,
            "External",
            [1, 2, 10],
            [2, 3],
            supplier_id=1,
            supplier_name="First",
        ),
        _graph_shortage_row(10, "External", [1, 3, 10], [1, 4]),
        _graph_shortage_row(20, "Enough", [1, 20], [5]),
        _graph_shortage_row(30, "Internal", [1, 30], [9]),
    ]
    sql_rows = [
        {"componentId": 10, "makeFlag": False, "actualStock": 30},
        {"componentId": 20, "makeFlag": False, "actualStock": 60},
        {"componentId": 30, "makeFlag": True, "actualStock": 0},
    ]
    originals = deepcopy((graph_rows, sql_rows))

    result = compose_results(
        _shortage_steps(),
        {"graph": _source(graph_rows), "sql": _source(sql_rows)},
        row_limit=200,
        result_transform=_shortage_transform(),
    )

    assert result == {
        "mode": "joined",
        "transform": "bom_shortage_v1",
        "rows": [
            {
                "finishedProductId": 1,
                "finishedProductName": "Finished",
                "productionQty": Decimal("10.000000"),
                "componentId": 10,
                "componentName": "External",
                "requiredQty": Decimal("100.000000"),
                "actualStock": Decimal("30.000000"),
                "shortageQty": Decimal("70.000000"),
                "suppliers": [
                    {"supplierId": 1, "supplierName": "First"},
                    {"supplierId": 2, "supplierName": "Second"},
                ],
            }
        ],
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 1,
        "truncated": False,
    }
    assert (graph_rows, sql_rows) == originals


def test_bom_shortage_validates_all_rows_before_applying_row_limit() -> None:
    graph_rows = [
        _graph_shortage_row(10, "First", [1, 10], [2]),
        _graph_shortage_row(20, "Broken", [1, 20], [3]),
    ]
    del graph_rows[1]["quantityPerAssembly"]
    sql_rows = [
        {"componentId": 10, "makeFlag": False, "actualStock": 0},
        {"componentId": 20, "makeFlag": False, "actualStock": 0},
    ]

    result = compose_results(
        _shortage_steps(),
        {"graph": _source(graph_rows), "sql": _source(sql_rows)},
        row_limit=1,
        result_transform=_shortage_transform(),
    )

    assert result["rows"] == []
    assert "quantityPerAssembly" in str(result["error"])


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda graph, sql: sql.append(dict(sql[0])), "행이 중복"),
        (lambda graph, sql: sql.clear(), "component domain"),
        (
            lambda graph, sql: graph[0].update({"supplierId": 1}),
            "함께 null",
        ),
        (
            lambda graph, sql: graph[0].update({"pathProductIds": [10, 1]}),
            "path 방향",
        ),
    ],
)
def test_bom_shortage_rejects_source_contract_conflicts(
    mutate: Any, message: str
) -> None:
    graph_rows = [_graph_shortage_row(10, "Part", [1, 10], [2])]
    sql_rows = [{"componentId": 10, "makeFlag": False, "actualStock": 0}]
    mutate(graph_rows, sql_rows)

    result = compose_results(
        _shortage_steps(),
        {"graph": _source(graph_rows), "sql": _source(sql_rows)},
        row_limit=200,
        result_transform=_shortage_transform(),
    )

    assert result["rows"] == []
    assert message in str(result["error"])


def test_bom_shortage_preserves_inconclusive_empty_source_contract() -> None:
    result = compose_results(
        _shortage_steps(),
        {
            "graph": _source([], empty_reason="NO_DATA"),
            "sql": _source([], empty_reason="INCONCLUSIVE"),
        },
        row_limit=200,
        result_transform=_shortage_transform(),
    )

    assert result["transform"] == "bom_shortage_v1"
    assert result["rows"] == []
    assert result["error"] is None
    assert result["empty_reason"] == "INCONCLUSIVE"
