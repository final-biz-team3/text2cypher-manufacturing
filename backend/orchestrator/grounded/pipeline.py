"""Two bounded candidates under a common interpretation and validation boundary."""

import asyncio
import math
import os
from contextvars import ContextVar
from copy import deepcopy
from time import perf_counter
from typing import Any, cast

import neo4j.exceptions
import openai
import psycopg

from core.observability.events import emit_event
from orchestrator.errors import EntityAmbiguousError, QueryInfrastructureError
from orchestrator.grounded.answer import grounded_answer
from orchestrator.grounded.context import KnowledgeContext, load_knowledge
from orchestrator.grounded.execution import graph_candidate, sql_candidate
from orchestrator.grounded.interpret import interpret
from orchestrator.grounded.runtime import grounded_execution
from orchestrator.grounded.validation import validate_candidate
from orchestrator.guards.cypher_guard import make_cypher_guard
from orchestrator.guards.sql_guard import make_sql_guard
from orchestrator.nodes.compose_results import make_compose_results_node
from orchestrator.nodes.execute_plan import make_execute_plan_node
from orchestrator.nodes.plan_outputs import make_plan_outputs_node
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.query_failures import make_query_failure
from orchestrator.state import FailureKind
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph
from strict_query.pipeline import build_strict_query


def _failure(
    code: str,
    reason: str,
    *,
    clarification: dict[str, Any] | None = None,
    kind: FailureKind = "user_correctable",
) -> dict[str, Any]:
    failure = make_query_failure(
        code=code,
        stage="validation",
        category=code,
        kind=kind,
        retryable=False,
        user_safe_reason=reason,
        suggested_action="",
        failed_tool=None,
    )
    return {
        "query_failure": failure,
        "clarification": clarification,
        "final_answer": reason,
        "answer_metadata": {
            "mode": "fixed",
            "attemptCount": 0,
            "fallbackReason": None,
            "validationRejected": False,
        },
    }


def _positive_timeout(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def make_coordinator(
    client: Any,
    knowledge: KnowledgeContext,
    strict_run: Any,
    modern_run: Any,
    *,
    trace: ContextVar[dict[str, Any]] | None = None,
) -> Any:
    timeout = _positive_timeout("GROUNDED_QUERY_TIMEOUT_SECONDS", "50")
    candidate_timeout = _positive_timeout("GROUNDED_CANDIDATE_TIMEOUT_SECONDS", "20")

    async def process(state: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        query = state["query"]
        reports: list[dict[str, Any]] = []
        common: dict[str, Any] = {
            "validation_report": {
                "knowledge_sha256": knowledge.digest,
                "candidates": reports,
            }
        }
        try:
            async with asyncio.timeout(timeout):
                intent = await interpret(client, query, knowledge)
                common.update(
                    query_intent=intent.model_dump(),
                    output_definitions=[o.model_dump() for o in intent.outputs],
                )
                if intent.action in {"write", "mixed"}:
                    return {
                        **common,
                        **_failure(
                            "REQUEST_POLICY_BLOCKED",
                            "데이터를 변경하는 요청은 실행할 수 없습니다. 조회할 내용만 요청해 주세요.",
                        ),
                    }
                if intent.action == "unanswerable":
                    return {
                        **common,
                        **_failure(
                            "UNANSWERABLE",
                            "현재 제공된 데이터와 업무 정의로는 요청한 내용을 확인할 수 없습니다.",
                        ),
                    }
                if intent.action == "clarify":
                    assert intent.clarification is not None
                    return {
                        **common,
                        **_failure(
                            "CLARIFICATION_NEEDED",
                            intent.clarification.question,
                            clarification=intent.clarification.model_dump(),
                        ),
                    }
                for strategy, run in (("strict", strict_run), ("pr61", modern_run)):
                    # No failed entities, rows, queries or model-authored answers cross candidates.
                    inputs = {"query": query, "query_intent": intent.model_dump()}
                    if state.get("confirmed_entity") is not None:
                        inputs["confirmed_entity"] = deepcopy(state["confirmed_entity"])
                    if reports:
                        inputs["candidate_feedback"] = {
                            "deterministic": reports[-1].get("deterministic", []),
                            "semantic": reports[-1].get("semantic", []),
                            "error_type": reports[-1].get("error_type"),
                            "failure_code": reports[-1].get("failure_code"),
                        }
                    evidence: dict[str, Any] = {}
                    trace_token = trace.set(evidence) if trace else None
                    execution_token = grounded_execution.set(True)
                    try:
                        async with asyncio.timeout(candidate_timeout):
                            candidate = await run(inputs)
                            candidate["execution_evidence"] = evidence
                            failure = candidate.get("query_failure") or {}
                            if failure.get("kind") == "infrastructure":
                                reports.append(
                                    {
                                        "strategy": strategy,
                                        "accepted": False,
                                        "reviewed": False,
                                        "failure_kind": "infrastructure",
                                    }
                                )
                                return {
                                    **common,
                                    **_failure(
                                        "QUERY_INFRASTRUCTURE_UNAVAILABLE",
                                        "조회 시스템에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                                        kind="infrastructure",
                                    ),
                                }
                            report = await validate_candidate(
                                client, query, intent, candidate, knowledge
                            )
                    except Exception as exc:
                        candidate = {}
                        report = {
                            "accepted": False,
                            "error_type": type(exc).__name__,
                            "reviewed": False,
                        }
                        if isinstance(exc, EntityAmbiguousError) and (
                            strategy == "pr61"
                            or exc.lookup_name.casefold()
                            in {
                                e.text.casefold()
                                for e in intent.entities
                                if e.kind == "name"
                            }
                        ):
                            raise
                        if isinstance(
                            exc,
                            (
                                QueryInfrastructureError,
                                openai.APIError,
                                psycopg.OperationalError,
                                neo4j.exceptions.ServiceUnavailable,
                                neo4j.exceptions.SessionExpired,
                            ),
                        ):
                            raise
                    finally:
                        grounded_execution.reset(execution_token)
                        if trace is not None and trace_token is not None:
                            trace.reset(trace_token)
                    report["strategy"] = strategy
                    reports.append(report)
                    if report["accepted"]:
                        answer = await grounded_answer(client, intent, candidate)
                        allowed = {
                            k: v
                            for k, v in candidate.items()
                            if k
                            in {
                                "entity",
                                "tool_plan",
                                "routeDraft",
                                "rawRouteDraft",
                                "subqueries",
                                "resultTransform",
                                "sql_query",
                                "cypher_query",
                                "sql_result",
                                "graph_result",
                                "composed_result",
                                "execution_evidence",
                            }
                        }
                        common["validation_report"]["elapsed_ms"] = round(
                            (perf_counter() - started) * 1000, 3
                        )
                        return {
                            **allowed,
                            **common,
                            **answer,
                            "query_strategy": strategy,
                            "query_failure": None,
                        }
                    if report.get("clarification"):
                        clarification = report["clarification"]
                        return {
                            **common,
                            **_failure(
                                "CLARIFICATION_NEEDED",
                                clarification["question"],
                                clarification=clarification,
                            ),
                        }
        except EntityAmbiguousError:
            # Preserve the existing typed candidate/confirmed_entity protocol.
            raise
        except (
            QueryInfrastructureError,
            openai.APIError,
            psycopg.OperationalError,
            neo4j.exceptions.ServiceUnavailable,
            neo4j.exceptions.SessionExpired,
        ) as exc:
            common["validation_report"]["error_type"] = type(exc).__name__
            return {
                **common,
                **_failure(
                    "QUERY_INFRASTRUCTURE_UNAVAILABLE",
                    "조회 시스템에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                    kind="infrastructure",
                ),
            }
        except TimeoutError:
            return {
                **common,
                **_failure(
                    "QUERY_TIMEOUT",
                    "질의 처리 제한 시간에 도달했습니다. 잠시 후 다시 시도해 주세요.",
                    kind="infrastructure",
                ),
            }
        except Exception as exc:
            common["validation_report"]["error_type"] = type(exc).__name__
            return {
                **common,
                **_failure(
                    "INTERPRETATION_UNVERIFIED",
                    "질문 해석을 검증하지 못해 답을 확정할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                    kind="internal",
                ),
            }
        return {
            **common,
            **_failure(
                "RESULT_UNVERIFIED",
                "조회 결과가 질문의 모든 조건을 충족하는지 확인하지 못했습니다. 답을 확정하지 않겠습니다.",
            ),
        }

    async def observed(state: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        result = await process(state)
        report = result["validation_report"]
        report["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        emit_event(
            "grounded.query.completed",
            "pipeline",
            outcome="failure" if result.get("query_failure") else "success",
            query_strategy=result.get("query_strategy"),
            candidate_count=len(report["candidates"]),
            semantic_review_count=sum(
                bool(r.get("reviewed")) for r in report["candidates"]
            ),
            knowledge_sha256=knowledge.digest,
            duration_ms=report["elapsed_ms"],
        )
        return result

    return observed


def build_grounded_node(
    client: Any,
    pool: Any,
    *,
    sql_schema: Any,
    sql_schema_text: str,
    graph_schema: Any,
    graph_schema_text: str,
    catalog: Any,
) -> Any:
    knowledge = load_knowledge()
    trace: ContextVar[dict[str, Any]] = ContextVar("grounded_execution_trace")
    sql_guard, graph_guard = make_sql_guard(sql_schema), make_cypher_guard(graph_schema)

    async def execute_sql(query: str) -> Any:
        decision = sql_guard(query)
        if not decision.allowed:
            raise ValueError("SQL rejected by execution guard")
        return await sql_candidate(
            pool,
            query,
            row_limit=int(os.getenv("SQL_ROW_LIMIT", "200")),
            evidence=trace.get(),
        )

    async def execute_graph(query: str) -> Any:
        decision = graph_guard(query)
        if not decision.allowed:
            raise ValueError("Cypher rejected by execution guard")
        return await graph_candidate(query, evidence=trace.get())

    try:
        strict = build_strict_query(
            client, pool, execute_sql_fn=execute_sql, execute_cypher_fn=execute_graph
        )
    except Exception:

        async def strict(_state: Any) -> Any:
            raise RuntimeError("Strict pipeline unavailable")

    resolver = make_resolve_entity_node(client, pool, graph_schema)
    sql_agent = make_sql_agent_subgraph(
        client, execute_sql, sql_schema, semantic_context=catalog.describe("sql")
    )
    graph_agent = make_cypher_agent_subgraph(
        client,
        execute_cypher=execute_graph,
        query_policy=graph_schema.query_policy,
        graph_schema=graph_schema,
        semantic_context=catalog.describe("graph"),
        semantic_catalog=catalog,
    )
    nodes = [
        make_route_query_node(
            client,
            catalog=catalog,
            shared_join_aliases=catalog.shared_join_aliases,
            sql_schema_text=sql_schema_text,
            graph_schema_text=graph_schema_text,
        ),
        make_plan_outputs_node(client, catalog),
        make_execute_plan_node(
            sql_agent=sql_agent,
            cypher_agent=graph_agent,
            sql_schema_text=sql_schema_text,
            cypher_schema_text=graph_schema_text,
        ),
        make_compose_results_node(semantic_catalog=catalog),
    ]

    async def modern(inputs: dict[str, Any]) -> dict[str, Any]:
        current = dict(inputs)
        # Unnamed properties/IDs stay in the query; do not perform fuzzy name lookup.
        named = [e for e in inputs["query_intent"]["entities"] if e["kind"] == "name"]
        if named or inputs.get("confirmed_entity"):
            resolution = dict(current)
            if named and not inputs.get("confirmed_entity"):
                resolution["query"] = " / ".join(e["text"] for e in named)
            current.update(await resolver(cast(Any, resolution)))
        for node in nodes:
            current.update(await node(cast(Any, current)))
            if current.get("query_failure"):
                break
        return current

    return make_coordinator(client, knowledge, strict, modern, trace=trace)
