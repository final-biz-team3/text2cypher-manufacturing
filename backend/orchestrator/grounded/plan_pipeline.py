"""Executable plan coordinator with one shared request budget."""

import asyncio
import os
import re
from time import perf_counter
from typing import Any, cast

import neo4j.exceptions
import openai
import psycopg

from core.observability.events import emit_event
from orchestrator.errors import EntityAmbiguousError
from orchestrator.grounded.budget import (
    BudgetClient,
    BudgetExceededError,
    RequestBudget,
)
from orchestrator.grounded.context import load_knowledge
from orchestrator.grounded.plan_engine import PlanError, execute_plan, final_result
from orchestrator.grounded.plan_execution import make_step_executor
from orchestrator.grounded.plan_generation import generate_plan, interpret_meaning
from orchestrator.grounded.plan_models import (
    FinalResult,
    Meaning,
    QueryPlan,
    QueryStatus,
)
from orchestrator.grounded.plan_validation import validate_execution
from orchestrator.grounded.strict_adapter import adapt_strict_plan, strict_eligible
from orchestrator.guards.cypher_guard import make_cypher_guard
from orchestrator.guards.sql_guard import make_sql_guard
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.query_failures import make_query_failure
from orchestrator.state import OrchestratorState
from strict_query.pipeline import build_strict_query


def render_result(
    result: FinalResult, meaning: Meaning
) -> tuple[str, list[dict[str, Any]]]:
    def escape(value: Any) -> str:
        return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", str(value)).replace("\n", " ")

    lines, references = [], []
    for section in result.sections:
        lines.append(escape(section.title))
        if not section.rows:
            lines.append("지정한 조건에 해당하는 데이터가 없습니다.")
        for index, row in enumerate(section.rows[:10]):
            lines.append(
                "- "
                + "; ".join(
                    f"{escape(c.label)}: {escape('NULL' if row[c.id] is None else row[c.id])}"
                    + (f" ({escape(c.unit)})" if c.unit else "")
                    for c in section.columns
                )
            )
            references.append(
                {
                    "section": section.id,
                    "row_index": index,
                    "fields": [c.id for c in section.columns],
                }
            )
        if len(section.rows) > 10:
            lines.append(
                "앞의 10개 항목을 표시했습니다. 전체 조회 결과는 결과 표에서 확인할 수 있습니다."
            )
        lines.append("")
    if result.truncated:
        lines.append("조회 한도에 도달해 일부 결과만 표시합니다.")
    conditions = [
        r.evidence.text
        for r in meaning.requirements
        if r.kind in {"filter", "period", "grain", "ordering", "limit", "relationship"}
    ]
    if conditions:
        lines.append(
            "적용 조건: " + "; ".join(escape(c) for c in dict.fromkeys(conditions))
        )
    if meaning.assumptions:
        lines.append(
            "문서상 기본값: "
            + "; ".join(
                f"{escape(a.text)} [{escape(a.source)}]" for a in meaning.assumptions
            )
        )
    return "\n".join(lines).strip(), references


def make_plan_node(
    client: Any, pool: Any, *, sql_schema: Any, graph_schema: Any, catalog: Any
) -> Any:
    knowledge = load_knowledge()
    cap = int(os.getenv("SQL_ROW_LIMIT", "200"))
    execute = make_step_executor(
        pool, make_sql_guard(sql_schema), make_cypher_guard(graph_schema), cap
    )

    async def run(state: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        budget = RequestBudget(
            float(os.getenv("GROUNDED_QUERY_TIMEOUT_SECONDS", "120")),
            int(os.getenv("GROUNDED_MODEL_CALL_LIMIT", "12")),
        )
        scoped = BudgetClient(client, budget)
        reports: list[dict[str, Any]] = []
        common: dict[str, Any] = {
            "query": state["query"],
            "validation_report": {
                "version": 2,
                "knowledge_sha256": knowledge.digest,
                "candidates": reports,
            },
        }

        def finish(status: QueryStatus, message: str) -> dict[str, Any]:
            common["validation_report"].update(
                elapsed_ms=(perf_counter() - started) * 1000, model_calls=budget.calls
            )
            emit_event(
                "grounded.query.completed",
                "pipeline",
                outcome=status,
                duration_ms=(perf_counter() - started) * 1000,
                model_calls=budget.calls,
            )
            failure = (
                None
                if status in {"answered", "empty", "clarification"}
                else make_query_failure(
                    code=status.upper(),
                    stage="validation",
                    category=(
                        "POLICY_BLOCKED" if status == "blocked" else status.upper()
                    ),
                    kind=(
                        "infrastructure"
                        if status == "error"
                        else (
                            "internal" if status == "unverified" else "user_correctable"
                        )
                    ),
                    retryable=False,
                    user_safe_reason=message,
                    suggested_action="",
                    failed_tool=None,
                )
            )
            return {
                **common,
                "status": status,
                "result": None,
                "final_answer": message,
                "query_failure": failure,
            }

        try:
            async with asyncio.timeout(
                float(os.getenv("GROUNDED_QUERY_TIMEOUT_SECONDS", "120"))
            ):
                meaning = await interpret_meaning(scoped, state["query"], knowledge)
                common["query_intent"] = meaning.model_dump()
                if meaning.action in {"write", "mixed"}:
                    return finish(
                        "blocked",
                        "데이터 변경 요청은 실행할 수 없습니다. 조회할 내용만 요청해 주세요.",
                    )
                if meaning.clarification:
                    return {
                        **finish("clarification", meaning.clarification.question),
                        "clarification": meaning.clarification.model_dump(),
                    }
                resolved_entities = None
                if any(m.kind == "name" for m in meaning.mentions) or state.get(
                    "confirmed_entity"
                ):
                    resolver = make_resolve_entity_node(scoped, pool, graph_schema)
                    resolution = await resolver(cast(OrchestratorState, state))
                    resolved_entities = resolution.get("entity")
                    common["entity"] = resolved_entities
                plan: QueryPlan | None = None
                feedback: Any = None
                use_strict = False
                for attempt in range(2):
                    candidate_start = perf_counter()
                    report: dict[str, Any] = {
                        "accepted": False,
                        "strategy": "general",
                        "phase": "planning",
                    }
                    reports.append(report)
                    try:
                        async with asyncio.timeout(
                            float(os.getenv("GROUNDED_CANDIDATE_TIMEOUT_SECONDS", "45"))
                        ):
                            if plan is None or (attempt and not use_strict):
                                plan = await generate_plan(
                                    scoped,
                                    state["query"],
                                    meaning,
                                    knowledge,
                                    feedback,
                                    resolved_entities,
                                )
                            if plan.support != "executable":
                                if (
                                    attempt == 0
                                    and plan.support == "missing_data"
                                    and meaning.data_support != "missing"
                                ):
                                    feedback = {
                                        "issue": "Interpretation found queryable fields. Recheck whether missing_data confuses unavailable prompt values with live queryable facts."
                                    }
                                    plan = None
                                    continue
                                return finish(
                                    (
                                        "unanswerable"
                                        if plan.support == "missing_data"
                                        else "unsupported"
                                    ),
                                    (
                                        "현재 데이터에서 필요한 사실을 확인할 수 없습니다."
                                        if plan.support == "missing_data"
                                        else "현재 처리 구조로 요청한 조회를 완성하지 못했습니다."
                                    ),
                                )
                            use_strict = attempt == 0 and strict_eligible(
                                meaning, plan, catalog
                            )
                            active = plan
                            if use_strict:
                                report["strategy"] = "strict"
                                legacy = build_strict_query(scoped, pool)
                                legacy_input = {"query": state["query"]}
                                if state.get("confirmed_entity") is not None:
                                    legacy_input["confirmed_entity"] = state[
                                        "confirmed_entity"
                                    ]
                                candidate = await legacy(legacy_input)
                                active = adapt_strict_plan(plan, candidate)
                            report["phase"] = "execution"
                            results = await execute_plan(active, meaning, execute, cap)
                            public = final_result(active, meaning, results)
                            report["phase"] = "validation"
                            report.update(
                                await validate_execution(
                                    scoped,
                                    state["query"],
                                    meaning,
                                    active,
                                    results,
                                    knowledge,
                                )
                            )
                            common["query_plan"] = active.model_dump()
                            common["step_results"] = {
                                k: v.model_dump() for k, v in results.items()
                            }
                            if report["accepted"]:
                                answer, references = render_result(public, meaning)
                                response = finish(
                                    (
                                        "answered"
                                        if any(s.rows for s in public.sections)
                                        else "empty"
                                    ),
                                    answer,
                                )
                                response.update(
                                    result=public.model_dump(),
                                    query_strategy="strict" if use_strict else "pr61",
                                    answer_metadata={
                                        "mode": "structured",
                                        "attemptCount": 0,
                                        "fallbackReason": None,
                                        "validationRejected": False,
                                        "references": references,
                                    },
                                    tool_plan=list(
                                        dict.fromkeys(s.tool for s in active.steps)
                                    ),
                                )
                                # Legacy fields only represent a single unambiguous source step.
                                for tool, query_field, result_field in (
                                    ("sql", "sql_query", "sql_result"),
                                    ("graph", "cypher_query", "graph_result"),
                                ):
                                    matches = [
                                        s for s in active.steps if s.tool == tool
                                    ]
                                    if len(matches) == 1:
                                        step = matches[0]
                                        response[query_field] = step.query
                                        response[result_field] = {
                                            "result": results[step.id].rows,
                                            "error": None,
                                            "attempts": [],
                                            "empty_reason": (
                                                None
                                                if results[step.id].rows
                                                else "NO_DATA"
                                            ),
                                        }
                                return response
                            feedback = {
                                "issues": report.get("issues", []),
                                "format_errors": report.get("format_errors", []),
                            }
                            if report.get("status") == "review_invalid":
                                return finish(
                                    "unverified",
                                    "조회는 완료했지만 검토 결과가 일관되지 않아 답을 확정하지 못했습니다.",
                                )
                    except (
                        PlanError,
                        ValueError,
                        TimeoutError,
                        psycopg.ProgrammingError,
                        psycopg.DataError,
                        neo4j.exceptions.CypherSyntaxError,
                        neo4j.exceptions.CypherTypeError,
                    ) as exc:
                        report.update(
                            error_type=type(exc).__name__,
                            phase=report["phase"],
                            diagnostic=str(exc) if isinstance(exc, PlanError) else None,
                        )
                        feedback = {
                            "type": type(exc).__name__,
                            "diagnostic": (
                                str(exc)
                                if isinstance(exc, PlanError)
                                else (
                                    str(exc.diag.message_primary)
                                    if isinstance(
                                        exc,
                                        (psycopg.ProgrammingError, psycopg.DataError),
                                    )
                                    else (
                                        str(exc.message)
                                        if isinstance(
                                            exc,
                                            (
                                                neo4j.exceptions.CypherSyntaxError,
                                                neo4j.exceptions.CypherTypeError,
                                            ),
                                        )
                                        else "Candidate could not be validated or completed within budget"
                                    )
                                )
                            ),
                        }
                    finally:
                        report["elapsed_ms"] = (perf_counter() - candidate_start) * 1000
                return finish(
                    "unverified",
                    "질문의 조건과 조회 결과가 일치하는지 확인하지 못해 답을 확정하지 않았습니다.",
                )
        except EntityAmbiguousError:
            raise
        except (TimeoutError, BudgetExceededError):
            return finish("error", "질의 처리 시간 또는 모델 호출 한도에 도달했습니다.")
        except (
            openai.APIError,
            psycopg.OperationalError,
            neo4j.exceptions.ServiceUnavailable,
            neo4j.exceptions.SessionExpired,
        ):
            return finish(
                "error",
                "조회 시스템에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            )
        except Exception as exc:
            common["validation_report"]["error_type"] = type(exc).__name__
            if isinstance(exc, PlanError):
                common["validation_report"]["diagnostic"] = str(exc)
            return finish(
                "unverified", "질문을 실행 가능한 조회로 연결하지 못했습니다."
            )

    return run
