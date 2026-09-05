"""실제 재시도 흐름의 계측, 이벤트, 마스킹 계약을 검증한다."""

import json
import logging

import pytest
from prometheus_client import CollectorRegistry, Counter

import orchestrator.subgraphs.retry_agent as retry_module
from orchestrator.guards.result import GuardResult
from orchestrator.nodes.execute_plan import _result_summary
from orchestrator.subgraphs.retry_agent import make_retry_agent_subgraph


@pytest.fixture
def telemetry(monkeypatch):
    registry = CollectorRegistry()
    counters = {}
    for name, labels in (
        ("QUERY_ATTEMPTS", ("tool", "issue_code", "outcome")),
        ("REPAIRS", ("tool", "issue_code", "outcome", "engine")),
        ("REPAIR_EXHAUSTED", ("tool", "issue_code")),
    ):
        counter = Counter(name.lower(), name, labels, registry=registry)
        counters[name] = counter
        monkeypatch.setattr(retry_module, name, counter)
    events = []
    monkeypatch.setattr(
        retry_module,
        "emit_event",
        lambda name, category, **fields: events.append({"name": name, **fields}),
    )
    monkeypatch.setenv("OBS_LOG_FAILED_QUERY", "true")
    return counters, events


def _total(counter: Counter) -> float:
    return sum(
        sample.value
        for metric in counter.collect()
        for sample in metric.samples
        if sample.name.endswith("_total")
    )


@pytest.mark.parametrize(
    "label,tool", [("sql_agent", "sql"), ("cypher_agent", "graph")]
)
@pytest.mark.parametrize(
    "outcomes,blocked,expected_attempts,expected_repairs,expected_exhausted,empty_reason",
    [
        (["ok"], False, 1, 0, 0, None),
        (["retry", "ok"], False, 2, 1, 0, None),
        (["retry"] * 3, False, 3, 1, 1, None),
        (["connection"], False, 1, 0, 0, None),
        (["internal"], False, 1, 0, 0, None),
        ([], True, 1, 0, 0, None),
        (["empty", "ok"], False, 2, 1, 0, None),
        (["empty", "empty"], False, 2, 0, 0, "NO_DATA"),
        (["retry", "empty", "empty"], False, 3, 0, 0, "INCONCLUSIVE"),
    ],
)
async def test_retry_flow_emits_each_attempt_and_masks_queries(
    telemetry,
    label,
    tool,
    outcomes,
    blocked,
    expected_attempts,
    expected_repairs,
    expected_exhausted,
    empty_reason,
):
    counters, events = telemetry
    feedback = []
    executed: list[str] = []
    query = "SELECT 'query-private-value', 17747"
    exact_error = "DB_PRIVATE_ERROR: internal_table.hidden_column = 'db-private-value'"

    async def generate(state, previous_query, previous_error):
        feedback.append(previous_error)
        return query

    async def execute(query):
        outcome = outcomes[len(executed)]
        executed.append(query)
        if outcome == "retry":
            raise SyntaxError(exact_error)
        if outcome == "connection":
            raise ConnectionError(exact_error)
        if outcome == "internal":
            raise RuntimeError(exact_error)
        return [] if outcome == "empty" else [{"value": 1}]

    agent = make_retry_agent_subgraph(
        logger=logging.getLogger(__name__),
        label=label,
        generate=generate,
        execute=execute,
        connection_exceptions=(ConnectionError,),
        retryable_exceptions=(SyntaxError,),
        empty_result_feedback="EMPTY FEEDBACK",
        guard=(
            (lambda _: GuardResult(False, "UNKNOWN_TABLE", exact_error))
            if blocked
            else None
        ),
    )
    result = await agent.ainvoke(
        {"query": "조회", "entity": None, "schema": "", "messages": [], "error": None}
    )

    assert len(feedback) == expected_attempts
    assert len(executed) == (0 if blocked else expected_attempts)
    assert _total(counters["QUERY_ATTEMPTS"]) == expected_attempts
    assert _total(counters["REPAIRS"]) == expected_repairs
    assert _total(counters["REPAIR_EXHAUSTED"]) == expected_exhausted
    assert result.get("empty_reason") == empty_reason
    for name in ("query.generated", "query.attempt.started", "query.attempt.completed"):
        matching = [event for event in events if event["name"] == name]
        assert [event["attempt"] for event in matching] == list(
            range(1, expected_attempts + 1)
        )
        assert all(event["tool"] == tool for event in matching)
    decisions = [event for event in events if event["name"] == "repair.decision.made"]
    assert all(
        event["outcome"]
        == ("success" if event["issue_code"] == "EMPTY_RESULT" else "failure")
        for event in decisions
    )
    assert (
        sum(event["decision"] == "retry" for event in decisions)
        == expected_attempts - 1
    )
    names = [event["name"] for event in events]
    assert names.count("repair.exhausted") == expected_exhausted
    assert names.count("repair.completed") == expected_repairs - expected_exhausted
    if empty_reason:
        assert not any(item["recovered"] for item in result["retryDiagnostics"])
    if outcomes == ["retry", "empty", "empty"]:
        assert feedback == [None, exact_error, "EMPTY FEEDBACK"]

    summary = _result_summary(result)
    assert summary["generated_query"] == "SELECT [VALUE], [VALUE]"
    if blocked or any(item in outcomes for item in ("retry", "connection", "internal")):
        assert summary["failed_query"] == "SELECT [VALUE], [VALUE]"
    assert "retry_feedback" not in summary
    serialized = json.dumps(events)
    for private in (
        "query-private-value",
        "17747",
        "DB_PRIVATE_ERROR",
        "db-private-value",
    ):
        assert private not in serialized


async def test_query_capture_can_be_disabled(telemetry, monkeypatch):
    _, events = telemetry
    monkeypatch.setenv("OBS_LOG_FAILED_QUERY", "false")

    async def generate(state, previous_query, previous_error):
        return "SELECT 'private-value'"

    async def execute(query):
        raise SyntaxError("private-error")

    result = await make_retry_agent_subgraph(
        logger=logging.getLogger(__name__),
        label="sql_agent",
        generate=generate,
        execute=execute,
        connection_exceptions=(),
        retryable_exceptions=(SyntaxError,),
        empty_result_feedback="EMPTY",
    ).ainvoke(
        {"query": "조회", "entity": None, "schema": "", "messages": [], "error": None}
    )

    assert result["failed_query"] is None
    assert result["generated_query"] is None
    assert all(event.get("failed_query") is None for event in events)
    assert all(event.get("generated_query") is None for event in events)
