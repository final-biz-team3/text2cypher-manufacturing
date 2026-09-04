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
    async def create(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=30,
                total_tokens=150,
                prompt_tokens_details=SimpleNamespace(
                    cached_tokens=80,
                    cache_write_tokens=20,
                ),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
            )
        )


class _FakeCompletionsWithoutUsage:
    async def create(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace()


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
    assert client.snapshot()["modelTokenUsage"] == {
        "reportedCallCount": 1,
        "promptTokens": 120,
        "cachedPromptTokens": 80,
        "cacheWritePromptTokens": 20,
        "completionTokens": 30,
        "reasoningTokens": 12,
        "totalTokens": 150,
    }
    client.reset_case()
    assert client.snapshot() == {
        "modelCallCount": 0,
        "modelElapsedMs": 0.0,
        "modelTokenUsage": {
            "reportedCallCount": 0,
            "promptTokens": 0,
            "cachedPromptTokens": 0,
            "cacheWritePromptTokens": 0,
            "completionTokens": 0,
            "reasoningTokens": 0,
            "totalTokens": 0,
        },
    }


@pytest.mark.asyncio
async def test_counting_client_tolerates_responses_without_usage() -> None:
    raw = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletionsWithoutUsage())
    )
    client = CountingOpenAIClient(raw)

    await client.chat.completions.create(model="test")

    assert client.snapshot()["modelCallCount"] == 1
    assert client.snapshot()["modelTokenUsage"]["reportedCallCount"] == 0


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
