# Self-Correction 뼈대 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQL/Cypher 쿼리를 "한 번 생성하고 한 번 실행을 시도하는" LangGraph SubGraph 뼈대(SQL Agent, Cypher Agent)를 만들고, 오케스트레이터 메인 그래프에 배선해 `generate_queries`를 대체한다. **재시도 여부·횟수·판단 기준을 포함한 self-correction 자체는 전부 이번 범위 밖**이며, 다른 담당자가 이 뼈대 위에서 처음부터 설계한다. 이번 브랜치는 state 계약과 노드 하나씩만 만든다 — 루프도, 조건부 엣지도, 재시도 상한도 넣지 않는다.

**Architecture:** SQL Agent·Cypher Agent를 각각 `agent → tools → END`(선형, 루프 없음) SubGraph로 만든다. `agent`는 기존 `generate_sql`/`generate_cypher`로 쿼리를 한 번 생성하고, `tools`는 주입받은 `execute_sql`/`execute_cypher` 콜백을 한 번 호출해 성공하면 `result`, 실패(예외)하면 `error`에 담고 그대로 끝낸다 — 예외를 다시 던지거나 재시도하지 않는다. 메인 그래프는 `resolve_entity → route_query → sql_agent → cypher_agent → generate_answer → END`로 순차 배선한다.

**Tech Stack:** Python 3.12, LangGraph, FastAPI, pytest.

## Global Constraints

- 재시도 개념(루프, 조건부 엣지, 상한, `RetryExceededError` 발생)은 **이번 범위에 포함하지 않는다** — `backend/orchestrator/errors.py`의 `RetryExceededError`는 계속 미사용 상태로 둔다(변경 없음)
- `tools`는 `execute_sql`/`execute_cypher` 호출이 예외를 내도 그 예외를 밖으로 전파하지 않고 `error` 필드에 담아 정상 종료한다
- 재시도 시 entity/route를 재추출하지 않는다는 원칙은 유지되지만, 이번 범위에는 재시도 자체가 없으므로 해당 없음
- 실제 검증/실행 로직(SQL 파싱, 화이트리스트, DB 실행)은 구현하지 않는다 — `execute_sql`/`execute_cypher`는 항상 `NotImplementedError`를 내는 자리표시 콜백
- 주석은 무엇을 하는지만 짧게 적고, 이유는 적지 않는다
- 테스트는 실행 전 사용자에게 먼저 확인받는다
- 브랜치: `feat/self-correction-skeleton` (`dev`에서 분기, 이미 생성됨)

---

### Task 1: SQL Agent SubGraph 뼈대 (생성 1회 + 실행 시도 1회, 재시도 없음)

**Files:**
- Create: `backend/orchestrator/subgraphs/sql_agent.py`
- Test: `backend/tests/orchestrator/test_sql_agent.py`

**Interfaces:**
- Consumes: `generate_sql`(`backend/agents/sql/generator.py`, 기존 함수 재사용)
- Produces: `SQLAgentState`(TypedDict: `query, entity, schema, messages, result, error`), `make_sql_agent_subgraph(openai_client, execute_sql: Callable[[str], Any]) -> CompiledStateGraph` — Task 4가 사용

- [ ] **Step 1: 실패하는 테스트 작성**

Create `backend/tests/orchestrator/test_sql_agent.py`:

```python
"""SQL Agent SubGraph의 생성-실행 뼈대를 테스트한다."""

from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph
from tests.mocks.openai import MockOpenAIClient, make_content_response


def _initial_state(query: str = "제품 수를 알려줘.") -> dict:
    return {
        "query": query,
        "entity": None,
        "schema": "production.product {productid: INTEGER}",
        "messages": [],
        "result": None,
        "error": None,
    }


def test_sql_agent_returns_result_when_execution_succeeds() -> None:
    """실행이 성공하면 result를 채우고 error는 None이다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT COUNT(*) FROM production.product")
    )
    subgraph = make_sql_agent_subgraph(openai_client, execute_sql=lambda sql: [{"count": 10}])

    result = subgraph.invoke(_initial_state())

    assert result["result"] == [{"count": 10}]
    assert result["error"] is None
    assert result["messages"][-1]["content"] == "SELECT COUNT(*) FROM production.product"
    assert len(openai_client.calls) == 1


def test_sql_agent_returns_error_when_execution_fails() -> None:
    """실행이 실패하면 예외를 전파하지 않고 error 필드에 담아 정상 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response("SELECT bad_column FROM production.product")
    )

    def execute_sql(sql: str) -> None:
        raise ValueError("column bad_column does not exist")

    subgraph = make_sql_agent_subgraph(openai_client, execute_sql=execute_sql)

    result = subgraph.invoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "column bad_column does not exist"
    assert len(openai_client.calls) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/orchestrator/test_sql_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.subgraphs.sql_agent'`

- [ ] **Step 3: 최소 구현**

Create `backend/orchestrator/subgraphs/sql_agent.py`:

```python
"""SQL을 한 번 생성하고 한 번 실행을 시도하는 뼈대 SubGraph를 만든다."""

from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.sql.generator import generate_sql


class SQLAgentState(TypedDict):
    query: str
    entity: dict | None
    schema: str
    messages: list
    result: Any | None
    error: str | None


def make_sql_agent_subgraph(
    openai_client: Any,
    execute_sql: Callable[[str], Any],
) -> CompiledStateGraph:
    """SQL 생성 1회·실행 시도 1회 뼈대를 만든다. execute_sql은 self-correction
    구현자가 실제 검증·실행 로직으로 교체하는 자리다. 재시도는 이 뼈대에 없다."""

    def agent(state: SQLAgentState) -> dict:
        sql = generate_sql(
            openai_client,
            query=state["query"],
            entity=state["entity"],
            schema_text=state["schema"],
        )
        return {
            "messages": [*state["messages"], {"role": "assistant", "content": sql}]
        }

    def tools(state: SQLAgentState) -> dict:
        sql = state["messages"][-1]["content"]
        try:
            result = execute_sql(sql)
        except Exception as exc:
            return {"error": str(exc), "result": None}
        return {"result": result, "error": None}

    graph = StateGraph(SQLAgentState)
    graph.add_node("agent", agent)  # type: ignore[call-overload]
    graph.add_node("tools", tools)  # type: ignore[call-overload]
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "tools")
    graph.add_edge("tools", END)
    return graph.compile()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests/orchestrator/test_sql_agent.py -v`
Expected: 전체 PASS. 이어서 `ruff check backend/orchestrator/subgraphs backend/tests/orchestrator/test_sql_agent.py`와 `mypy backend/orchestrator/subgraphs`를 실행해 문제없는지 확인한다 (mypy가 `add_node` 호출에서 `call-overload`를 다르게 flag하면, 기존 `graph.py`가 쓰는 것과 동일한 `# type: ignore[call-overload]` 패턴으로 맞춘다).

- [ ] **Step 5: 커밋**

```bash
git add backend/orchestrator/subgraphs/sql_agent.py backend/tests/orchestrator/test_sql_agent.py
git commit -m "Feat: SQL Agent SubGraph 생성-실행 뼈대 추가"
```

---

### Task 2: Cypher Agent SubGraph 뼈대 (생성 1회 + 실행 시도 1회, 재시도 없음)

**Files:**
- Create: `backend/orchestrator/subgraphs/cypher_agent.py`
- Test: `backend/tests/orchestrator/test_cypher_agent.py`

**Interfaces:**
- Consumes: `generate_cypher`(`backend/agents/cypher/generator.py`), `GraphQueryPolicy`(`backend/agents/cypher/schema/models.py`)
- Produces: `CypherAgentState`(TypedDict: `query, entity, schema, messages, result, error`), `make_cypher_agent_subgraph(openai_client, execute_cypher: Callable[[str], Any], query_policy: GraphQueryPolicy) -> CompiledStateGraph` — Task 4가 사용

- [ ] **Step 1: 실패하는 테스트 작성**

Create `backend/tests/orchestrator/test_cypher_agent.py`:

```python
"""Cypher Agent SubGraph의 생성-실행 뼈대를 테스트한다."""

from agents.cypher.schema.models import GraphQueryPolicy
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from tests.mocks.openai import MockOpenAIClient, make_content_response

QUERY_POLICY = GraphQueryPolicy(bomAsOfDate="2014-08-08", bomMaxDepth=4)


def _initial_state(query: str = "부품 사용처를 알려줘.") -> dict:
    return {
        "query": query,
        "entity": None,
        "schema": "(:Product)-[:REQUIRES_COMPONENT]->(:Product)",
        "messages": [],
        "result": None,
        "error": None,
    }


def test_cypher_agent_returns_result_when_execution_succeeds() -> None:
    """실행이 성공하면 result를 채우고 error는 None이다."""
    openai_client = MockOpenAIClient(make_content_response("MATCH (n:Product) RETURN n"))
    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=lambda cypher: [{"n": "x"}], query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] == [{"n": "x"}]
    assert result["error"] is None
    assert result["messages"][-1]["content"] == "MATCH (n:Product) RETURN n"
    assert len(openai_client.calls) == 1


def test_cypher_agent_returns_error_when_execution_fails() -> None:
    """실행이 실패하면 예외를 전파하지 않고 error 필드에 담아 정상 종료한다."""
    openai_client = MockOpenAIClient(make_content_response("MATCH (n:Unknown) RETURN n"))

    def execute_cypher(cypher: str) -> None:
        raise ValueError("unknown label Unknown")

    subgraph = make_cypher_agent_subgraph(
        openai_client, execute_cypher=execute_cypher, query_policy=QUERY_POLICY
    )

    result = subgraph.invoke(_initial_state())

    assert result["result"] is None
    assert result["error"] == "unknown label Unknown"
    assert len(openai_client.calls) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/orchestrator/test_cypher_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.subgraphs.cypher_agent'`

- [ ] **Step 3: 최소 구현**

Create `backend/orchestrator/subgraphs/cypher_agent.py`:

```python
"""Cypher를 한 번 생성하고 한 번 실행을 시도하는 뼈대 SubGraph를 만든다."""

from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.cypher.generator import generate_cypher
from agents.cypher.schema.models import GraphQueryPolicy


class CypherAgentState(TypedDict):
    query: str
    entity: dict | None
    schema: str
    messages: list
    result: Any | None
    error: str | None


def make_cypher_agent_subgraph(
    openai_client: Any,
    execute_cypher: Callable[[str], Any],
    query_policy: GraphQueryPolicy,
) -> CompiledStateGraph:
    """Cypher 생성 1회·실행 시도 1회 뼈대를 만든다. execute_cypher는 self-correction
    구현자가 실제 검증·실행 로직으로 교체하는 자리다. 재시도는 이 뼈대에 없다."""

    def agent(state: CypherAgentState) -> dict:
        cypher = generate_cypher(
            openai_client,
            query=state["query"],
            entity=state["entity"],
            schema_text=state["schema"],
            query_policy=query_policy,
        )
        return {
            "messages": [*state["messages"], {"role": "assistant", "content": cypher}]
        }

    def tools(state: CypherAgentState) -> dict:
        cypher = state["messages"][-1]["content"]
        try:
            result = execute_cypher(cypher)
        except Exception as exc:
            return {"error": str(exc), "result": None}
        return {"result": result, "error": None}

    graph = StateGraph(CypherAgentState)
    graph.add_node("agent", agent)  # type: ignore[call-overload]
    graph.add_node("tools", tools)  # type: ignore[call-overload]
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "tools")
    graph.add_edge("tools", END)
    return graph.compile()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests/orchestrator/test_cypher_agent.py -v`
Expected: 전체 PASS. `ruff check backend/orchestrator/subgraphs backend/tests/orchestrator/test_cypher_agent.py`와 `mypy backend/orchestrator/subgraphs` 확인.

- [ ] **Step 5: 커밋**

```bash
git add backend/orchestrator/subgraphs/cypher_agent.py backend/tests/orchestrator/test_cypher_agent.py
git commit -m "Feat: Cypher Agent SubGraph 생성-실행 뼈대 추가"
```

---

### Task 3: `generate_answer` 스텁 노드

**Files:**
- Create: `backend/orchestrator/nodes/generate_answer.py`
- Test: `backend/tests/orchestrator/test_generate_answer.py`

**Interfaces:**
- Consumes: `OrchestratorState`(`backend/orchestrator/state.py`, 기존)
- Produces: `make_generate_answer_node() -> Callable[[OrchestratorState], dict]` — Task 4가 사용

- [ ] **Step 1: 실패하는 테스트 작성**

Create `backend/tests/orchestrator/test_generate_answer.py`:

```python
"""generate_answer 노드가 sql_result/graph_result를 final_answer로 조합하는 동작을 테스트한다."""

from orchestrator.nodes.generate_answer import make_generate_answer_node


def test_generate_answer_combines_sql_and_graph_results() -> None:
    """SQL과 GRAPH 결과가 모두 있으면 둘 다 final_answer에 포함한다."""
    node = make_generate_answer_node()

    result = node(
        {
            "query": "질의",
            "sql_result": {"result": [{"count": 10}], "error": None},
            "graph_result": {"result": None, "error": "실행 실패"},
        }
    )

    assert result == {
        "final_answer": (
            "SQL: {'result': [{'count': 10}], 'error': None} / "
            "GRAPH: {'result': None, 'error': '실행 실패'}"
        )
    }


def test_generate_answer_returns_none_when_no_results() -> None:
    """결과가 하나도 없으면 final_answer도 None이다."""
    node = make_generate_answer_node()

    result = node({"query": "질의", "sql_result": None, "graph_result": None})

    assert result == {"final_answer": None}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/orchestrator/test_generate_answer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.nodes.generate_answer'`

- [ ] **Step 3: 최소 구현**

Create `backend/orchestrator/nodes/generate_answer.py`:

```python
"""sql_result·graph_result를 final_answer로 조합하는 얇은 pass-through 노드를 만든다."""

from collections.abc import Callable

from orchestrator.state import OrchestratorState


def make_generate_answer_node() -> Callable[[OrchestratorState], dict]:
    """LLM 호출 없이 sql_result/graph_result를 final_answer 문자열로 합치는 노드를 만든다."""

    def generate_answer(state: OrchestratorState) -> dict:
        parts = []
        sql_result = state.get("sql_result")
        if sql_result is not None:
            parts.append(f"SQL: {sql_result}")
        graph_result = state.get("graph_result")
        if graph_result is not None:
            parts.append(f"GRAPH: {graph_result}")
        return {"final_answer": " / ".join(parts) if parts else None}

    return generate_answer
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests/orchestrator/test_generate_answer.py -v`
Expected: 전체 PASS.

- [ ] **Step 5: 커밋**

```bash
git add backend/orchestrator/nodes/generate_answer.py backend/tests/orchestrator/test_generate_answer.py
git commit -m "Feat: generate_answer 얇은 pass-through 스텁 노드 추가"
```

---

### Task 4: 메인 그래프 배선 — `generate_queries`를 SubGraph로 교체

**Files:**
- Modify: `backend/orchestrator/graph.py`
- Modify: `backend/api/chat.py`
- Modify: `backend/tests/orchestrator/test_graph.py`
- Modify: `backend/tests/api/test_chat.py`
- Delete: `backend/orchestrator/nodes/generate_queries.py`
- Delete: `backend/tests/orchestrator/test_generate_queries.py`

**Interfaces:**
- Consumes: `make_sql_agent_subgraph`(Task 1), `make_cypher_agent_subgraph`(Task 2), `make_generate_answer_node`(Task 3)
- Produces: `build_orchestrator_graph(openai_client, postgres_connection) -> CompiledStateGraph` — 외부 시그니처는 변경하지 않음(`chat.py` 호출부 그대로)

이 태스크를 마치면 재시도 루프가 없으므로, `sql`/`graph`로 라우팅되는 질의는 `execute_sql`/`execute_cypher` 자리표시(`NotImplementedError`)가 잡혀 `sql_result`/`graph_result`에 `error` 문자열로 담기고, 그래프는 예외 없이 끝까지 정상 실행된다. `/chat`은 200을 반환하되 `final_answer`에는 "SQL 실행/검증은 self-correction 구현에서 채운다." 같은 에러 메시지가 담긴다 — self-correction 실제 구현이 붙기 전까지는 의도된 동작이다.

- [ ] **Step 1: 실패하는 테스트로 먼저 새 배선을 정의한다**

`backend/tests/orchestrator/test_graph.py` 전체를 다음으로 교체한다:

```python
"""엔티티 확정 -> 라우팅 -> self-correction 뼈대까지의 전체 흐름을 테스트한다."""

from orchestrator.graph import build_orchestrator_graph
from tests.mocks.openai import (
    MockOpenAIClient,
    make_content_response,
    make_tool_call_response,
)
from tests.mocks.postgres import MockPostgresConnection


def test_graph_resolves_entity_then_runs_sql_agent_once() -> None:
    """제품명이 있는 SQL형 질의는 entity 확정 후 sql_agent가 한 번 생성·실행을
    시도한다(execute_sql이 자리표시라 항상 실패하고 error에 담긴다)."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        ),
        make_content_response('["sql"]'),
        make_content_response(
            "SELECT listprice, standardcost FROM production.product "
            "WHERE productid = 956"
        ),
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke(
        {"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."}
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == (
        "SELECT listprice, standardcost FROM production.product "
        "WHERE productid = 956"
    )
    assert result["sql_result"]["result"] is None
    assert "self-correction 구현에서 채운다" in result["sql_result"]["error"]
    assert result["cypher_query"] is None
    assert result["graph_result"] is None
    assert len(openai_client.calls) == 3


def test_graph_routes_to_graph_and_runs_cypher_agent_once() -> None:
    """부품 사용처를 묻는 질의는 entity 확정 후 graph로 라우팅되고 cypher_agent가
    한 번 생성·실행을 시도한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity", {"entityType": "product", "entityName": "Paint - Black"}
        ),
        make_content_response('["graph"]'),
        make_content_response(
            "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
            "WHERE part.productId = 492 RETURN parent"
        ),
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Paint - Black": (492, "Paint - Black")}
    )
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke(
        {"query": "부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘."}
    )

    assert result["entity"] == {"productId": 492, "productName": "Paint - Black"}
    assert result["tool_plan"] == ["graph"]
    assert result["sql_query"] is None
    assert result["sql_result"] is None
    assert result["cypher_query"] == (
        "MATCH (part:Product)<-[:REQUIRES_COMPONENT*1..4]-(parent:Product) "
        "WHERE part.productId = 492 RETURN parent"
    )
    assert result["graph_result"]["result"] is None
    assert "self-correction 구현에서 채운다" in result["graph_result"]["error"]
    assert len(openai_client.calls) == 3


def test_graph_builds_final_answer_from_sql_result() -> None:
    """특정 제품을 지칭하지 않는 집계 질의도 sql_agent를 거쳐 final_answer가 채워진다."""
    openai_client = MockOpenAIClient(
        make_content_response("[]"),
        make_content_response('["sql"]'),
        make_content_response("SELECT COUNT(*) FROM production.product"),
    )
    postgres_connection = MockPostgresConnection(rows_by_name={})
    graph = build_orchestrator_graph(openai_client, postgres_connection)

    result = graph.invoke({"query": "전체 제품 수를 알려줘."})

    assert result["entity"] is None
    assert result["tool_plan"] == ["sql"]
    assert result["sql_query"] == "SELECT COUNT(*) FROM production.product"
    assert result["final_answer"] is not None
    assert "SQL:" in result["final_answer"]
```

`backend/tests/api/test_chat.py`의 `test_chat_passes_confirmed_entity_to_orchestrator`를 다음으로 교체한다(같은 파일의 `test_chat_request_rejects_unknown_field`는 그대로 둔다):

```python
def test_chat_passes_confirmed_entity_and_runs_sql_agent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirmed_entity가 있으면 매칭 없이 바로 라우팅으로 넘어가고, sql_agent가
    한 번 생성·실행을 시도한 뒤 200으로 정상 종료한다."""
    openai_client = MockOpenAIClient(
        make_content_response('["sql"]'),
        make_content_response(
            "SELECT listprice FROM production.product WHERE productid = 956"
        ),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module,
        "get_connection",
        lambda: MockPostgresConnection(
            rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
        ),
    )

    result = asyncio.run(
        chat(
            ChatRequest(
                query="그 제품 정가 알려줘.",
                confirmed_entity={
                    "productId": 956,
                    "productName": "Touring-1000 Yellow, 54",
                },
            )
        )
    )

    assert result["entity"] == {
        "productId": 956,
        "productName": "Touring-1000 Yellow, 54",
    }
    assert result["sql_query"] == (
        "SELECT listprice FROM production.product WHERE productid = 956"
    )
    assert result["final_answer"] is not None
    assert len(openai_client.calls) == 2
```

(이미 있는 `import asyncio`, `import pytest`, `import api.chat as chat_module`, `from api.chat import ChatRequest, chat`, `from tests.mocks.openai import ...`, `from tests.mocks.postgres import MockPostgresConnection`는 그대로 둔다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/orchestrator/test_graph.py backend/tests/api/test_chat.py -v`
Expected: FAIL — 기존 배선(`generate_queries`)이 여전히 붙어 있어 `sql_result`/`graph_result`/`final_answer` 필드가 존재하지 않는다.

- [ ] **Step 3: 최소 구현**

`backend/orchestrator/graph.py` 전체를 다음으로 교체한다:

```python
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphQueryPolicy, GraphSchema
from agents.cypher.schema.serializer import serialize_graph_schema
from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.serializer import serialize_sql_schema
from orchestrator.nodes.generate_answer import make_generate_answer_node
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.state import OrchestratorState
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_schema_context() -> tuple[str, str, GraphSchema]:
    """SQL/Cypher 스키마를 프로젝트 YAML에서 읽는다."""
    sql_schema = load_sql_schema(_PROJECT_ROOT / "schema" / "sql_schema.yaml")
    cypher_schema = load_graph_schema(_PROJECT_ROOT / "schema" / "graph_schema.yaml")
    if cypher_schema.query_policy is None:
        raise ValueError("Graph schema requires BOM query policy metadata.")

    return (
        serialize_sql_schema(sql_schema),
        serialize_graph_schema(cypher_schema),
        cypher_schema,
    )


def _execute_sql_stub(sql: str) -> Any:
    """self-correction 구현자가 실제 SQL 검증·실행 로직으로 교체할 자리."""
    raise NotImplementedError("SQL 실행/검증은 self-correction 구현에서 채운다.")


def _execute_cypher_stub(cypher: str) -> Any:
    """self-correction 구현자가 실제 Cypher 검증·실행 로직으로 교체할 자리."""
    raise NotImplementedError("Cypher 실행/검증은 self-correction 구현에서 채운다.")


def _make_sql_agent_node(openai_client: Any, sql_schema_text: str):
    """SQL Agent SubGraph를 감싸 OrchestratorState와 주고받는 노드를 만든다."""
    subgraph = make_sql_agent_subgraph(openai_client, execute_sql=_execute_sql_stub)

    def sql_agent(state: OrchestratorState) -> dict:
        if "sql" not in (state.get("tool_plan") or []):
            return {"sql_query": None, "sql_result": None}
        result = subgraph.invoke(
            {
                "query": state["query"],
                "entity": state.get("entity"),
                "schema": sql_schema_text,
                "messages": [],
                "result": None,
                "error": None,
            }
        )
        return {
            "sql_query": result["messages"][-1]["content"],
            "sql_result": {"result": result["result"], "error": result["error"]},
        }

    return sql_agent


def _make_cypher_agent_node(
    openai_client: Any, cypher_schema_text: str, cypher_query_policy: GraphQueryPolicy
):
    """Cypher Agent SubGraph를 감싸 OrchestratorState와 주고받는 노드를 만든다."""
    subgraph = make_cypher_agent_subgraph(
        openai_client,
        execute_cypher=_execute_cypher_stub,
        query_policy=cypher_query_policy,
    )

    def cypher_agent(state: OrchestratorState) -> dict:
        if "graph" not in (state.get("tool_plan") or []):
            return {"cypher_query": None, "graph_result": None}
        result = subgraph.invoke(
            {
                "query": state["query"],
                "entity": state.get("entity"),
                "schema": cypher_schema_text,
                "messages": [],
                "result": None,
                "error": None,
            }
        )
        return {
            "cypher_query": result["messages"][-1]["content"],
            "graph_result": {"result": result["result"], "error": result["error"]},
        }

    return cypher_agent


# OpenAI/PostgreSQL 클라이언트를 주입받아 컴파일된 그래프를 반환
# START -> resolve_entity -> route_query -> sql_agent -> cypher_agent -> generate_answer -> END
def build_orchestrator_graph(
    openai_client: Any,
    postgres_connection: Any,
) -> CompiledStateGraph:
    sql_schema_text, cypher_schema_text, cypher_schema = _load_schema_context()
    cypher_query_policy = cypher_schema.query_policy
    assert cypher_query_policy is not None

    graph = StateGraph(OrchestratorState)
    # mypy는 factory가 반환하는 `Callable[[OrchestratorState], dict]` 정적 타입을
    # add_node의 `_Node[NodeInputT] | ...` 오버로드 Union과 단일화하지 못해
    # call-overload 오류를 낸다(런타임 시그니처는 `_Node`와 정확히 일치). 이는
    # langgraph 1.2.11의 add_node 오버로드/mypy 2.3.0 조합에서 알려진 타입 추론
    # 한계이며, 인자를 top-level 함수로 직접 넘기면 재현되지 않는다.
    graph.add_node(
        "resolve_entity",
        make_resolve_entity_node(
            openai_client, postgres_connection, cypher_schema
        ),  # type: ignore[call-overload]
    )
    graph.add_node(
        "route_query", make_route_query_node(openai_client)  # type: ignore[call-overload]
    )
    graph.add_node(
        "sql_agent",
        _make_sql_agent_node(openai_client, sql_schema_text),  # type: ignore[call-overload]
    )
    graph.add_node(
        "cypher_agent",
        _make_cypher_agent_node(
            openai_client, cypher_schema_text, cypher_query_policy
        ),  # type: ignore[call-overload]
    )
    graph.add_node(
        "generate_answer",
        make_generate_answer_node(),  # type: ignore[call-overload]
    )
    graph.add_edge(START, "resolve_entity")
    graph.add_edge("resolve_entity", "route_query")
    graph.add_edge("route_query", "sql_agent")
    graph.add_edge("sql_agent", "cypher_agent")
    graph.add_edge("cypher_agent", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()
```

`backend/api/chat.py`의 `chat` 함수 반환문을 다음으로 교체한다(그 외 파일 내용은 그대로 둔다):

```python
    return {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
        "sql_query": result.get("sql_query"),
        "cypher_query": result.get("cypher_query"),
        "final_answer": result.get("final_answer"),
    }
```

`backend/orchestrator/nodes/generate_queries.py`와 `backend/tests/orchestrator/test_generate_queries.py`를 삭제한다:

```bash
git rm backend/orchestrator/nodes/generate_queries.py backend/tests/orchestrator/test_generate_queries.py
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests -v` (전체 스위트)
Expected: 전체 PASS (통합 마크는 기본 설정으로 제외됨). 이어서 `ruff check backend`, `mypy backend` 확인.

- [ ] **Step 5: 커밋**

```bash
git add backend/orchestrator/graph.py backend/api/chat.py backend/tests/orchestrator/test_graph.py backend/tests/api/test_chat.py
git add -u backend/orchestrator/nodes/generate_queries.py backend/tests/orchestrator/test_generate_queries.py
git commit -m "Feat: generate_queries를 SQL/Cypher Agent SubGraph 배선으로 교체"
```

---

## 참고: 이번 플랜에서 다루지 않는 것

- 재시도 개념 전체(루프, 조건부 엣지, 상한, 실패 유형 판단, `RetryExceededError`) — self-correction 담당자가 처음부터 설계
- SQL/Cypher 실제 검증(화이트리스트)·실행 로직 — `_execute_sql_stub`/`_execute_cypher_stub`을 교체하는 것이 다음 작업
- `generate_answer`의 실제 자연어 생성 — 지금은 얇은 문자열 조합 스텁
- 세션/이력 — 별도 플랜/브랜치
