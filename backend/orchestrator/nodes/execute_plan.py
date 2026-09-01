"""검증된 하위 질의를 의존성 wave 순서로 실행한다."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from orchestrator.bindings import collect_input_bindings
from orchestrator.planning import Subquery
from orchestrator.query_failures import make_query_failure
from orchestrator.state import OrchestratorState
from orchestrator.subgraphs.retry_agent import INCONCLUSIVE, NO_DATA

_TOOL_OUTPUT_FIELDS = {
    "sql": ("sql_query", "sql_result"),
    "graph": ("cypher_query", "graph_result"),
}

_FAILURE_PRIORITY = {
    "infrastructure": 3,
    "internal": 2,
    "user_correctable": 1,
}


def _prefer_failure(
    current: dict[str, Any] | None, candidate: dict[str, Any]
) -> dict[str, Any]:
    """가장 심각한 실제 실패를 보존하고 종속 실패는 최후순위로 둔다."""
    if current is None:
        return candidate
    current_kind = current.get("kind")
    candidate_kind = candidate.get("kind")
    current_rank = (
        _FAILURE_PRIORITY.get(current_kind, 0) if isinstance(current_kind, str) else 0,
        not current.get("dependent_failure", False),
    )
    candidate_rank = (
        (
            _FAILURE_PRIORITY.get(candidate_kind, 0)
            if isinstance(candidate_kind, str)
            else 0
        ),
        not candidate.get("dependent_failure", False),
    )
    return candidate if candidate_rank > current_rank else current


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """재시도 SubGraph 결과에서 외부의 기존 결과 필드만 보존한다."""
    return {
        "result": result["result"],
        "error": result["error"],
        "attempts": result.get("attempts", []),
        "empty_reason": result.get("empty_reason"),
        "truncated": result.get("truncated", False),
        "failure": result.get("failure"),
    }


def _dependency_failure(dependency_ids: list[str]) -> dict[str, Any]:
    names = ", ".join(dependency_ids)
    return {
        "result": None,
        "error": f"선행 하위 질의가 실패하여 실행하지 않았습니다: {names}",
        "attempts": [],
        "empty_reason": None,
        "truncated": False,
        "failure": make_query_failure(
            code="DEPENDENCY_FAILED",
            stage="dependency",
            category="DEPENDENCY_FAILED",
            kind="user_correctable",
            retryable=False,
            user_safe_reason="질의의 선행 조회가 완료되지 않아 후속 조회를 진행하지 못했습니다.",
            suggested_action="조회 대상과 조건을 더 구체적으로 지정해 다시 질문해 주세요.",
            dependent_failure=True,
        ),
    }


def _dependency_empty(empty_reasons: list[str | None]) -> dict[str, Any]:
    empty_reason = INCONCLUSIVE if INCONCLUSIVE in empty_reasons else NO_DATA
    return {
        "result": [],
        "error": None,
        "attempts": [],
        "empty_reason": empty_reason,
        "truncated": False,
        "failure": None,
    }


def _extract_input_bindings(
    subquery: Subquery, outcomes: dict[str, dict[str, Any]]
) -> dict[str, list[Any]]:
    """선행 결과에서 최초 등장 순서로 고유 binding 배열을 만든다."""
    return collect_input_bindings(
        subquery.get("inputBindings", {}),
        {
            dependency_id: outcome["result"]
            for dependency_id, outcome in outcomes.items()
        },
    )


def _initial_state(
    *,
    subquery: Subquery,
    entity: dict | list[dict] | None,
    schema_text: str,
    input_bindings: dict[str, list[Any]],
) -> dict[str, Any]:
    return {
        "query": subquery["question"],
        "entity": entity,
        "schema": schema_text,
        "required_outputs": subquery["requiredOutputs"],
        "input_bindings": input_bindings,
        "messages": [],
        "result": None,
        "error": None,
        "attempt_count": 0,
        "attempts": [],
        "empty_retried": False,
        "empty_reason": None,
        "truncated": False,
        "failure": None,
    }


def make_execute_plan_node(
    *,
    sql_agent: Any,
    cypher_agent: Any,
    sql_schema_text: str,
    cypher_schema_text: str,
) -> Callable[[OrchestratorState], Awaitable[dict[str, Any]]]:
    """계획 실행 결과를 기존 SQL·GRAPH 필드에 일대일로 대응한다."""
    agents = {"sql": sql_agent, "graph": cypher_agent}
    schemas = {"sql": sql_schema_text, "graph": cypher_schema_text}

    async def execute_plan(state: OrchestratorState) -> dict[str, Any]:
        output: dict[str, Any] = {
            "sql_query": None,
            "sql_result": None,
            "cypher_query": None,
            "graph_result": None,
            "query_failure": None,
        }
        outcomes: dict[str, dict[str, Any]] = {}

        async def execute_subquery(
            subquery: Subquery,
        ) -> tuple[str, str, str | None, dict[str, Any]]:
            dependency_ids = subquery["dependsOn"]
            failed_dependencies = [
                dependency_id
                for dependency_id in dependency_ids
                if outcomes[dependency_id]["error"] is not None
                or outcomes[dependency_id]["result"] is None
            ]
            empty_dependencies = [
                dependency_id
                for dependency_id in dependency_ids
                if outcomes[dependency_id]["result"] == []
            ]
            query_field, result_field = _TOOL_OUTPUT_FIELDS[subquery["tool"]]

            if failed_dependencies:
                summary = _dependency_failure(failed_dependencies)
                return query_field, result_field, None, summary
            if empty_dependencies:
                summary = _dependency_empty(
                    [
                        outcomes[dependency_id].get("empty_reason")
                        for dependency_id in empty_dependencies
                    ]
                )
                return query_field, result_field, None, summary

            input_bindings = _extract_input_bindings(subquery, outcomes)
            result = await agents[subquery["tool"]].ainvoke(
                _initial_state(
                    subquery=subquery,
                    entity=None if input_bindings else state.get("entity"),
                    schema_text=schemas[subquery["tool"]],
                    input_bindings=input_bindings,
                )
            )
            summary = _result_summary(result)
            return query_field, result_field, result["messages"][-1]["content"], summary

        pending = list(state.get("subqueries", []))
        while pending:
            ready = [
                subquery
                for subquery in pending
                if all(
                    dependency_id in outcomes for dependency_id in subquery["dependsOn"]
                )
            ]
            if not ready:  # validate_subqueries가 순환을 차단하지만 fail-closed로 둔다.
                raise ValueError(
                    "실행 가능한 subquery가 없어 의존 계획을 진행할 수 없습니다."
                )

            results = await asyncio.gather(
                *(execute_subquery(subquery) for subquery in ready)
            )
            for subquery, (query_field, result_field, query_text, summary) in zip(
                ready, results, strict=True
            ):
                outcomes[subquery["id"]] = summary
                failure = summary.get("failure")
                if isinstance(failure, dict):
                    output["query_failure"] = _prefer_failure(
                        output["query_failure"], failure
                    )
                if query_text is not None:
                    output[query_field] = query_text
                output[result_field] = summary
            ready_ids = {subquery["id"] for subquery in ready}
            pending = [
                subquery for subquery in pending if subquery["id"] not in ready_ids
            ]

        return output

    return execute_plan
