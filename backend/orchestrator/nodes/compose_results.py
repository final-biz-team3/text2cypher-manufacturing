"""source 실행 결과를 최종 데이터 계약으로 조합하는 노드를 만든다."""

import os
from collections.abc import Callable
from typing import Any

from orchestrator.composition import compose_results
from orchestrator.state import OrchestratorState


def make_compose_results_node(
    *, row_limit: int | None = None
) -> Callable[[OrchestratorState], Any]:
    """SQL_ROW_LIMIT을 공유하면서 순수 조합기를 LangGraph 노드로 감싼다."""
    configured_limit = (
        int(os.getenv("SQL_ROW_LIMIT", "200")) if row_limit is None else row_limit
    )

    async def compose_results_node(state: OrchestratorState) -> dict[str, Any]:
        composed = compose_results(
            state.get("subqueries", []),
            {
                "sql": state.get("sql_result"),
                "graph": state.get("graph_result"),
            },
            row_limit=configured_limit,
            result_transform=state.get("resultTransform"),
        )
        return {"composed_result": composed}

    return compose_results_node
