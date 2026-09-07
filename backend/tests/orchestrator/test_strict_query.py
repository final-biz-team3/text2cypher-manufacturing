"""이전 질의 경로의 수용·격리·실패 전환과 실제 graph 배선을 검증한다."""

import asyncio
from copy import deepcopy

import pytest

import orchestrator.graph as graph_module
import orchestrator.nodes.strict_query as strict_module
from orchestrator.errors import EntityNotFoundError
from tests.mocks.openai import MockOpenAIClient


def result():
    return {
        "entity": None,
        "composed_result": {
            "mode": "single",
            "rows": [{"stock": 10}],
            "sections": {},
            "error": None,
            "empty_reason": None,
            "total_count": 1,
            "truncated": False,
        },
        "query_failure": None,
    }


def install(monkeypatch, callback):
    monkeypatch.setattr(strict_module, "build_strict_query", lambda *a, **kw: callback)
    return strict_module.make_strict_query_node(None, None)


async def test_accepts_only_successful_query_state_not_legacy_answer(monkeypatch):
    async def run(state):
        assert state == {"query": "질문", "confirmed_entity": {"productId": 1}}
        return {**result(), "final_answer": "OLD_ANSWER", "unknown": "SECRET"}

    node = install(monkeypatch, run)
    answer = await node({"query": "질문", "confirmed_entity": {"productId": 1}})
    assert answer["query_strategy"] == "strict"
    assert answer["composed_result"]["rows"] == [{"stock": 10}]
    assert "final_answer" not in answer and "unknown" not in answer


@pytest.mark.parametrize(
    "condition", ["query_failure", "composition_failure", "empty_result", "truncated"]
)
async def test_invalid_result_discards_all_partial_state(monkeypatch, condition):
    candidate = result()
    candidate["entity"] = {"productId": 999}
    candidate["sql_query"] = "STALE_QUERY"
    if condition == "query_failure":
        candidate["query_failure"] = {"code": "FAILED"}
    if condition == "composition_failure":
        candidate["composed_result"]["error"] = "FAILED"
    if condition == "empty_result":
        candidate["composed_result"]["rows"] = []
    if condition == "truncated":
        candidate["composed_result"]["truncated"] = True

    async def run(_):
        return candidate

    answer = await install(monkeypatch, run)({"query": "질문"})
    assert set(answer) == {"query_strategy", "strict_attempt"}
    assert answer["query_strategy"] == "pr61"
    assert answer["strict_attempt"]["reason"] == condition


async def test_exception_falls_back_without_leaking_details(monkeypatch):
    async def run(_):
        raise EntityNotFoundError("PRIVATE_NAME")

    answer = await install(monkeypatch, run)({"query": "질문"})
    assert answer["strict_attempt"]["reason"] == "EntityNotFoundError"
    assert "PRIVATE_NAME" not in str(answer)


async def test_legacy_initialization_error_does_not_break_pr61(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("invalid historical schema")

    monkeypatch.setattr(strict_module, "build_strict_query", broken)
    node = strict_module.make_strict_query_node(None, None)
    answer = await node({"query": "질문"})
    assert answer["query_strategy"] == "pr61"
    assert answer["strict_attempt"]["reason"] == "initialization:ValueError"


async def test_failed_strict_lookup_cannot_mutate_confirmed_entity(monkeypatch):
    async def run(state):
        state["confirmed_entity"]["productId"] = 999
        raise ValueError("failed")

    state = {"query": "질문", "confirmed_entity": {"productId": 1}}
    answer = await install(monkeypatch, run)(state)
    assert answer["query_strategy"] == "pr61"
    assert state["confirmed_entity"] == {"productId": 1}


async def test_timeout_cancels_strict_work(monkeypatch):
    cancelled = asyncio.Event()

    async def run(_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setenv("STRICT_QUERY_TIMEOUT_SECONDS", "0.01")
    answer = await install(monkeypatch, run)({"query": "질문"})
    assert answer["strict_attempt"]["reason"] == "timeout"
    assert cancelled.is_set()


async def test_caller_cancellation_is_not_swallowed(monkeypatch):
    async def run(_):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await install(monkeypatch, run)({"query": "질문"})


async def test_shared_write_guard_prevents_both_query_paths(monkeypatch):
    async def never(_):
        pytest.fail("쓰기 요청은 질의 경로에 도달하면 안 된다")

    monkeypatch.setattr(strict_module, "build_strict_query", lambda *a, **kw: never)
    client = MockOpenAIClient()
    state = await graph_module.build_orchestrator_graph(client, None).ainvoke(
        {"query": "제품을 삭제해줘"}
    )
    assert state["query_failure"]["code"] == "REQUEST_POLICY_BLOCKED"
    assert "query_strategy" not in state
    assert not client.calls


@pytest.mark.parametrize("strict_succeeds", [True, False])
async def test_graph_uses_pr61_answer_once_and_falls_back_from_clean_input(
    monkeypatch, strict_succeeds
):
    calls = []

    async def allowed(_):
        return {"query_failure": None}

    monkeypatch.setattr(graph_module, "make_guard_request_node", lambda: allowed)
    monkeypatch.setattr(graph_module, "make_classify_topic_node", lambda _: allowed)

    async def legacy(state):
        calls.append("strict")
        if not strict_succeeds:
            state["entity"] = {"productId": 999}
            raise ValueError("bad legacy plan")
        return result()

    monkeypatch.setattr(strict_module, "build_strict_query", lambda *a, **kw: legacy)

    async def resolve(state):
        calls.append("pr61")
        assert not state.get("entity") and not state.get("composed_result")
        return {"entity": None}

    async def noop(_):
        return {}

    async def compose(_):
        return result()

    async def answer(state):
        calls.append("answer")
        assert state["composed_result"]["rows"] == [{"stock": 10}]
        return {"final_answer": "PR61_ANSWER"}

    monkeypatch.setattr(
        graph_module, "make_resolve_entity_node", lambda *a, **kw: resolve
    )
    monkeypatch.setattr(graph_module, "make_route_query_node", lambda *a, **kw: noop)
    monkeypatch.setattr(graph_module, "make_plan_outputs_node", lambda *a, **kw: noop)
    monkeypatch.setattr(graph_module, "make_execute_plan_node", lambda *a, **kw: noop)
    monkeypatch.setattr(
        graph_module, "make_compose_results_node", lambda *a, **kw: compose
    )
    monkeypatch.setattr(
        graph_module, "make_generate_answer_node", lambda *a, **kw: answer
    )
    input_state = {"query": "질문"}
    before = deepcopy(input_state)
    output = await graph_module.build_orchestrator_graph(
        MockOpenAIClient(), None
    ).ainvoke(input_state)
    assert calls == (
        ["strict", "answer"] if strict_succeeds else ["strict", "pr61", "answer"]
    )
    assert output["final_answer"] == "PR61_ANSWER"
    assert input_state == before
