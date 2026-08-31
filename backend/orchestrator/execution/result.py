"""Result contract shared by production SQL and Cypher executors."""

from typing import Any, TypedDict


class QueryResultBatch(TypedDict):
    rows: list[dict[str, Any]]
    truncated: bool
