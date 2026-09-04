from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from evaluation.models import load_manifest
from evaluation.normalization import normalized_sha256
from evaluation.observability import CountingOpenAIClient
from evaluation.runner import EvaluationRunner

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _FakeCompletions:
    async def create(self, *args: Any, **kwargs: Any) -> str:
        return "ok"


class _FakeGraph:
    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = updates

    async def astream(self, *args: Any, **kwargs: Any) -> Any:
        for update in self.updates:
            yield update


@pytest.mark.asyncio
async def test_counting_client_measures_chat_completion_calls() -> None:
    raw = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    client = CountingOpenAIClient(raw)

    await client.chat.completions.create(model="test")

    assert client.call_count == 1
    assert client.model_elapsed_ms >= 0
    client.reset_case()
    assert client.snapshot() == {
        "modelCallCount": 0,
        "modelElapsedMs": 0.0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cachedInputTokens": 0,
        "cacheWriteTokens": 0,
        "reasoningTokens": 0,
        "estimatedCostUsd": 0.0,
    }


@pytest.mark.asyncio
async def test_counting_client_records_tokens_and_estimated_cost() -> None:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=40, cache_write_tokens=10),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
    )

    class _UsageCompletions:
        async def create(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(usage=usage)

    raw = SimpleNamespace(chat=SimpleNamespace(completions=_UsageCompletions()))
    client = CountingOpenAIClient(raw)

    await client.chat.completions.create(model="gpt-5.6-luna")

    snapshot = client.snapshot()
    assert snapshot["inputTokens"] == 100
    assert snapshot["outputTokens"] == 20
    assert snapshot["cachedInputTokens"] == 40
    assert snapshot["cacheWriteTokens"] == 10
    assert snapshot["reasoningTokens"] == 5
    assert snapshot["estimatedCostUsd"] == pytest.approx(0.0000373)


def test_orchestrator_mode_scores_production_state_and_attempts() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ03")
    expected = manifest.contracts[case.contract_id].subqueries[0]
    plan = expected.planning_shape()
    rows = [{"activeSupplierCount": 104}]

    runner = object.__new__(EvaluationRunner)
    runner.manifest = manifest
    runner.execution_mode = "orchestrator"
    raw_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    runner.openai_client = CountingOpenAIClient(raw_client)
    runner.orchestrator_graph = _FakeGraph(
        [
            {"resolve_entity": {"entity": None}},
            {
                "route_query": {
                    "tool_plan": ["sql"],
                    "subqueries": [plan],
                }
            },
            {
                "execute_plan": {
                    "sql_query": "SELECT 104 AS activeSupplierCount",
                    "sql_result": {
                        "result": rows,
                        "error": None,
                        "attempts": [
                            {
                                "query": "SELECT 104 AS activeSupplierCount",
                                "error": None,
                            }
                        ],
                        "empty_reason": None,
                    },
                }
            },
            {
                "compose_results": {
                    "composed_result": {
                        "mode": "single",
                        "rows": rows,
                        "sections": {},
                        "error": None,
                        "empty_reason": None,
                        "total_count": 1,
                        "truncated": False,
                    }
                }
            },
        ]
    )

    def gold_result(
        self: EvaluationRunner, expected: Any, parameters: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        return rows, normalized_sha256(rows)

    runner._gold_result = MethodType(gold_result, runner)  # type: ignore[method-assign]

    record = runner._evaluate_case(case, 1)

    assert record["status"] == "PASS"
    assert record["firstAttemptExecutionPass"] is True
    assert record["recoveredByRetry"] is False
    assert record["attemptCount"] == 1
    assert record["sourceResultPass"] is True
    assert record["composedResultPass"] is True
    assert record["failureStage"] is None
    assert record["modelCallCount"] == 0
    assert record["elapsedMs"] >= 0
