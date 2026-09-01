"""Dependency result 행을 input binding으로 투영하는 계약을 테스트한다."""

from copy import deepcopy
from typing import Any

import pytest

from orchestrator.bindings import collect_input_bindings


def test_collect_input_bindings_preserves_duplicate_values() -> None:
    upstream = {
        "graph_components": [
            {"componentId": 10},
            {"componentId": 10},
            {"componentId": 20},
        ]
    }

    result = collect_input_bindings(
        {"componentIds": "graph_components.componentId"}, upstream
    )

    assert result == {"componentIds": [10, 10, 20]}


def test_collect_input_bindings_aligns_fields_from_same_dependency() -> None:
    upstream = {
        "graph_components": [
            {"componentId": 10, "quantity": 2},
            {"componentId": 10, "quantity": 3},
            {"componentId": 20, "quantity": 1},
        ]
    }

    result = collect_input_bindings(
        {
            "componentIds": "graph_components.componentId",
            "quantities": "graph_components.quantity",
        },
        upstream,
    )

    assert result == {
        "componentIds": [10, 10, 20],
        "quantities": [2, 3, 1],
    }


def test_collect_input_bindings_preserves_distinct_values_and_none() -> None:
    upstream = {
        "graph_components": [
            {"componentId": 10},
            {"componentId": None},
            {"componentId": 20},
            {"componentId": None},
        ]
    }

    result = collect_input_bindings(
        {"componentIds": "graph_components.componentId"}, upstream
    )

    assert result == {"componentIds": [10, None, 20, None]}


def test_collect_input_bindings_rejects_missing_source_field() -> None:
    upstream = {"graph_components": [{"componentId": 10}, {"name": "missing"}]}

    with pytest.raises(ValueError) as exc_info:
        collect_input_bindings(
            {"componentIds": "graph_components.componentId"}, upstream
        )

    message = str(exc_info.value)
    assert "graph_components" in message
    assert "1번 행" in message
    assert "componentId" in message


@pytest.mark.parametrize(
    ("upstream", "expected"),
    [
        ({}, "결과가 없습니다"),
        ({"graph_components": None}, "list여야 합니다"),
        ({"graph_components": {}}, "list여야 합니다"),
    ],
)
def test_collect_input_bindings_rejects_invalid_dependency_result(
    upstream: dict[str, Any], expected: str
) -> None:
    with pytest.raises(ValueError, match=expected):
        collect_input_bindings(
            {"componentIds": "graph_components.componentId"}, upstream
        )


def test_collect_input_bindings_does_not_mutate_upstream_rows() -> None:
    upstream = {
        "graph_components": [
            {"componentId": 10, "quantity": None},
            {"componentId": 10, "quantity": 3},
        ]
    }
    before = deepcopy(upstream)

    collect_input_bindings(
        {
            "componentIds": "graph_components.componentId",
            "quantities": "graph_components.quantity",
        },
        upstream,
    )

    assert upstream == before
