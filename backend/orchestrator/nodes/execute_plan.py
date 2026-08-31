"""검증된 하위 질의를 의존성 wave 순서로 실행한다."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from orchestrator.bindings import collect_input_bindings
from orchestrator.planning import Subquery
from orchestrator.state import OrchestratorState
from orchestrator.subgraphs.retry_agent import INCONCLUSIVE, NO_DATA

logger = logging.getLogger(__name__)

_TOOL_OUTPUT_FIELDS = {
    "sql": ("sql_query", "sql_result"),
    "graph": ("cypher_query", "graph_result"),
}


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """재시도 SubGraph 결과에서 외부의 기존 결과 필드만 보존한다."""
    return {
        "result": result["result"],
        "error": result["error"],
        "attempts": result.get("attempts", []),
        "empty_reason": result.get("empty_reason"),
        "truncated": result.get("truncated", False),
    }


def _dependency_failure(dependency_ids: list[str]) -> dict[str, Any]:
    names = ", ".join(dependency_ids)
    return {
        "result": None,
        "error": f"선행 하위 질의가 실패하여 실행하지 않았습니다: {names}",
        "attempts": [],
        "empty_reason": None,
        "truncated": False,
    }


def _dependency_empty(empty_reasons: list[str | None]) -> dict[str, Any]:
    empty_reason = INCONCLUSIVE if INCONCLUSIVE in empty_reasons else NO_DATA
    return {
        "result": [],
        "error": None,
        "attempts": [],
        "empty_reason": empty_reason,
        "truncated": False,
    }


def _input_binding_failure() -> dict[str, Any]:
    return {
        "result": None,
        "error": "하위 질의 입력 계획이 유효하지 않아 실행하지 않았습니다.",
        "attempts": [],
        "empty_reason": None,
        "truncated": False,
    }


def _extract_input_bindings(
    subquery: Subquery, outcomes: dict[str, dict[str, Any]]
) -> dict[str, list[Any]]:
    """선행 결과 행의 순서와 중복을 그대로 binding 배열에 투영한다."""
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

            try:
                input_bindings = _extract_input_bindings(subquery, outcomes)
            except ValueError as exc:
                logger.error(
                    "input binding 검증 실패: subquery_id=%r error=%s",
                    subquery["id"],
                    exc,
                )
                return query_field, result_field, None, _input_binding_failure()
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
                if query_text is not None:
                    output[query_field] = query_text
                output[result_field] = summary
            ready_ids = {subquery["id"] for subquery in ready}
            pending = [
                subquery for subquery in pending if subquery["id"] not in ready_ids
            ]

        return output

    return execute_plan
