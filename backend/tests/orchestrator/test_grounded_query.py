"""Specification tests over synthetic data; no evaluation assets are imported."""

import asyncio
import sqlite3
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from orchestrator.grounded.answer import grounded_answer, render_selection
from orchestrator.grounded.context import KnowledgeContext, load_knowledge
from orchestrator.grounded.models import AnswerSelection, CandidateReview, QueryIntent
from orchestrator.grounded.pipeline import make_coordinator
from orchestrator.grounded.planning import plan_request_outputs
from orchestrator.grounded.runtime import grounded_execution
from orchestrator.grounded.validation import deterministic_checks, validate_candidate
from orchestrator.planning import Subquery


@pytest.fixture
def intent():
    return QueryIntent.model_validate(
        {
            "action": "read",
            "requirements": [
                {
                    "id": "r1",
                    "kind": "filter",
                    "description": "cost >= 5",
                    "evidence": {"text": "5 이상", "source": "question"},
                }
            ],
            "entities": [],
            "outputs": [
                {
                    "alias": "cost",
                    "label": "단가",
                    "tool": "sql",
                    "source_fields": ["sql:public.items.cost"],
                    "expression": "cost",
                    "value_type": "number",
                    "nullable": False,
                    "unit": None,
                    "evidence": {"text": "단가", "source": "question"},
                }
            ],
            "assumptions": [],
            "clarification": None,
            "reason": "",
        }
    )


@pytest.fixture
def knowledge():
    return KnowledgeContext(
        {"sql:public.items.cost": "numeric unit unspecified"},
        frozenset({"sql:public.items.cost"}),
        "test-digest",
    )


def candidate(rows=None):
    return {
        "entity": {"id": 17},
        "sql_query": "SELECT cost AS cost FROM public.items WHERE cost >= 5",
        "composed_result": {
            "mode": "single",
            "rows": [{"cost": 6}] if rows is None else rows,
            "sections": {},
            "error": None,
            "empty_reason": "NO_DATA" if rows == [] else None,
            "truncated": False,
        },
        "execution_evidence": {"sql": {"columns": ["cost"]}},
    }


def test_evidence_and_physical_fields_are_required(intent, knowledge):
    knowledge.validate_intent(intent, "단가 5 이상")
    invalid = intent.model_copy(deep=True)
    invalid.outputs[0].source_fields = ["sql:public.secrets.cost"]
    with pytest.raises(ValueError, match="physical"):
        knowledge.validate_intent(invalid, "단가 5 이상")
    invalid = intent.model_copy(deep=True)
    invalid.requirements[0].evidence.text = "condition invented by model"
    with pytest.raises(ValueError, match="Evidence"):
        knowledge.validate_intent(invalid, "단가 5 이상")


@pytest.mark.parametrize("extra", [[], [(3, 30)], [(1, 11), (None, 90)]])
def test_independent_hybrid_join_matches_relational_semantics(extra):
    from orchestrator.composition import compose_results

    left = [(1, 10), (2, 20), (None, 40)]
    right = [(1, 100), (3, 300), (None, 400)] + extra
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE a(id INTEGER, x INTEGER)")
    conn.execute("CREATE TABLE b(id INTEGER, y INTEGER)")
    conn.executemany("INSERT INTO a VALUES (?,?)", left)
    conn.executemany("INSERT INTO b VALUES (?,?)", right)
    expected = sorted(
        conn.execute("SELECT a.id,x,y FROM a JOIN b ON a.id=b.id").fetchall()
    )
    conn.close()
    plan: list[Subquery] = [
        {
            "id": tool,
            "tool": tool,
            "question": "source",
            "dependsOn": [],
            "joinKeys": ["productId"],
            "requiredOutputs": ["productId", metric],
        }
        for tool, metric in (("sql", "x"), ("graph", "y"))
    ]
    sources = {
        "sql": {"result": [{"productId": k, "x": v} for k, v in left]},
        "graph": {"result": [{"productId": k, "y": v} for k, v in right]},
    }
    result = compose_results(plan, sources, row_limit=200, allow_independent_join=True)
    assert result["error"] is None
    assert sorted((r["productId"], r["x"], r["y"]) for r in result["rows"]) == expected
    # A bound lookup is still required to respect the upstream identity domain.
    plan[1]["dependsOn"] = ["sql"]
    plan[1]["inputBindings"] = {"ids": "sql.productId"}
    sources["sql"]["result"] = [{"productId": 1, "x": 10}]
    assert compose_results(plan, sources, row_limit=200, allow_independent_join=True)[
        "error"
    ]


def test_inconclusive_empty_and_stale_query_are_not_validated_as_no_data(intent):
    empty = candidate([])
    empty["composed_result"]["empty_reason"] = "INCONCLUSIVE"
    assert any(
        c["verdict"] == "contradicted" for c in deterministic_checks(intent, empty)
    )
    stale = candidate()
    stale["execution_evidence"]["sql"]["query"] = "SELECT cost FROM public.items"
    assert any(
        c["check"] == "executed_query:sql" and c["verdict"] == "contradicted"
        for c in deterministic_checks(intent, stale)
    )


def test_request_plan_cannot_silently_drop_output_source(intent):
    with pytest.raises(ValueError, match="omits requested output sources"):
        plan_request_outputs(
            {
                "query_intent": intent.model_dump(),
                "routeDraft": {
                    "subqueries": [
                        {
                            "id": "g",
                            "tool": "graph",
                            "question": "relationships",
                            "dependsOn": [],
                            "joinKeys": [],
                        }
                    ]
                },
            }
        )


async def test_hybrid_review_requires_composition_evidence(
    monkeypatch, intent, knowledge
):
    item = candidate()
    item["subqueries"] = [{"id": "s", "tool": "sql"}, {"id": "g", "tool": "graph"}]
    review = CandidateReview.model_validate(
        {
            "interpretation_complete": True,
            "checks": [
                {
                    "requirement_id": "r1",
                    "verdict": "supported",
                    "tool": "sql",
                    "query_excerpt": "cost >= 5",
                    "explanation": "filter preserved",
                }
            ],
            "clarification": None,
        }
    )
    call = AsyncMock(return_value=review)
    monkeypatch.setattr("orchestrator.grounded.validation.typed_call", call)
    report = await validate_candidate(None, "단가 5 이상", intent, item, knowledge)
    assert not report["accepted"] and report["status"] == "unverified"
    requirements = call.call_args.kwargs["payload"]["review_requirements"]
    assert any(r["id"] == "__composition__" for r in requirements)


async def test_interpretation_repairs_provenance_once(monkeypatch, intent, knowledge):
    from orchestrator.grounded.interpret import interpret

    broken = intent.model_copy(deep=True)
    broken.outputs[0].source_fields = ["sql:public.missing.cost"]
    call = AsyncMock(side_effect=[broken, intent])
    monkeypatch.setattr("orchestrator.grounded.interpret.typed_call", call)
    result = await interpret(None, "단가 5 이상", knowledge)
    assert result == intent
    assert call.await_count == 2
    repair_payload = call.call_args.kwargs["payload"]
    assert repair_payload["question"] == "단가 5 이상"
    assert repair_payload["repair"]["diagnostics"]


async def test_interpretation_repair_cannot_accept_unsupported_fields(
    monkeypatch, intent, knowledge
):
    from orchestrator.grounded.interpret import interpret

    broken = intent.model_copy(deep=True)
    broken.outputs[0].source_fields = ["sql:public.missing.cost"]
    call = AsyncMock(return_value=broken)
    monkeypatch.setattr("orchestrator.grounded.interpret.typed_call", call)
    with pytest.raises(ValueError, match="physical"):
        await interpret(None, "단가 5 이상", knowledge)
    assert call.await_count == 2


def test_request_scoped_output_not_catalog_whitelist(intent):
    intent.outputs[0].alias = "myNewAggregate"
    state = {
        "query_intent": intent.model_dump(),
        "routeDraft": {
            "subqueries": [
                {
                    "id": "measure",
                    "tool": "sql",
                    "question": "단가 5 이상",
                    "dependsOn": [],
                    "joinKeys": [],
                }
            ]
        },
    }
    planned = plan_request_outputs(state)
    assert planned["subqueries"][0]["requiredOutputs"] == ["myNewAggregate"]
    assert "public.items.cost" in str(planned["subqueries"][0]["generatorRules"])


@pytest.mark.parametrize(
    "rows,verdict",
    [
        ([], "supported"),
        ([{"cost": None}], "contradicted"),
        ([{"cost": True}], "contradicted"),
        ([{"wrong": 6}], "contradicted"),
        ([{"cost": 6}], "supported"),
    ],
)
def test_result_shape_is_separate_from_semantics(intent, rows, verdict):
    checks = deterministic_checks(intent, candidate(rows))
    assert checks[-1]["verdict"] == verdict
    assert all("semantic" not in c["check"] for c in checks)


async def test_execution_success_does_not_bypass_semantic_review(
    monkeypatch, intent, knowledge
):
    review = CandidateReview.model_validate(
        {
            "interpretation_complete": True,
            "checks": [
                {
                    "requirement_id": "r1",
                    "verdict": "contradicted",
                    "tool": "sql",
                    "query_excerpt": "cost >= 5",
                    "explanation": "boundary wrong",
                }
            ],
            "clarification": None,
        }
    )
    call = AsyncMock(return_value=review)
    monkeypatch.setattr("orchestrator.grounded.validation.typed_call", call)
    report = await validate_candidate(
        None, "단가 5 이상", intent, candidate(), knowledge
    )
    assert not report["accepted"] and report["reviewed"]
    assert call.call_args.kwargs["payload"]["question"] == "단가 5 이상"


@pytest.mark.parametrize(
    "ids,excerpt",
    [(["other"], "cost >= 5"), (["r1", "r1"], "cost >= 5"), (["r1"], "invented query")],
)
async def test_review_needs_complete_unique_ids_and_actual_query_evidence(
    monkeypatch, intent, knowledge, ids, excerpt
):
    review = CandidateReview.model_validate(
        {
            "interpretation_complete": True,
            "checks": [
                {
                    "requirement_id": rid,
                    "verdict": "supported",
                    "tool": "sql",
                    "query_excerpt": excerpt,
                    "explanation": "checked",
                }
                for rid in ids
            ],
            "clarification": None,
        }
    )
    monkeypatch.setattr(
        "orchestrator.grounded.validation.typed_call", AsyncMock(return_value=review)
    )
    report = await validate_candidate(
        None, "단가 5 이상", intent, candidate(), knowledge
    )
    assert not report["accepted"]


async def test_fallback_uses_fresh_state_and_one_alternative(
    monkeypatch, intent, knowledge
):
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    original = {"query": "단가 5 이상", "confirmed_entity": {"id": 9}}
    before = deepcopy(original)

    async def bad(inputs):
        inputs["confirmed_entity"]["id"] = 999
        return {**candidate(), "entity": {"id": 999}}

    modern = AsyncMock(return_value=candidate())
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.validate_candidate",
        AsyncMock(
            side_effect=[
                {
                    "accepted": False,
                    "deterministic": [{"check": "filter", "verdict": "contradicted"}],
                },
                {"accepted": True},
            ]
        ),
    )
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.grounded_answer",
        AsyncMock(return_value={"final_answer": "ok"}),
    )
    result = await make_coordinator(None, knowledge, bad, modern)(original)
    assert original == before
    forwarded = modern.call_args.args[0]
    assert forwarded["confirmed_entity"] == {"id": 9}
    assert not {"entity", "sql_query", "composed_result"} & forwarded.keys()
    assert "candidate_feedback" in forwarded
    assert result["query_strategy"] == "pr61"
    assert len(result["validation_report"]["candidates"]) == 2
    modern.assert_awaited_once()
    assert not grounded_execution.get()


@pytest.mark.parametrize(
    "action,code",
    [
        ("write", "REQUEST_POLICY_BLOCKED"),
        ("mixed", "REQUEST_POLICY_BLOCKED"),
        ("unanswerable", "UNANSWERABLE"),
        ("clarify", "CLARIFICATION_NEEDED"),
    ],
)
async def test_non_read_does_not_execute(monkeypatch, intent, knowledge, action, code):
    intent.action = action
    if action == "clarify":
        from orchestrator.grounded.models import Clarification

        intent.clarification = Clarification(
            question="기간을 지정해 주세요", options=[]
        )
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    strict, modern = AsyncMock(), AsyncMock()
    result = await make_coordinator(None, knowledge, strict, modern)({"query": "q"})
    assert result["query_failure"]["code"] == code
    strict.assert_not_awaited()
    modern.assert_not_awaited()


async def test_cancellation_is_propagated(monkeypatch, intent, knowledge):
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    strict = AsyncMock(side_effect=asyncio.CancelledError)
    modern = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await make_coordinator(None, knowledge, strict, modern)({"query": "q"})
    modern.assert_not_awaited()
    assert not grounded_execution.get()


async def test_verified_empty_does_not_trigger_another_candidate(
    monkeypatch, intent, knowledge
):
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.validate_candidate",
        AsyncMock(return_value={"accepted": True}),
    )
    modern = AsyncMock()
    result = await make_coordinator(
        None, knowledge, AsyncMock(return_value=candidate([])), modern
    )({"query": "q"})
    assert "없습니다" in result["final_answer"]
    assert result["query_failure"] is None
    modern.assert_not_awaited()


def test_answer_values_are_taken_from_same_row_and_all_requested_fields(intent):
    selection = AnswerSelection.model_validate(
        {"items": [{"section": "", "row_index": 1, "fields": ["cost"]}]}
    )
    answer = render_selection(selection, {"": [{"cost": 100}, {"cost": 7}]}, intent)
    assert "단가: 7" in answer and "100" not in answer
    selection.items[0].fields = []
    with pytest.raises(ValueError, match="omits"):
        render_selection(selection, {"": [{"cost": 100}, {"cost": 7}]}, intent)


async def test_small_answers_preserve_every_row_without_a_model_call(intent):
    result = await grounded_answer(None, intent, candidate([{"cost": 8}, {"cost": 6}]))
    assert result["final_answer"].index("단가: 8") < result["final_answer"].index(
        "단가: 6"
    )
    assert len(result["answer_metadata"]["references"]) == 2
    assert result["answer_metadata"]["attemptCount"] == 0


def test_physical_context_excludes_question_shapes():
    knowledge = load_knowledge()
    assert "sql:production.product.productid" in knowledge.fields
    assert "graph:Product.productId" in knowledge.fields
    assert not any("resultShapes" in source for source in knowledge.sources)


def test_prompt_keeps_whole_schema_without_duplicate_field_definitions():
    import json

    knowledge = load_knowledge()
    payload = knowledge.prompt_payload()
    assert set(payload["physical_fields"]) == knowledge.fields
    for path in ("schema/sql_schema.yaml", "schema/graph_schema.yaml"):
        assert payload["sources"][path] == json.loads(knowledge.sources[path])
    assert not set(payload["sources"]) & knowledge.fields
    assert len(json.dumps(payload)) < len(json.dumps(knowledge.sources))


def test_semantics_across_synthetic_database_variants():
    # Distinguish >= from >, anti-join from inner join, and aggregate grain.
    for extra in ([], [(4, "A", 5)], [(4, "B", None), (5, "B", 8)]):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE items(id INTEGER, category TEXT, cost INTEGER)")
        rows = [(1, "A", 5), (2, "A", 8), (3, "B", None)] + extra
        conn.executemany("INSERT INTO items VALUES (?,?,?)", rows)
        inclusive = conn.execute(
            "SELECT id FROM items WHERE cost >= 5 ORDER BY id"
        ).fetchall()
        equivalent = conn.execute(
            "SELECT id FROM items WHERE NOT(cost < 5) ORDER BY id"
        ).fetchall()
        strict = conn.execute(
            "SELECT id FROM items WHERE cost > 5 ORDER BY id"
        ).fetchall()
        assert inclusive == equivalent and strict != inclusive
        grouped = conn.execute(
            "SELECT category, COUNT(cost), SUM(cost) FROM items GROUP BY category ORDER BY category"
        ).fetchall()
        assert grouped[0][0] == "A" and grouped[0][1] >= 2
        conn.close()


@pytest.mark.parametrize("raised", [False, True])
async def test_infrastructure_is_not_reported_as_semantic_failure(
    monkeypatch, intent, knowledge, raised
):
    from orchestrator.errors import QueryInfrastructureError

    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    strict = (
        AsyncMock(side_effect=QueryInfrastructureError())
        if raised
        else AsyncMock(return_value={"query_failure": {"kind": "infrastructure"}})
    )
    modern = AsyncMock()
    result = await make_coordinator(None, knowledge, strict, modern)({"query": "q"})
    assert result["query_failure"]["kind"] == "infrastructure"
    assert result["query_failure"]["code"] == "QUERY_INFRASTRUCTURE_UNAVAILABLE"
    modern.assert_not_awaited()
    assert not grounded_execution.get()


async def test_candidate_timeout_allows_only_one_clean_alternative(
    monkeypatch, intent, knowledge
):
    monkeypatch.setenv("GROUNDED_CANDIDATE_TIMEOUT_SECONDS", "0.02")
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )

    async def slow(_):
        await asyncio.sleep(1)

    modern = AsyncMock(return_value=candidate([]))
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.validate_candidate",
        AsyncMock(return_value={"accepted": True}),
    )
    result = await make_coordinator(None, knowledge, slow, modern)({"query": "q"})
    assert result["query_strategy"] == "pr61"
    assert result["validation_report"]["candidates"][0]["error_type"] == "TimeoutError"
    modern.assert_awaited_once()


async def test_request_traces_are_isolated_under_concurrency(
    monkeypatch, intent, knowledge
):
    from contextvars import ContextVar

    trace: ContextVar[dict[str, str]] = ContextVar("test_trace")
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.validate_candidate",
        AsyncMock(return_value={"accepted": True}),
    )

    async def run(inputs):
        assert grounded_execution.get()
        trace.get()["request"] = inputs["query"]
        await asyncio.sleep(0)
        assert trace.get()["request"] == inputs["query"]
        return candidate([])

    node = make_coordinator(None, knowledge, run, AsyncMock(), trace=trace)
    outputs = await asyncio.gather(node({"query": "left"}), node({"query": "right"}))
    assert [r["execution_evidence"]["request"] for r in outputs] == ["left", "right"]
    assert not grounded_execution.get()


def test_dynamic_binding_preserves_source_ownership():
    from orchestrator.planning import route_draft_json_schema, validate_route_subqueries

    request_outputs = {"sql": ["newMeasure"], "graph": []}
    schema = route_draft_json_schema({"productId"}, request_outputs=request_outputs)
    binding = schema["properties"]["subqueries"]["items"]["properties"]["inputBindings"]
    assert "newMeasure" in binding["items"]["properties"]["sourceOutput"]["enum"]
    routes = [
        {
            "id": "s",
            "tool": "sql",
            "question": "measure",
            "dependsOn": [],
            "joinKeys": [],
            "inputBindings": [],
        },
        {
            "id": "g",
            "tool": "graph",
            "question": "relationship",
            "dependsOn": ["s"],
            "joinKeys": [],
            "inputBindings": [
                {
                    "target": "values",
                    "sourceSubqueryId": "s",
                    "sourceOutput": "newMeasure",
                }
            ],
        },
    ]
    assert validate_route_subqueries(routes, request_outputs=request_outputs)[1][
        "inputBindings"
    ] == {"values": "s.newMeasure"}
    with pytest.raises(ValueError, match="owned"):
        validate_route_subqueries(
            routes, request_outputs={"sql": [], "graph": ["newMeasure"]}
        )


def test_default_and_unit_need_documented_evidence(intent, knowledge):
    from orchestrator.grounded.models import Evidence

    intent.outputs[0].unit = "KRW"
    with pytest.raises(ValueError, match="Units"):
        knowledge.validate_intent(intent, "단가 5 이상")
    intent.outputs[0].unit = None
    intent.assumptions = [Evidence(source="question", text="5 이상")]
    with pytest.raises(ValueError, match="Defaults"):
        knowledge.validate_intent(intent, "단가 5 이상")


def test_synthetic_join_direction_null_duplicates_and_ties():
    for extra in ([], [(4, 1)], [(4, 1), (1, 3)]):
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE edges(parent int, child int)")
            conn.executemany(
                "INSERT INTO edges VALUES (?,?)",
                [(1, 2), (1, 3), (2, 4), (5, None), *extra],
            )
            forward = conn.execute(
                "SELECT child FROM edges WHERE parent=1 ORDER BY child"
            ).fetchall()
            reverse = conn.execute(
                "SELECT parent FROM edges WHERE child=1 ORDER BY parent"
            ).fetchall()
            assert forward != reverse
            counts = conn.execute(
                "SELECT COUNT(child),COUNT(DISTINCT child),COUNT(*) FROM edges"
            ).fetchone()
            assert counts[0] < counts[2]
            if extra and len(extra) == 2:
                assert counts[1] < counts[0]
            assert conn.execute(
                "SELECT parent FROM edges WHERE child IS NULL"
            ).fetchall() == [(5,)]
            conn.execute("CREATE TABLE measures(id int, qty int)")
            conn.executemany(
                "INSERT INTO measures VALUES (?,?)", [(2, 7), (1, 7), (3, 4)]
            )
            assert conn.execute(
                "SELECT id FROM measures ORDER BY qty DESC,id ASC LIMIT 2"
            ).fetchall() == [(1,), (2,)]


def test_optional_api_clarification_preserves_legacy_serialization():
    from api.chat import ChatResponse
    from orchestrator.grounded.models import Clarification

    response = ChatResponse(query="q")
    assert "clarification" not in response.model_dump()
    response.clarification = Clarification(question="기간은?", options=[])
    assert response.model_dump()["clarification"] == {
        "question": "기간은?",
        "options": [],
    }


@pytest.mark.parametrize(
    "module_name",
    [
        "orchestrator.subgraphs.retry_agent",
        "strict_query.snapshot.orchestrator.subgraphs.retry_agent",
    ],
)
async def test_both_generators_preserve_empty_without_regeneration(module_name):
    import importlib
    import logging

    generate = AsyncMock(return_value="SELECT cost FROM public.items WHERE cost > 99")
    execute = AsyncMock(return_value=[])
    graph = importlib.import_module(module_name).make_retry_agent_subgraph(
        logger=logging.getLogger(__name__),
        label="sql_agent",
        generate=generate,
        execute=execute,
        connection_exceptions=(),
        retryable_exceptions=(),
        empty_result_feedback="must not be delivered",
    )
    token = grounded_execution.set(True)
    try:
        result = await graph.ainvoke(
            {
                "query": "cost over 99",
                "entity": None,
                "schema": "items(cost numeric)",
                "messages": [],
                "result": None,
                "error": None,
                "attempt_count": 0,
                "attempts": [],
                "empty_retried": False,
                "empty_reason": None,
            }
        )
    finally:
        grounded_execution.reset(token)
    generate.assert_awaited_once()
    execute.assert_awaited_once()
    assert result["result"] == [] and result["empty_reason"] == "NO_DATA"
    assert result["error"] is None


async def test_strict_guessed_name_does_not_force_clarification(
    monkeypatch, intent, knowledge
):
    from orchestrator.errors import EntityAmbiguousError

    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.validate_candidate",
        AsyncMock(return_value={"accepted": True}),
    )
    strict = AsyncMock(side_effect=EntityAmbiguousError([], "invented product"))
    modern = AsyncMock(return_value=candidate([]))
    result = await make_coordinator(None, knowledge, strict, modern)({"query": "q"})
    assert result["query_strategy"] == "pr61"
    modern.assert_awaited_once()


async def test_total_timeout_retains_the_active_candidate_phase(
    monkeypatch, intent, knowledge
):
    monkeypatch.setenv("GROUNDED_QUERY_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("GROUNDED_CANDIDATE_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.interpret", AsyncMock(return_value=intent)
    )
    calls = 0

    async def review(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"accepted": False}
        await asyncio.Event().wait()

    monkeypatch.setattr("orchestrator.grounded.pipeline.validate_candidate", review)
    run = AsyncMock(return_value=candidate())
    result = await make_coordinator(None, knowledge, run, run)({"query": "q"})
    assert result["query_failure"]["code"] == "QUERY_TIMEOUT"
    reports = result["validation_report"]["candidates"]
    assert len(reports) == 2
    assert reports[-1]["phase"] == "validation"
    assert reports[-1]["error_type"] == "CancelledError"
    assert reports[-1]["elapsed_ms"] > 0
