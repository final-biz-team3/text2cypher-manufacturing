# General Query Route Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third `general` route to the orchestrator so domain-unrelated questions (greetings, small talk, off-topic requests) get a fixed 200 answer instead of either a forced sql/graph plan or an unhandled 500, and make the existing `RoutePlanError` (a real routing failure) return 422 instead of leaking as an unhandled 500.

**Architecture:** `route_query`'s LLM prompt gains a third `general` option; `orchestrator/planning.py`'s `parse_execution_plan` short-circuits validation when `tool_plan == ["general"]`; a new `answer_general` node returns a fixed Korean string; `orchestrator/graph.py` adds a conditional edge after `route_query` that skips straight to `answer_general` (bypassing `execute_plan`/`compose_results`) when the route is `general`. Separately, `RoutePlanError` moves from `orchestrator/nodes/route_query.py` into `orchestrator/errors.py` and becomes an `AppError` subclass so `main.py`'s existing `@app.exception_handler(AppError)` catches it.

**Tech Stack:** Python 3.12, FastAPI, LangGraph (`StateGraph`/conditional edges), pytest + pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-09-01-general-query-route-design.md](../specs/2026-09-01-general-query-route-design.md) — the plan argues from this spec; read both.

## Global Constraints

- `SUPPORTED_TOOLS = {"sql", "graph"}` in `orchestrator/planning.py` must NOT include `"general"` — it stays a separate constant (`GENERAL_ROUTE`) so a subquery's `tool` field can never be `"general"`.
- `general` is only valid alone (`tool_plan == ["general"]`); mixed with `sql`/`graph` it must still be rejected by the existing `unsupported_tools` check.
- `execute_plan.py`/`compose_results.py`/`generate_answer.py` are never modified — the general route bypasses them entirely at the graph level.
- The fixed general-route answer text is exactly:
  ```
  제조 데이터와 관련된 질문을 입력해 주세요.
  제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다.
  ```
  (two lines, joined with `\n`, no trailing period changes, no other wording).
- `RoutePlanError`'s new `AppError` fields: `status_code=422`, `code="ROUTE_PLAN_ERROR"`, `message="질문을 처리할 계획을 세우지 못했습니다. 질문을 더 구체적으로 입력해 주세요."`.
- No frontend changes — `ChatResponse`'s fields are already all optional.

---

## Task 1: `general` short-circuit in `orchestrator/planning.py`

**Files:**
- Modify: `backend/orchestrator/planning.py:6` (add constant), `backend/orchestrator/planning.py:209-215` (insert early-return)
- Test: `backend/tests/orchestrator/test_planning.py` (new file — no existing direct unit tests for `parse_execution_plan`; today it's only exercised indirectly through `test_route_query.py`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `GENERAL_ROUTE: str = "general"` (module-level constant in `orchestrator/planning.py`), importable as `from orchestrator.planning import GENERAL_ROUTE`. `parse_execution_plan(content: str, query: str) -> ExecutionPlan` behavior: when the parsed `tool_plan == ["general"]`, returns `{"tool_plan": ["general"], "subqueries": []}` without validating `subqueries`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/orchestrator/test_planning.py`:

```python
"""parse_execution_plan이 general 라우트를 검증 없이 통과시키는 동작을 테스트한다."""

import pytest

from orchestrator.planning import parse_execution_plan


def test_parse_execution_plan_accepts_general_route_with_empty_subqueries() -> None:
    """tool_plan이 ["general"]이면 subqueries가 비어 있어도 통과한다."""
    result = parse_execution_plan(
        '{"tool_plan":["general"],"subqueries":[]}', "안녕하세요"
    )

    assert result == {"tool_plan": ["general"], "subqueries": []}


def test_parse_execution_plan_accepts_general_route_without_subqueries_key() -> None:
    """subqueries 키 자체가 없어도(LLM이 아예 생략해도) general은 통과한다."""
    result = parse_execution_plan('{"tool_plan":["general"]}', "기분이 어때요?")

    assert result == {"tool_plan": ["general"], "subqueries": []}


def test_parse_execution_plan_rejects_general_mixed_with_other_tools() -> None:
    """general은 다른 도구와 섞일 수 없다."""
    with pytest.raises(ValueError, match="지원하지 않는 tool_plan 값"):
        parse_execution_plan('{"tool_plan":["sql","general"],"subqueries":[]}', "질의")


def test_parse_execution_plan_still_requires_subqueries_for_sql_route() -> None:
    """general이 아닌 기존 라우트는 회귀 없이 그대로 빈 subqueries를 거부한다."""
    with pytest.raises(ValueError, match="비어 있지 않은 배열"):
        parse_execution_plan('{"tool_plan":["sql"],"subqueries":[]}', "질의")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_planning.py -v` (from `backend/`)
Expected: the two `general` tests FAIL — `test_parse_execution_plan_accepts_general_route_with_empty_subqueries` and `..._without_subqueries_key` both raise `ValueError: 지원하지 않는 tool_plan 값: general` (since `"general"` isn't in `SUPPORTED_TOOLS` yet). The other two tests should already PASS (they exercise unchanged behavior) — that's fine, they're regression guards, not new-behavior tests.

- [ ] **Step 3: Implement the minimal change**

In `backend/orchestrator/planning.py`, change line 6:

```python
SUPPORTED_TOOLS = {"sql", "graph"}
GENERAL_ROUTE = "general"
```

Then in `parse_execution_plan`, insert the early-return **between** the duplicate-check and the `unsupported_tools` check (current lines 210-212):

```python
    tool_plan: list[str] = list(raw_tool_plan)
    if len(tool_plan) != len(set(tool_plan)):
        raise ValueError("tool_plan에는 같은 도구를 중복 지정할 수 없습니다.")
    if tool_plan == [GENERAL_ROUTE]:
        return {"tool_plan": tool_plan, "subqueries": []}
    unsupported_tools = set(tool_plan) - SUPPORTED_TOOLS
    if unsupported_tools:
        names = ", ".join(sorted(unsupported_tools))
        raise ValueError(f"지원하지 않는 tool_plan 값: {names}")
```

**This ordering is required**: `"general"` is not in `SUPPORTED_TOOLS`, so if the early-return came after the `unsupported_tools` check, `["general"]` would always be rejected as an unsupported tool before ever reaching the early-return.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_planning.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full existing route_query/planning-adjacent suite for regressions**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_route_query.py tests/orchestrator/test_execute_plan.py tests/orchestrator/test_composition.py -v`
Expected: all PASS unchanged (this task doesn't touch `route_query.py` yet).

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/planning.py backend/tests/orchestrator/test_planning.py
git commit -m "Feat: general tool_plan이 subquery 검증을 건너뛰도록 parse_execution_plan 확장"
```

---

## Task 2: `general` option in `route_query`'s prompt

**Files:**
- Modify: `backend/orchestrator/nodes/route_query.py:51-82` (system prompt only — no code-logic changes)
- Test: `backend/tests/orchestrator/test_route_query.py` (add tests)

**Interfaces:**
- Consumes: `parse_execution_plan` from Task 1 (already handles `general` transparently — no new code path in `route_query.py` itself, since `parse_execution_plan` either returns a plan or raises `ValueError`/`RoutePlanError` exactly as before).
- Produces: nothing new for other tasks — this task only changes prompt text and adds tests proving the node passes `general` through unmodified and doesn't distort the LLM's sql/graph judgment on boundary cases.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/orchestrator/test_route_query.py` (after the existing `test_route_query_returns_graph_tool_plan_for_relationship_query` test, i.e. anywhere in the file — order doesn't matter for pytest):

```python
async def test_route_query_returns_general_tool_plan_for_off_topic_query() -> None:
    """도메인과 무관한 질문은 general로 분류되고 예외 없이 통과한다."""
    openai_client = MockOpenAIClient(
        make_content_response('{"tool_plan":["general"],"subqueries":[]}')
    )
    node = make_route_query_node(openai_client)

    result = await node({"query": "오늘 날씨가 어때요?", "entity": None})

    assert result == {"tool_plan": ["general"], "subqueries": []}
    assert len(openai_client.calls) == 1


async def test_route_query_keeps_domain_route_when_query_starts_with_greeting() -> None:
    """인사말로 시작해도 실제 도메인 질문이면 LLM이 고른 sql 라우트를 코드가
    general로 강제 변환하지 않는다(경계 케이스 회귀 테스트)."""
    raw_response = (
        '{"tool_plan":["sql"],"subqueries":[{"id":"sql_low_stock","tool":"sql",'
        '"question":"재고가 부족한 제품을 조회한다.","dependsOn":[],'
        '"requiredOutputs":[],"joinKeys":[]}]}'
    )
    openai_client = MockOpenAIClient(make_content_response(raw_response))
    node = make_route_query_node(openai_client)

    result = await node(
        {"query": "안녕하세요, 재고가 부족한 제품 좀 알려주세요.", "entity": None}
    )

    assert result["tool_plan"] == ["sql"]


async def test_route_query_honors_general_even_with_confirmed_entity() -> None:
    """confirmed entity가 있어도 LLM이 general을 반환하면 그대로 통과시킨다
    (코드가 LLM 판단을 임의로 덮어쓰지 않는다는 걸 못박는 테스트)."""
    openai_client = MockOpenAIClient(
        make_content_response('{"tool_plan":["general"],"subqueries":[]}')
    )
    node = make_route_query_node(openai_client)

    result = await node(
        {
            "query": "그냥 잡담하고 싶어요.",
            "entity": {"productId": 680, "productName": "LL Road Frame"},
        }
    )

    assert result == {"tool_plan": ["general"], "subqueries": []}
```

- [ ] **Step 2: Run tests to verify the new ones fail for the right reason**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_route_query.py -v`
Expected: `test_route_query_returns_general_tool_plan_for_off_topic_query` and `test_route_query_honors_general_even_with_confirmed_entity` currently PASS already (Task 1 already made `parse_execution_plan` accept `general` — this task only changes the *prompt*, not `parse_execution_plan`'s behavior). `test_route_query_keeps_domain_route_when_query_starts_with_greeting` also already passes (it only exercises existing sql-routing code, no `general` involved). **These tests are regression guards for the prompt change, not new-behavior tests that must fail first** — the actual new "behavior" here is the prompt text itself, which isn't independently unit-testable (it's tested by the LLM's real judgment in production, not by these mocked-response tests). Confirm all 3 pass now, then proceed to the prompt edit; re-run afterward to confirm nothing broke.

- [ ] **Step 3: Update the system prompt**

In `backend/orchestrator/nodes/route_query.py`, replace the `_SYSTEM_PROMPT` constant (currently lines 51-82) with:

```python
_SYSTEM_PROMPT = """당신은 제조 데이터 질의 라우터입니다.
사용자 질문과 확인된 entity를 보고 어떤 Tool을 실행해야 하는지 결정합니다.
반드시 아래 Tool 중에서만 선택하고 JSON 객체로 반환합니다.

Tool 목록:
- sql: 수치 조회, 집계, 재고 계산, 가격, 수량, 날짜 비교가 필요한 질의
- graph: 제품-부품-공급업체-공정 간 다단계 관계 탐색이 필요한 질의
- general: 제조 데이터 조회·분석과 무관한 질문(인사, 날씨·감정 등 잡담, 시스템 정체성 질문, 시스템 능력 밖의 요청 등)

예시:
Q: "LL Road Frame의 정가와 표준원가를 알려줘."
entity: {"productId": 680}
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_product_cost","tool":"sql","question":"LL Road Frame의 정가와 표준원가를 알려줘.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}

Q: "부품 Blade를 사용하는 완제품을 최대 4단계까지 알려줘."
entity: {"productId": 316}
A: {"tool_plan":["graph"],"subqueries":[{"id":"graph_impact","tool":"graph","question":"부품 Blade를 사용하는 완제품 경로를 최대 4단계까지 조회한다.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}

Q: "공급업체 Cycling Master가 공급을 중단하면 영향받는 완제품과 현재 부품 재고를 알려줘."
entity: {"supplierId": 52}
A: {"tool_plan":["graph","sql"],"subqueries":[{"id":"graph_impact","tool":"graph","question":"활성 공급업체 Cycling Master의 공급 부품과 영향 완제품 경로를 조회한다.","dependsOn":[],"requiredOutputs":["componentId"],"joinKeys":["componentId"]},{"id":"sql_stock","tool":"sql","question":"앞 단계에서 확인한 부품들의 현재 재고를 조회한다.","dependsOn":["graph_impact"],"inputBindings":{"componentIds":"graph_impact.componentId"},"requiredOutputs":["componentId"],"joinKeys":["componentId"]}]}

Q: "안녕하세요"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "오늘 날씨가 어때요?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "기분이 어때요?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "요즘 재밌는 영화 뭐 있어?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "당신은 누구인가요?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "안녕하세요, 재고가 부족한 제품 좀 알려주세요."
entity: null
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_low_stock","tool":"sql","question":"재고가 부족한 제품을 조회한다.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}

Q: "LL Road Frame 어때요?"
entity: {"productId": 680}
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_product_info","tool":"sql","question":"LL Road Frame의 정보를 조회한다.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}

규칙:
- 단일 SQL/GRAPH 질문도 subquery를 정확히 1개 만들고 question에 원래 질문의 의미를 보존한다.
- 복합 질문은 데이터 소스의 책임별로 나누고 dependsOn, inputBindings, requiredOutputs, joinKeys를 명시한다.
- requiredOutputs에는 다른 단계로 전달하거나 최종 결합에 실제로 필요한 필드만 쓴다. 단일 질의처럼 전달·결합이 없으면 빈 배열로 둔다.
- HYBRID의 전달 필드와 최종 결합 키는 해당 단계의 requiredOutputs와 joinKeys 둘 다에 넣는다.
- 선행 결과가 필요하지 않은 두 단계는 dependsOn을 빈 배열로 둔다.
- id는 sql_stock, graph_impact처럼 책임을 나타내며 질문에 없는 RQ 번호를 사용하지 않는다.
- inputBindings 값은 반드시 "선행단계ID.출력필드" 형식이다.
- tool_plan은 실제 의존 실행 순서로 쓰고 각 도구는 한 번만 포함한다.
- 제조 데이터 조회·분석과 무관한 질문(인사, 날씨·감정 등 잡담, 시스템 정체성 질문, 시스템 능력 밖의 요청 등)이면 다른 Tool 없이 general만 반환하고 subqueries는 빈 배열로 둔다.
- 인사말이나 잡담으로 시작해도 질문에 도메인 키워드(제품·재고·부품·공급업체·생산·작업지시·폐기·가격·수량 등)나 확인된 entity가 포함돼 있으면 general이 아니라 해당 sql/graph로 라우팅한다.

설명이나 Markdown 없이 JSON 객체만 반환한다."""
```

Every other line in `route_query.py` (imports, `RoutePlanError`, `_recover_tool_plan`, `make_route_query_node`) stays untouched in this task — Task 5 moves `RoutePlanError`/`_recover_tool_plan` out.

- [ ] **Step 4: Run tests to verify they still pass**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_route_query.py -v`
Expected: all tests PASS, including `test_route_query_sends_query_and_entity_in_prompt` (it asserts substrings that still exist in the new prompt — verify this specifically since the prompt text changed).

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/nodes/route_query.py backend/tests/orchestrator/test_route_query.py
git commit -m "Feat: route_query 프롬프트에 general 라우트와 경계 few-shot 예시 추가"
```

---

## Task 3: `answer_general` node

**Files:**
- Create: `backend/orchestrator/nodes/answer_general.py`
- Test: `backend/tests/orchestrator/test_answer_general.py` (new)

**Interfaces:**
- Consumes: `OrchestratorState` from `orchestrator.state` (same type every other node uses).
- Produces: `make_answer_general_node() -> Callable[[OrchestratorState], Any]` — a factory matching the exact pattern of `make_generate_answer_node()` in `orchestrator/nodes/generate_answer.py`. The returned callable is `async def answer_general(state: OrchestratorState) -> dict` returning `{"final_answer": <fixed string>}` regardless of input. Task 4 imports this factory.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/orchestrator/test_answer_general.py`:

```python
"""answer_general이 general 라우트에 고정 안내 문구를 반환하는 동작을 테스트한다."""

from orchestrator.nodes.answer_general import make_answer_general_node

_EXPECTED_ANSWER = (
    "제조 데이터와 관련된 질문을 입력해 주세요.\n"
    "제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다."
)


async def test_answer_general_returns_fixed_message() -> None:
    node = make_answer_general_node()

    result = await node({"query": "안녕하세요", "tool_plan": ["general"]})

    assert result == {"final_answer": _EXPECTED_ANSWER}


async def test_answer_general_message_is_deterministic_regardless_of_query() -> None:
    """질의 내용과 무관하게 항상 같은 문구를 반환한다."""
    node = make_answer_general_node()

    first = await node({"query": "안녕하세요", "tool_plan": ["general"]})
    second = await node({"query": "오늘 날씨 어때요?", "tool_plan": ["general"]})

    assert first == second == {"final_answer": _EXPECTED_ANSWER}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_answer_general.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.nodes.answer_general'`.

- [ ] **Step 3: Write the implementation**

Create `backend/orchestrator/nodes/answer_general.py`:

```python
"""도메인과 무관한 질문(general 라우트)에 고정 안내 메시지를 반환한다."""

from collections.abc import Callable
from typing import Any

from orchestrator.state import OrchestratorState

_GENERAL_ANSWER = (
    "제조 데이터와 관련된 질문을 입력해 주세요.\n"
    "제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다."
)


def make_answer_general_node() -> Callable[[OrchestratorState], Any]:
    """LLM 호출 없이 고정 문자열을 final_answer로 반환한다."""

    async def answer_general(state: OrchestratorState) -> dict:
        return {"final_answer": _GENERAL_ANSWER}

    return answer_general
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_answer_general.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/nodes/answer_general.py backend/tests/orchestrator/test_answer_general.py
git commit -m "Feat: general 라우트용 고정 답변 노드 answer_general 추가"
```

---

## Task 4: Conditional edge in `orchestrator/graph.py`

**Files:**
- Modify: `backend/orchestrator/graph.py` (imports, add node, replace one `add_edge` call with `add_conditional_edges`)
- Test: `backend/tests/orchestrator/test_graph_general_route.py` (new — non-integration, no real Postgres/Neo4j needed since the general route never reaches `execute_sql`/`execute_cypher`)

**Interfaces:**
- Consumes: `make_answer_general_node` from Task 3 (`orchestrator.nodes.answer_general`).
- Produces: nothing new for later tasks — this is the wiring task that makes the whole pipeline behave end-to-end for a `general` query. Task 6's `/chat`-level tests depend on this working.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/orchestrator/test_graph_general_route.py`:

```python
"""route_query가 general을 반환하면 execute_plan을 건너뛰고 answer_general로
끝나는지 컴파일된 그래프 전체를 통해 테스트한다. 실제 DB 접속이 필요 없다 -
general 라우트는 execute_sql/execute_cypher를 아예 호출하지 않는다."""

import pytest

import orchestrator.graph as graph_module
from orchestrator.graph import build_orchestrator_graph
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_no_tool_call_response,
)
from tests.mocks.postgres import MockAsyncPostgresPool

_EXPECTED_ANSWER = (
    "제조 데이터와 관련된 질문을 입력해 주세요.\n"
    "제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다."
)


async def test_graph_general_route_skips_execution_and_returns_fixed_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """general로 분류되면 execute_sql/execute_cypher가 전혀 호출되지 않고
    고정 답변만 나오며, sql/cypher 관련 필드는 전부 비어 있다."""

    async def fail_execute_sql(sql: str) -> list[dict]:
        raise AssertionError("general 라우트에서 execute_sql이 호출되면 안 된다")

    async def fail_execute_cypher(cypher: str) -> list[dict]:
        raise AssertionError("general 라우트에서 execute_cypher가 호출되면 안 된다")

    monkeypatch.setattr(graph_module, "execute_sql", fail_execute_sql)
    monkeypatch.setattr(graph_module, "execute_cypher", fail_execute_cypher)

    openai_client = MockOpenAIClient(
        make_no_tool_call_response(),
        make_content_response('{"tool_plan":["general"],"subqueries":[]}'),
    )
    graph = build_orchestrator_graph(
        openai_client, MockAsyncPostgresPool(rows_by_name={})
    )

    result = await graph.ainvoke({"query": "오늘 날씨가 어때요?"})

    assert result["tool_plan"] == ["general"]
    assert result["final_answer"] == _EXPECTED_ANSWER
    assert result.get("sql_query") is None
    assert result.get("cypher_query") is None
    assert result.get("sql_result") is None
    assert result.get("graph_result") is None
    assert len(openai_client.calls) == 2


async def test_graph_domain_route_still_reaches_execute_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """회귀 확인: sql 라우트는 여전히 execute_plan을 거쳐 execute_sql이 호출된다."""
    calls: list[str] = []

    async def fake_execute_sql(sql: str) -> list[dict]:
        calls.append(sql)
        return [{"count": 1}]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)

    openai_client = MockOpenAIClient(
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    graph = build_orchestrator_graph(
        openai_client, MockAsyncPostgresPool(rows_by_name={})
    )

    result = await graph.ainvoke({"query": "전체 제품 수를 알려줘."})

    assert result["tool_plan"] == ["sql"]
    assert calls == ["SELECT COUNT(*) FROM production.product"]
    assert result["sql_result"]["result"] == [{"count": 1}]
```

- [ ] **Step 2: Run tests to verify they fail for the right reason**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_graph_general_route.py -v`
Expected: `test_graph_general_route_skips_execution_and_returns_fixed_answer` FAILS — `parse_execution_plan` returns `{"tool_plan": ["general"], "subqueries": []}` fine (Task 1), but `graph.py` still unconditionally routes `route_query -> execute_plan`, so `execute_plan` runs with zero subqueries and produces `sql_query: None, sql_result: None, cypher_query: None, graph_result: None` — then `compose_results`/`generate_answer` run too, producing `final_answer: None` (not the fixed message) since `composed_result` is the "no subqueries" failure object, not `None`... Run it and confirm the actual failure is `assert result["final_answer"] == _EXPECTED_ANSWER` (it will actually be something like `"COMPOSED: {...}"` from the existing `generate_answer` pass-through, not `None` — either way it's not `_EXPECTED_ANSWER`, confirming the conditional edge doesn't exist yet). `test_graph_domain_route_still_reaches_execute_plan` should already PASS (regression guard, unaffected by this task).

- [ ] **Step 3: Implement the conditional edge**

In `backend/orchestrator/graph.py`:

`graph.py` currently has no import from `orchestrator.planning` at all. Add two new import lines, keeping the existing alphabetical ordering of the `from orchestrator...` import block (lines 13-22): `from orchestrator.nodes.answer_general import make_answer_general_node` goes right after `from orchestrator.execution.sql_executor import execute_sql` (before `nodes.compose_results`, since "answer_general" sorts before "compose_results"); `from orchestrator.planning import GENERAL_ROUTE` goes right after `from orchestrator.nodes.route_query import make_route_query_node` (before `orchestrator.state`, since "planning" sorts before "state"):

```python
from orchestrator.execution.cypher_executor import execute_cypher
from orchestrator.execution.sql_executor import execute_sql
from orchestrator.nodes.answer_general import make_answer_general_node
from orchestrator.nodes.compose_results import make_compose_results_node
from orchestrator.nodes.execute_plan import make_execute_plan_node
from orchestrator.nodes.generate_answer import make_generate_answer_node
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.planning import GENERAL_ROUTE
from orchestrator.state import OrchestratorState
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph
```

(Exact ordering isn't critical since `ruff --fix` in the pre-commit hook will re-sort imports automatically — but writing it correctly avoids an extra auto-fix diff at commit time.)

Add a routing function above `build_orchestrator_graph` (after the module-level `_PROJECT_ROOT = ...` / `_load_schema_context` function, before `def build_orchestrator_graph`):

```python
def _route_after_route_query(state: OrchestratorState) -> str:
    """general로 분류되면 execute_plan을 건너뛰고 고정 답변으로 바로 끝낸다."""
    return "answer_general" if state.get("tool_plan") == [GENERAL_ROUTE] else "execute_plan"
```

(Reuses the `GENERAL_ROUTE = "general"` constant from Task 1 instead of a second hardcoded `"general"` string, so the two checks — `planning.py`'s early-return and this routing decision — can't silently drift apart.)

Inside `build_orchestrator_graph`, add the new node (after the existing `graph.add_node("route_query", ...)` call, anywhere before the edges are wired):

```python
    graph.add_node(
        "answer_general",
        cast(Any, make_answer_general_node()),
    )
```

Replace the single line `graph.add_edge("route_query", "execute_plan")` with:

```python
    graph.add_conditional_edges(
        "route_query",
        _route_after_route_query,
        {"answer_general": "answer_general", "execute_plan": "execute_plan"},
    )
    graph.add_edge("answer_general", END)
```

The rest of the edges (`graph.add_edge(START, "resolve_entity")`, `graph.add_edge("resolve_entity", "route_query")`, `graph.add_edge("execute_plan", "compose_results")`, `graph.add_edge("compose_results", "generate_answer")`, `graph.add_edge("generate_answer", END)`) stay exactly as they are.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_graph_general_route.py -v`
Expected: both PASS.

- [ ] **Step 5: Run the broader orchestrator suite for regressions**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/ -v -m "not integration"`
Expected: all PASS (integration tests requiring live Postgres/Neo4j are excluded here — those are covered separately if the environment has live DBs available; this task doesn't change their behavior).

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/graph.py backend/tests/orchestrator/test_graph_general_route.py
git commit -m "Feat: route_query가 general이면 execute_plan을 건너뛰는 조건부 엣지 추가"
```

---

## Task 5: `RoutePlanError` → `AppError` migration

**Files:**
- Modify: `backend/orchestrator/errors.py` (add `_recover_tool_plan`, `RoutePlanError`)
- Modify: `backend/orchestrator/nodes/route_query.py:1-48` (remove `_recover_tool_plan`/`RoutePlanError`, fix imports)
- Modify: `backend/tests/orchestrator/test_route_query.py:5` (import path)
- Modify: `backend/tests/evaluation/test_runner_outcomes.py:13` (import path)
- Modify: `backend/evaluation/runner.py:39-41` (import path)
- Test: `backend/tests/orchestrator/test_errors.py` (add tests)

**Interfaces:**
- Consumes: `AppError` (already defined in `orchestrator/errors.py`), `SUPPORTED_TOOLS` from `orchestrator.planning`.
- Produces: `RoutePlanError` now lives at `orchestrator.errors.RoutePlanError`, subclasses `AppError`, and is constructed exactly as before: `RoutePlanError(message: str, raw_response: str, tool_plan: list[str] | None = None)`. Its instance attributes (`raw_response`, `tool_plan`, plus the inherited `status_code`, `code`, `message`) are unchanged in meaning — only `status_code`/`code`/`message` are now always `422`/`"ROUTE_PLAN_ERROR"`/the fixed Korean string (previously `RoutePlanError` had no `status_code`/`code` at all, since it wasn't an `AppError`). Task 6 depends on this: `main.py`'s existing `@app.exception_handler(AppError)` now catches it automatically (no change needed to `main.py`).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/orchestrator/test_errors.py`, updating the import at the top:

```python
from orchestrator.errors import (
    AppError,
    EntityAmbiguousError,
    EntityNotFoundError,
    RetryExceededError,
    RoutePlanError,
)
```

And add these test functions (anywhere in the file):

```python
def test_route_plan_error_has_422_status() -> None:
    """라우팅 계획을 못 세우면 422와 안내 메시지를 담는다."""
    error = RoutePlanError("검증 실패", '{"tool_plan":["sql"]}')

    assert isinstance(error, AppError)
    assert error.status_code == 422
    assert error.code == "ROUTE_PLAN_ERROR"
    assert "질문을 더 구체적으로" in error.message


def test_route_plan_error_recovers_valid_tool_plan_from_raw_response() -> None:
    """raw_response에 유효한 tool_plan이 있으면 복구해 보존한다."""
    error = RoutePlanError(
        "subquery 검증 실패", '{"tool_plan":["sql","graph"],"subqueries":[]}'
    )

    assert error.raw_response == '{"tool_plan":["sql","graph"],"subqueries":[]}'
    assert error.tool_plan == ["sql", "graph"]


def test_route_plan_error_tool_plan_is_none_when_unrecoverable() -> None:
    """raw_response가 완전히 깨지면 tool_plan은 None이다."""
    error = RoutePlanError("파싱 실패", "not json")

    assert error.tool_plan is None


def test_route_plan_error_accepts_explicit_tool_plan_override() -> None:
    """호출부가 tool_plan을 직접 넘기면 raw_response 복구를 건너뛴다."""
    error = RoutePlanError("실패", "not json", tool_plan=["graph"])

    assert error.tool_plan == ["graph"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_errors.py -v`
Expected: FAIL with `ImportError: cannot import name 'RoutePlanError' from 'orchestrator.errors'`.

- [ ] **Step 3: Move `RoutePlanError`/`_recover_tool_plan` into `orchestrator/errors.py`**

Replace the full contents of `backend/orchestrator/errors.py` with:

```python
# Orchestrator 파이프라인의 도메인 예외 계층

import json

from orchestrator.planning import SUPPORTED_TOOLS


# 모든 도메인 예외의 공통 베이스
class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


# 질의 대상 이름으로 엔티티를 찾지 못했을 때 발생
class EntityNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            404,
            "ENTITY_NOT_FOUND",
            "질의 대상을 찾을 수 없습니다. 이름을 다시 확인해 주세요.",
        )


# 유사 후보가 여러 개라 사용자 확인이 필요할 때 발생
# 이번 범위(resolve_entity의 정확 일치 매칭)에서는 raise되지 않는다
class EntityAmbiguousError(AppError):
    def __init__(self, candidates: list) -> None:
        super().__init__(
            200,
            "ENTITY_AMBIGUOUS",
            "비슷한 이름이 여러 개 있습니다. 아래 후보 중 하나를 선택해 주세요.",
        )
        self.candidates = candidates


# self-correction 재시도 횟수 초과용으로 정의됐으나, 재시도 루프는 소진 시에도
# raise하지 않고 error 필드를 유지한 채 정상 종료하도록 구현돼 실제로는 쓰이지 않는다
class RetryExceededError(AppError):
    def __init__(self) -> None:
        super().__init__(
            422,
            "RETRY_EXCEEDED",
            "질의를 처리하지 못했습니다. 질문을 더 구체적으로 입력해 주세요.",
        )


def _recover_tool_plan(raw_response: str) -> list[str] | None:
    """전체 계획이 잘못돼도 독립적으로 유효한 route 선택은 보존한다."""
    try:
        raw = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return None
    value = (
        raw
        if isinstance(raw, list)
        else raw.get("tool_plan") if isinstance(raw, dict) else None
    )
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(tool, str) for tool in value)
        or len(value) != len(set(value))
        or bool(set(value) - SUPPORTED_TOOLS)
    ):
        return None
    return list(value)


# 라우팅 계획을 세우지 못했을 때(빈/무효 tool_plan, 검증 실패 재시도 소진 등) 발생
class RoutePlanError(AppError):
    """검증 실패 정보와 모델 응답 원문을 함께 보존한다."""

    def __init__(
        self,
        message: str,
        raw_response: str,
        tool_plan: list[str] | None = None,
    ) -> None:
        super().__init__(
            422,
            "ROUTE_PLAN_ERROR",
            "질문을 처리할 계획을 세우지 못했습니다. 질문을 더 구체적으로 입력해 주세요.",
        )
        self.raw_response = raw_response
        self.tool_plan = (
            tool_plan if tool_plan is not None else _recover_tool_plan(raw_response)
        )
```

(No circular import risk: `orchestrator/planning.py` imports only `json` and `typing` — verified by reading the file — so `orchestrator/errors.py` importing `SUPPORTED_TOOLS` from it is safe.)

- [ ] **Step 4: Remove the old definitions from `route_query.py` and fix its imports**

In `backend/orchestrator/nodes/route_query.py`, replace the header (current lines 1-48, everything from `import json` down through the end of the `RoutePlanError` class) with:

```python
import json
import logging
import os
from collections.abc import Callable
from typing import Any

from orchestrator.errors import RoutePlanError
from orchestrator.planning import parse_execution_plan
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)
```

(`SUPPORTED_TOOLS` is no longer imported here — it was only used by `_recover_tool_plan`, which moved to `errors.py`. `json` is still needed — it's used later in `route_query()` for `json.dumps(state.get("entity"), ...)`.)

Everything from `_SYSTEM_PROMPT = """..."""` onward (the Task 2 prompt, `make_route_query_node`, `route_query`) stays exactly as Task 2 left it — only the header above changes.

- [ ] **Step 5: Fix the three external import sites**

`backend/tests/orchestrator/test_route_query.py` line 5, change:
```python
from orchestrator.nodes.route_query import RoutePlanError, make_route_query_node
```
to:
```python
from orchestrator.errors import RoutePlanError
from orchestrator.nodes.route_query import make_route_query_node
```

`backend/tests/evaluation/test_runner_outcomes.py` line 13, change:
```python
from orchestrator.nodes.route_query import RoutePlanError
```
to:
```python
from orchestrator.errors import RoutePlanError
```

`backend/evaluation/runner.py` lines 39-41, change:
```python
from orchestrator.errors import AppError
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import RoutePlanError, make_route_query_node
```
to:
```python
from orchestrator.errors import AppError, RoutePlanError
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
```

Leave `runner.py`'s `except RoutePlanError as exc: ... except ValueError as exc: ...` block (around line 513) untouched — `RoutePlanError` is a specific exception type matched before the broader `except ValueError`, so it still catches correctly even though `RoutePlanError` is no longer a `ValueError` subclass.

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/orchestrator/test_errors.py tests/orchestrator/test_route_query.py tests/evaluation/test_runner_outcomes.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full backend suite (excluding integration) for regressions**

Run: `venv/Scripts/python.exe -m pytest -m "not integration" -q`
Expected: all PASS. Pay particular attention to any `mypy`/import-order issues if you also run `venv/Scripts/python.exe -m ruff check .` and `venv/Scripts/python.exe -m mypy .` — both should stay clean (no new errors introduced by the module move).

- [ ] **Step 8: Commit**

```bash
git add backend/orchestrator/errors.py backend/orchestrator/nodes/route_query.py \
  backend/tests/orchestrator/test_errors.py backend/tests/orchestrator/test_route_query.py \
  backend/tests/evaluation/test_runner_outcomes.py backend/evaluation/runner.py
git commit -m "Refactor: RoutePlanError를 AppError 계층으로 옮겨 /chat 422 처리가 가능하게 함"
```

---

## Task 6: `/chat` end-to-end tests

**Files:**
- Modify: `backend/tests/api/test_chat.py` (add two tests; add imports)

**Interfaces:**
- Consumes: everything from Tasks 1-5 (the full general route + the `RoutePlanError`/`AppError` migration). This is the final proof that the feature works end-to-end through the actual `/chat` FastAPI route.
- Produces: nothing further downstream.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/api/test_chat.py`. First, extend the existing imports at the top of the file — add `from fastapi.responses import JSONResponse` next to the existing `from fastapi import FastAPI, Request` import, and add `from orchestrator.errors import AppError` near the existing `from core.auth import CurrentUser, create_access_token` import (exact placement doesn't matter, just keep it with the other non-mock imports). Then add:

```python
async def test_chat_returns_fixed_answer_for_general_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """도메인과 무관한 질문은 실행 없이 고정 안내 문구로 응답한다."""
    openai_client = MockOpenAIClient(
        make_no_tool_call_response(),
        make_content_response('{"tool_plan":["general"],"subqueries":[]}'),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module, "get_pool", lambda: MockAsyncPostgresPool(rows_by_name={})
    )
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: MockAsyncWritePool())

    result = await chat(
        ChatRequest(query="오늘 날씨가 어때요?"),
        request=_fake_request(),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result.final_answer == (
        "제조 데이터와 관련된 질문을 입력해 주세요.\n"
        "제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다."
    )
    assert result.sql_query is None
    assert result.cypher_query is None
    assert result.sql_result is None
    assert result.graph_result is None


async def test_chat_endpoint_returns_422_when_route_planning_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """route_query가 재시도 후에도 실패하면 처리되지 않은 500이 아니라 422로
    응답한다. main.py를 직접 import하지 않는다 - use_windows_selector_event_loop_policy()는
    "이벤트 루프가 만들어지기 전, 모듈 로드 시점에 호출해야 한다"는 제약이 있는데
    (core/event_loop.py), 이미 이벤트 루프가 떠 있는 테스트 실행 중에 main을
    import하면 그 제약을 어기게 된다. 그래서 main.py의 실제 핸들러와 동일한
    로직(AppError -> {code, message} + status_code)을 이 테스트 앱에도 그대로
    등록해 같은 결과를 검증한다."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    openai_client = MockOpenAIClient(
        make_no_tool_call_response(),
        make_content_response("[]"),
        make_content_response("[]"),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module, "get_pool", lambda: MockAsyncPostgresPool(rows_by_name={})
    )

    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    app = FastAPI()
    app.include_router(chat_module.router)
    app.add_exception_handler(AppError, app_error_handler)
    client = TestClient(app)
    client.cookies.set("access_token", create_access_token("kim.quality", "admin"))

    response = client.post("/chat", json={"query": "질의"})

    assert response.status_code == 422
    assert response.json()["code"] == "ROUTE_PLAN_ERROR"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/api/test_chat.py -v`
Expected: both new tests PASS (Tasks 1-5 already implemented everything they depend on — there's no separate "RED" phase for this task since it's a pure integration proof over already-implemented, already-tested units; if either fails, it points at a wiring bug between tasks, not missing implementation). Also confirm every other test in the file still PASSES.

- [ ] **Step 3: Run the full backend suite**

Run: `venv/Scripts/python.exe -m pytest -m "not integration" -q`
Expected: all PASS, no regressions anywhere.

Run: `venv/Scripts/python.exe -m ruff check .` and `venv/Scripts/python.exe -m mypy .`
Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_chat.py
git commit -m "Test: /chat의 general 라우트 200 응답과 RoutePlanError 422 응답 통합 테스트 추가"
```

---

## Final Verification

After all 6 tasks:

- [ ] Run the full suite once more: `venv/Scripts/python.exe -m pytest -m "not integration" -q` from `backend/` — expect all green.
- [ ] If a live Postgres/Neo4j environment is available, also run the `integration`-marked tests (`tests/orchestrator/test_graph.py`, `tests/orchestrator/test_graph_integration.py`) to confirm the unchanged sql/graph paths still work end-to-end — this plan doesn't modify their code paths, but it's worth confirming nothing in `graph.py`'s edge rewiring broke them.
- [ ] Manually sanity-check the spec's 5 general few-shot questions and 2 boundary questions against a real running `/chat` (not just mocks) if the user wants to validate actual LLM classification quality before merging — the plan's tests only prove the *code* handles whatever the LLM returns correctly, not that the LLM's real judgment is perfect (see spec's "`general` 분류 정확도" section).
