"""Functional specifications using synthetic schemas and relational oracles."""

import asyncio
import sqlite3
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.grounded.budget import (
    BudgetClient,
    BudgetExceededError,
    RequestBudget,
)
from orchestrator.grounded.context import KnowledgeContext
from orchestrator.grounded.plan_engine import (
    PlanError,
    apply_operation,
    execute_plan,
    final_result,
    predicate_value,
    validate_plan,
)
from orchestrator.grounded.plan_generation import validate_meaning
from orchestrator.grounded.plan_models import (
    Aggregate,
    Binding,
    Meaning,
    Mention,
    PlanReview,
    Predicate,
    Projection,
    QueryPlan,
    Relationship,
    ResultOperation,
    ResultSection,
    SourceStep,
    StepResult,
)
from orchestrator.grounded.plan_pipeline import render_result
from orchestrator.grounded.plan_validation import inspect_sql, validate_execution
from orchestrator.grounded.strict_adapter import adapt_strict_plan


@pytest.fixture
def meaning():
    return Meaning.model_validate(
        {
            "action": "read",
            "data_support": "available",
            "missing_facts": [],
            "mentions": [],
            "assumptions": [],
            "clarification": None,
            "requirements": [
                {
                    "id": "r",
                    "kind": "filter",
                    "description": "cost >= 5",
                    "evidence": {"text": "5 이상", "source": "question"},
                    "fields": ["sql:public.items.cost"],
                    "predicate": {
                        "op": "ge",
                        "field": "sql:public.items.cost",
                        "value": 5,
                        "values": [],
                        "children": [],
                    },
                    "aggregate": None,
                    "group_by": [],
                    "relationship": None,
                    "ordering": [],
                    "limit": None,
                }
            ],
            "outputs": [
                {
                    "id": "value",
                    "alias": "cost",
                    "label": "단가",
                    "source_fields": ["sql:public.items.cost"],
                    "calculation": "cost",
                    "value_type": "number",
                    "nullable": True,
                    "unit": None,
                    "evidence": {"text": "단가", "source": "question"},
                }
            ],
        }
    )


@pytest.fixture
def knowledge():
    return KnowledgeContext(
        {"sql:public.items.cost": '{"type":"NUMERIC"}'},
        frozenset({"sql:public.items.cost"}),
        "synthetic",
    )


def source(name="s", query="SELECT cost AS cost FROM public.items WHERE cost >= 5"):
    return SourceStep(
        id=name,
        tool="sql",
        query=query,
        parameters=[],
        bindings=[],
        depends_on=[],
        requirement_ids=["r"],
        projections=[Projection(output_id="value", column="cost")],
        relationships=[],
    )


def plan():
    return QueryPlan(
        support="executable",
        reason="",
        steps=[source()],
        operations=[],
        sections=[
            ResultSection(
                id="answer",
                title="단가",
                input="s",
                projections=[Projection(output_id="value", column="cost")],
            )
        ],
    )


def result(name, rows, columns=None, truncated=False):
    return StepResult(
        step_id=name,
        rows=rows,
        columns=columns or list(rows[0] if rows else []),
        truncated=truncated,
        query="",
        elapsed_ms=0,
    )


def operation(kind, **kwargs):
    return ResultOperation.model_validate(
        {
            "id": "o",
            "kind": kind,
            "inputs": ["s"],
            "predicate": None,
            "columns": [],
            "aggregates": [],
            "ordering": [],
            "limit": None,
            "join_type": None,
            "left_keys": [],
            "right_keys": [],
            "requirement_ids": [],
            **kwargs,
        }
    )


@pytest.mark.parametrize("join_type", ["inner", "left", "semi", "anti"])
@pytest.mark.parametrize("extra", [[], [(1, 101)], [(4, 400), (None, 900)]])
def test_joins_match_sql_with_nulls_duplicates_and_unmatched_rows(join_type, extra):
    a = [(1, 10), (2, 20), (None, 30)]
    b = [(1, 100), (3, 300)] + extra
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE a(id,x)")
        conn.execute("CREATE TABLE b(id,y)")
        conn.executemany("INSERT INTO a VALUES (?,?)", a)
        conn.executemany("INSERT INTO b VALUES (?,?)", b)
        sql = {
            "inner": "SELECT a.id,x,b.id,y FROM a JOIN b ON a.id=b.id",
            "left": "SELECT a.id,x,b.id,y FROM a LEFT JOIN b ON a.id=b.id",
            "semi": "SELECT a.id,x FROM a WHERE EXISTS(SELECT 1 FROM b WHERE a.id=b.id)",
            "anti": "SELECT a.id,x FROM a WHERE NOT EXISTS(SELECT 1 FROM b WHERE a.id=b.id)",
        }[join_type]
        expected = conn.execute(sql).fetchall()
    results = {
        "s": result("s", [{"aid": k, "x": v} for k, v in a]),
        "b": result("b", [{"bid": k, "y": v} for k, v in b]),
    }
    output = apply_operation(
        operation(
            "join",
            inputs=["s", "b"],
            join_type=join_type,
            left_keys=["aid"],
            right_keys=["bid"],
        ),
        results,
        200,
    )
    assert [tuple(row[c] for c in output.columns) for row in output.rows] == expected


def test_numeric_join_and_distinct_follow_value_not_serialization():
    op = operation(
        "join", inputs=["s", "b"], join_type="inner", left_keys=["a"], right_keys=["b"]
    )
    actual = apply_operation(
        op,
        {"s": result("s", [{"a": 1}]), "b": result("b", [{"b": Decimal("1.0")}])},
        200,
    )
    assert len(actual.rows) == 1


@pytest.mark.parametrize("kind", ["filter", "aggregate", "sort", "join", "limit"])
def test_truncated_intermediate_never_becomes_complete_answer(kind):
    with pytest.raises(PlanError, match="Incomplete intermediate"):
        apply_operation(
            operation(kind), {"s": result("s", [{"x": 1}], truncated=True)}, 200
        )


def test_aggregate_null_empty_distinct_and_grouping():
    aggs = [
        Aggregate(output="all", function="count", field=None, distinct=False),
        Aggregate(output="nonnull", function="count", field="x", distinct=False),
        Aggregate(output="unique", function="count", field="x", distinct=True),
        Aggregate(output="total", function="sum", field="x", distinct=False),
    ]
    op = operation("aggregate", aggregates=aggs)
    output = apply_operation(
        op, {"s": result("s", [{"x": 2}, {"x": 2}, {"x": None}])}, 200
    )
    assert output.rows == [{"all": 3, "nonnull": 2, "unique": 1, "total": 4}]
    assert apply_operation(op, {"s": result("s", [], ["x"])}, 200).rows == [
        {"all": 0, "nonnull": 0, "unique": 0, "total": None}
    ]


def test_sort_null_placement_and_limit_after_filter():
    rows = result("s", [{"x": None}, {"x": 1}, {"x": 3}])
    predicate = Predicate(op="ge", field="x", value=2, values=[], children=[])
    filtered = apply_operation(
        operation("filter", predicate=predicate), {"s": rows}, 200
    )
    assert filtered.rows == [{"x": 3}]
    sorted_rows = apply_operation(
        operation(
            "sort", ordering=[{"field": "x", "descending": True, "nulls_first": False}]
        ),
        {"s": rows},
        200,
    )
    assert sorted_rows.rows == [{"x": 3}, {"x": 1}, {"x": None}]


def test_null_negation_uses_three_valued_logic():
    child = Predicate(op="lt", field="x", value=5, values=[], children=[])
    negated = Predicate(op="not", field=None, value=None, values=[], children=[child])
    assert predicate_value(negated, {"x": None}) is None
    assert predicate_value(negated, {"x": 5}) is True


async def test_same_source_steps_run_concurrently_and_binding_rows_stay_paired(meaning):
    active = plan()
    active.steps = [source("a"), source("b"), source("c")]
    active.steps[2].depends_on = ["a", "b"]
    active.steps[2].bindings = [
        Binding(parameter="rows", source_step="a", fields=["id", "qty"])
    ]
    # Assignments to nested models must be validated before execution.
    active = QueryPlan.model_validate(active.model_dump())
    active.sections[0].input = "c"
    started = set()
    gate = asyncio.Event()

    async def execute(step, params):
        if step.id in {"a", "b"}:
            started.add(step.id)
            if len(started) == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), 1)
            return result(step.id, [{"id": 1, "qty": None}, {"id": 1, "qty": 5}])
        assert params["rows"] == [{"id": 1, "qty": None}, {"id": 1, "qty": 5}]
        return result(step.id, [{"cost": 5}])

    results = await execute_plan(active, meaning, execute)
    assert set(results) == {"a", "b", "c"}


async def test_empty_binding_still_executes_count(meaning):
    active = plan()
    second = source("b")
    second.depends_on = ["s"]
    active.steps.append(second)
    active.sections[0].input = "b"

    async def execute(step, params):
        return result(step.id, [] if step.id == "s" else [{"cost": 0}], ["cost"])

    results = await execute_plan(active, meaning, execute)
    assert results["b"].rows == [{"cost": 0}]


@pytest.mark.parametrize(
    "query, verdict",
    [
        ("SELECT cost FROM public.items WHERE cost >= 5", "supported"),
        ("SELECT cost FROM public.items WHERE cost > 5", "unknown"),
        ("SELECT cost FROM public.items WHERE cost >= 5 OR cost IS NULL", "unknown"),
        ("SELECT cost FROM public.items WHERE cost >= 5 AND cost < 20", "supported"),
    ],
)
def test_ast_checks_operator_and_boolean_scope(meaning, knowledge, query, verdict):
    assert (
        inspect_sql(source(query=query), meaning.requirements[0], knowledge)["verdict"]
        == verdict
    )


def test_alias_adaptation_uses_expression_not_result_values():
    mapped = adapt_strict_plan(
        plan(),
        {"sql_query": "SELECT i.cost AS other FROM public.items i WHERE i.cost >= 5"},
    )
    assert mapped.sections[0].projections[0].column == "other"
    with pytest.raises(PlanError):
        adapt_strict_plan(plan(), {"sql_query": "SELECT 5 AS cost FROM public.items"})


def test_named_instance_cannot_use_numeric_field(meaning, knowledge):
    meaning.mentions = [
        Mention(text="단가", kind="name", source_field="sql:public.items.cost")
    ]
    meaning = Meaning.model_validate(meaning.model_dump())
    with pytest.raises(PlanError, match="named instance"):
        validate_meaning(meaning, "단가 5 이상", knowledge)


async def test_review_cannot_reject_without_specific_issue(
    monkeypatch, meaning, knowledge
):
    review = PlanReview.model_validate(
        {
            "checks": [
                {
                    "requirement_id": "__original_question__",
                    "step_id": "s",
                    "verdict": "unknown",
                    "query_excerpt": "SELECT",
                    "explanation": "uncertain",
                }
            ],
            "issues": [],
        }
    )
    mock = AsyncMock(return_value=review)
    monkeypatch.setattr("orchestrator.grounded.plan_validation.typed_call", mock)
    report = await validate_execution(
        None,
        "단가 5 이상",
        meaning,
        plan(),
        {"s": result("s", [{"cost": 6}])},
        knowledge,
    )
    assert not report["accepted"] and report["status"] == "review_invalid"
    assert mock.await_count == 2


async def test_review_accepts_evidence_without_global_boolean(
    monkeypatch, meaning, knowledge
):
    review = PlanReview.model_validate(
        {
            "checks": [
                {
                    "requirement_id": "__original_question__",
                    "step_id": "s",
                    "verdict": "supported",
                    "query_excerpt": "WHERE cost >= 5",
                    "explanation": "original condition retained",
                }
            ],
            "issues": [],
        }
    )
    monkeypatch.setattr(
        "orchestrator.grounded.plan_validation.typed_call",
        AsyncMock(return_value=review),
    )
    report = await validate_execution(
        None,
        "단가 5 이상",
        meaning,
        plan(),
        {"s": result("s", [{"cost": 6}])},
        knowledge,
    )
    assert report["accepted"]


async def test_budget_counts_concurrent_sdk_calls_without_sdk_retries():
    sdk = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value="ok"))
        )
    )
    sdk.with_options = lambda **kwargs: sdk
    budget = RequestBudget(calls=2)
    client = BudgetClient(sdk, budget)
    assert await asyncio.gather(client.create(), client.create()) == ["ok", "ok"]
    with pytest.raises(BudgetExceededError):
        await client.create()
    assert sdk.chat.completions.create.await_count == 2


def test_final_result_and_answer_share_ids_rows_and_nulls(meaning):
    data = final_result(
        plan(), meaning, {"s": result("s", [{"cost": None}, {"cost": 6}])}
    )
    answer, references = render_result(data, meaning)
    assert data.sections[0].rows == [{"value": None}, {"value": 6}]
    assert "NULL" in answer and "6" in answer
    assert all(r["fields"] == ["value"] for r in references)


def test_cycles_and_missing_output_do_not_execute(meaning):
    active = plan()
    active.steps[0].depends_on = ["s"]
    with pytest.raises(PlanError):
        validate_plan(active, meaning)
    active = plan()
    active.sections[0].projections = []
    with pytest.raises(PlanError):
        validate_plan(active, meaning)


def test_predicate_nested_field_must_exist(meaning, knowledge):
    meaning.requirements[0].predicate.field = "sql:public.items.invented"
    with pytest.raises(PlanError, match="unavailable physical field"):
        validate_meaning(meaning, "단가 5 이상", knowledge)


@pytest.mark.parametrize(
    "arrow,maximum,expected", [("->", 3, True), ("-", 3, False), ("->", 2, False)]
)
def test_graph_shape_is_partial_evidence_only(arrow, maximum, expected):
    from orchestrator.grounded.plan_validation import inspect_graph_relationships

    step = source(query=f"MATCH (a:Item)-[:PART*1..{maximum}]{arrow}(b:Item) RETURN b")
    step.tool = "graph"
    step.relationships = [
        Relationship(
            source_label="Item",
            relationship="PART",
            target_label="Item",
            direction="out",
            min_hops=1,
            max_hops=3,
        )
    ]
    check = inspect_graph_relationships(step)[0]
    assert check["structural_match"] is expected
    assert check["verdict"] == "unknown"


def test_graph_literal_cannot_be_relationship_evidence():
    from orchestrator.grounded.plan_validation import inspect_graph_relationships

    step = source(query="RETURN '(a:Item)-[:PART*1..3]->(b:Item)' AS text")
    step.relationships = [
        Relationship(
            source_label="Item",
            relationship="PART",
            target_label="Item",
            direction="out",
            min_hops=1,
            max_hops=3,
        )
    ]
    assert not inspect_graph_relationships(step)[0]["structural_match"]


def test_legacy_capability_does_not_admit_unproven_aggregate(meaning):
    from orchestrator.grounded.strict_adapter import strict_eligible

    active = plan()
    active.steps[0].query = "SELECT COUNT(*) AS cost FROM public.items"
    catalog = SimpleNamespace(
        by_tool={"sql": {"cost": SimpleNamespace(schema_paths=("public.items.cost",))}}
    )
    assert not strict_eligible(meaning, active, catalog)


async def test_reviewer_may_include_known_ast_supported_requirement(
    monkeypatch, meaning, knowledge
):
    from orchestrator.grounded.plan_models import ReviewedCheck

    review = PlanReview(
        checks=[
            ReviewedCheck(
                requirement_id=key,
                step_id="s",
                verdict="supported",
                query_excerpt="WHERE cost >= 5",
                explanation="condition retained",
            )
            for key in ["r", "__original_question__"]
        ],
        issues=[],
    )
    monkeypatch.setattr(
        "orchestrator.grounded.plan_validation.typed_call",
        AsyncMock(return_value=review),
    )
    report = await validate_execution(
        None,
        "단가 5 이상",
        meaning,
        plan(),
        {"s": result("s", [{"cost": 6}])},
        knowledge,
    )
    assert report["accepted"] and report["review_attempts"] == 1
