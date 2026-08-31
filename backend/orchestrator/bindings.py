"""Dependency result rows to downstream input binding conversion."""

from typing import Any


def collect_input_bindings(
    bindings: dict[str, str],
    upstream_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, list[Any]]:
    """Collect each binding field once while preserving first-seen row order."""
    result: dict[str, list[Any]] = {}
    for input_name, source in bindings.items():
        dependency_id, field = source.split(".", 1)
        values: list[Any] = []
        for row in upstream_rows[dependency_id]:
            value = row[field]
            if value not in values:
                values.append(value)
        result[input_name] = values
    return result
