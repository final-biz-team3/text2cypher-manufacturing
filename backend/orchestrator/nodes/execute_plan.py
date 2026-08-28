"""검증된 하위 질의 계획을 순서대로 실행한다."""

from collections.abc import Awaitable, Callable
from typing import Any

from orchestrator.planning import Subquery
from orchestrator.state import OrchestratorState
from orchestrator.subgraphs.retry_agent import INCONCLUSIVE, NO_DATA

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
    }


def _dependency_failure(dependency_ids: list[str]) -> dict[str, Any]:
    names = ", ".join(dependency_ids)
    return {
        "result": None,
        "error": f"선행 하위 질의가 실패하여 실행하지 않았습니다: {names}",
        "attempts": [],
        "empty_reason": None,
    }


def _dependency_empty(empty_reasons: list[str | None]) -> dict[str, Any]:
    empty_reason = INCONCLUSIVE if INCONCLUSIVE in empty_reasons else NO_DATA
    return {
        "result": [],
        "error": None,
        "attempts": [],
        "empty_reason": empty_reason,
    }


def _extract_input_bindings(
    subquery: Subquery, outcomes: dict[str, dict[str, Any]]
) -> dict[str, list[Any]]:
    """선행 결과의 행 순서와 중복을 보존해 binding 배열을 만든다."""
    bindings: dict[str, list[Any]] = {}
    for binding_name, source in subquery.get("inputBindings", {}).items():
        dependency_id, output_alias = source.split(".", 1)
        rows = outcomes[dependency_id]["result"]
        bindings[binding_name] = [row[output_alias] for row in rows]
    return bindings


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

        for subquery in state.get("subqueries", []):
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
                outcomes[subquery["id"]] = summary
                output[result_field] = summary
                continue
            if empty_dependencies:
                summary = _dependency_empty(
                    [
                        outcomes[dependency_id].get("empty_reason")
                        for dependency_id in empty_dependencies
                    ]
                )
                outcomes[subquery["id"]] = summary
                output[result_field] = summary
                continue

            input_bindings = _extract_input_bindings(subquery, outcomes)
            result = await agents[subquery["tool"]].ainvoke(
                _initial_state(
                    subquery=subquery,
                    entity=state.get("entity"),
                    schema_text=schemas[subquery["tool"]],
                    input_bindings=input_bindings,
                )
            )
            summary = _result_summary(result)
            outcomes[subquery["id"]] = summary
            output[query_field] = result["messages"][-1]["content"]
            output[result_field] = summary

        return output

    return execute_plan
