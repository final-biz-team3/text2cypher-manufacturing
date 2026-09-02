# Entity Resolution 일반화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `resolve_entity`가 product 외 supplier/location/scrapReason도 이름으로 찾을 수 있게 일반화하고, 오타·부분 이름에 대해 `pg_trgm` 퍼지 검색으로 후보를 제시해 사용자 확인을 받는 흐름을 구현한다.

**Architecture:** `schema/graph_schema.yaml`의 노드 정의(`source`/`uniqueKey`/`properties.*.sourceColumn`)에서 이름 검색 가능한 엔티티 타입 목록을 동적으로 도출하고, `resolve_entity` 노드가 이 목록으로 Function Calling 스키마와 DB 조회를 조립한다. 정확 일치 우선, 실패 시에만 `pg_trgm` 유사도 폴백, 폴백 결과는 항상 `EntityAmbiguousError`로 확인을 요구한다.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, pydantic v2, psycopg3, PostgreSQL `pg_trgm`, pytest.

## Global Constraints

- 정확 일치 우선 → 실패 시에만 퍼지 폴백 (회귀 없음)
- 퍼지 폴백: 유사도 ≥ 0.3, 최대 5개, score 내림차순
- 폴백 결과가 나오면 항상 `EntityAmbiguousError(candidates)` — 자동 확정 금지
- 후보 0개는 기존과 동일하게 `EntityNotFoundError`
- 후보 JSON 형태: `{id, name, entityType, score}`
- 엔티티 타입 목록은 `graph_schema.yaml`에서 동적으로 도출 — 하드코딩 금지
- category 엔티티 타입은 이번 범위에서 다루지 않음
- 주석은 무엇을 하는지만 짧게 적고, 이유(왜 이렇게 했는지)는 적지 않는다
- 새 브랜치(`dev`에서 분기)에서 작업하고, 테스트는 실행 전 사용자에게 먼저 확인받는다

---

### Task 1: GraphSchema 모델에 source/uniqueKey/sourceColumn 추가

**Files:**
- Modify: `backend/agents/cypher/schema/models.py`
- Test: `backend/tests/agents/cypher/schema/test_cypher_models.py`

**Interfaces:**
- Consumes: 없음 (기존 `PropertySchema`, `NodeSchema`, `GraphSchema` 확장)
- Produces: `NodeSchema.source: NodeSource | None`, `NodeSchema.unique_key: str | None`, `PropertySchema.source_column: str | None` — Task 2가 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/agents/cypher/schema/test_cypher_models.py` 끝에 추가:

```python
def test_node_schema_captures_source_and_unique_key_for_internal_use() -> None:
    """source·uniqueKey·sourceColumn을 파싱하되 model_dump에는 포함하지 않는다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "source": {"schema": "production", "table": "product"},
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                    },
                },
            },
            "relationships": {},
        }
    )

    node = schema.nodes["Product"]
    assert node.unique_key == "productId"
    assert node.source is not None
    assert node.source.schema_name == "production"
    assert node.source.table == "product"
    assert node.properties["productId"].source_column == "productid"
    assert "unique_key" not in schema.model_dump()["nodes"]["Product"]
    assert "source" not in schema.model_dump()["nodes"]["Product"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/agents/cypher/schema/test_cypher_models.py::test_node_schema_captures_source_and_unique_key_for_internal_use -v`
Expected: FAIL — `AttributeError: 'NodeSchema' object has no attribute 'unique_key'`

- [ ] **Step 3: 최소 구현**

`backend/agents/cypher/schema/models.py`를 다음과 같이 수정한다 (기존 `PropertySchema`/`NodeSchema` 클래스를 아래 내용으로 교체하고 `NodeSource`를 새로 추가):

```python
class PropertySchema(_SchemaModel):
    """노드 또는 관계 속성의 데이터 타입을 표현한다."""

    data_type: PropertyDataType = Field(alias="type")
    aliases: list[str] = Field(default_factory=list)
    source_column: str | None = Field(
        default=None, alias="sourceColumn", exclude=True
    )


class NodeSource(_SchemaModel):
    """노드가 매핑되는 PostgreSQL 스키마·테이블을 표현한다."""

    schema_name: str = Field(alias="schema")
    table: str


class NodeSchema(_SchemaModel):
    """노드가 가지는 속성을 표현한다."""

    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, PropertySchema]
    unique_key: str | None = Field(default=None, alias="uniqueKey", exclude=True)
    source: NodeSource | None = Field(default=None, exclude=True)
```

(`RelationshipSchema`, `GraphQueryPolicy`, `GraphSchema`는 변경하지 않는다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests/agents/cypher/schema/test_cypher_models.py -v`
Expected: 전체 PASS (기존 `test_graph_schema_maps_query_policy_and_ignores_other_metadata`의 `model_dump()` 정확 일치 검증도 그대로 통과해야 함)

- [ ] **Step 5: 커밋**

```bash
git add backend/agents/cypher/schema/models.py backend/tests/agents/cypher/schema/test_cypher_models.py
git commit -m "feat: GraphSchema에 source/uniqueKey/sourceColumn 파싱 추가"
```

---

### Task 2: 그래프 스키마에서 이름 검색 가능한 엔티티 타입 도출

**Files:**
- Create: `backend/agents/cypher/schema/entity_types.py`
- Test: `backend/tests/agents/cypher/schema/test_entity_types.py`

**Interfaces:**
- Consumes: `GraphSchema`(Task 1), `load_graph_schema`(`backend/agents/cypher/schema/loader.py`)
- Produces: `NamedEntityType(entity_type: str, table: str, id_column: str, name_column: str, id_field: str, name_field: str)`, `list_named_entity_types(schema: GraphSchema) -> list[NamedEntityType]` — Task 3이 사용

- [ ] **Step 1: 실패하는 테스트 작성**

Create `backend/tests/agents/cypher/schema/test_entity_types.py`:

```python
"""그래프 스키마에서 이름으로 검색 가능한 엔티티 타입을 도출하는 동작을 테스트한다."""

from pathlib import Path

from agents.cypher.schema.entity_types import list_named_entity_types
from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphSchema

PROJECT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[5] / "schema" / "graph_schema.yaml"
)


def test_list_named_entity_types_includes_only_nodes_with_name_and_source() -> None:
    """name 속성과 source·uniqueKey·sourceColumn이 모두 있는 노드만 포함한다."""
    schema = GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "source": {"schema": "production", "table": "product"},
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "WorkOrder": {
                    "uniqueKey": "workOrderId",
                    "source": {"schema": "production", "table": "workorder"},
                    "properties": {
                        "workOrderId": {
                            "type": "INTEGER",
                            "sourceColumn": "workorderid",
                        },
                    },
                },
            },
            "relationships": {},
        }
    )

    entity_types = list_named_entity_types(schema)

    assert len(entity_types) == 1
    assert entity_types[0].entity_type == "product"
    assert entity_types[0].table == "production.product"
    assert entity_types[0].id_column == "productid"
    assert entity_types[0].name_column == "name"
    assert entity_types[0].id_field == "productId"
    assert entity_types[0].name_field == "productName"


def test_list_named_entity_types_loads_project_schema() -> None:
    """프로젝트 기준 YAML에서 이름 있는 노드 4종을 도출한다."""
    schema = load_graph_schema(PROJECT_SCHEMA_PATH)

    entity_types = {
        entity.entity_type: entity for entity in list_named_entity_types(schema)
    }

    assert set(entity_types) == {"product", "supplier", "location", "scrapReason"}
    assert entity_types["supplier"].table == "purchasing.vendor"
    assert entity_types["supplier"].id_column == "businessentityid"
    assert entity_types["supplier"].id_field == "supplierId"
    assert entity_types["scrapReason"].table == "production.scrapreason"
    assert entity_types["scrapReason"].name_field == "scrapReasonName"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/agents/cypher/schema/test_entity_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.cypher.schema.entity_types'`

- [ ] **Step 3: 최소 구현**

Create `backend/agents/cypher/schema/entity_types.py`:

```python
"""그래프 스키마에서 이름으로 검색 가능한 엔티티 타입 목록을 만든다."""

from dataclasses import dataclass

from agents.cypher.schema.models import GraphSchema


@dataclass(frozen=True)
class NamedEntityType:
    """이름으로 검색 가능한 엔티티 하나의 조회 정보를 담는다."""

    entity_type: str
    table: str
    id_column: str
    name_column: str
    id_field: str
    name_field: str


def list_named_entity_types(schema: GraphSchema) -> list[NamedEntityType]:
    """name 속성을 가진 노드를 엔티티 타입 목록으로 변환한다."""
    entity_types: list[NamedEntityType] = []

    for node_name, node in schema.nodes.items():
        if "name" not in node.properties:
            continue
        if node.source is None or node.unique_key is None:
            continue
        if node.unique_key not in node.properties:
            continue

        id_source_column = node.properties[node.unique_key].source_column
        name_source_column = node.properties["name"].source_column
        if id_source_column is None or name_source_column is None:
            continue

        entity_type = node_name[0].lower() + node_name[1:]

        entity_types.append(
            NamedEntityType(
                entity_type=entity_type,
                table=f"{node.source.schema_name}.{node.source.table}",
                id_column=id_source_column,
                name_column=name_source_column,
                id_field=node.unique_key,
                name_field=f"{entity_type}Name",
            )
        )

    return entity_types
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests/agents/cypher/schema/test_entity_types.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/agents/cypher/schema/entity_types.py backend/tests/agents/cypher/schema/test_entity_types.py
git commit -m "feat: 그래프 스키마에서 이름 검색 가능한 엔티티 타입 도출 추가"
```

---

### Task 3: resolve_entity 정확 일치를 다중 엔티티 타입으로 일반화

**Files:**
- Modify: `backend/orchestrator/nodes/resolve_entity.py`
- Modify: `backend/orchestrator/graph.py`
- Modify: `backend/tests/orchestrator/test_resolve_entity.py`
- Modify: `backend/tests/orchestrator/test_graph.py`

**Interfaces:**
- Consumes: `NamedEntityType`, `list_named_entity_types`(Task 2), `GraphSchema`(Task 1)
- Produces: `make_resolve_entity_node(openai_client, postgres_connection, graph_schema: GraphSchema) -> Callable[[OrchestratorState], dict]` — Task 4, Task 6이 이 시그니처를 그대로 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/orchestrator/test_resolve_entity.py`를 다음 내용으로 교체한다:

```python
"""resolve_entity 노드가 엔티티를 확정하거나 통과시키는 동작을 테스트한다."""

import pytest

from agents.cypher.schema.models import GraphSchema
from orchestrator.errors import EntityNotFoundError
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from tests.mocks.openai import (
    MockOpenAIClient,
    make_no_tool_call_response,
    make_tool_call_response,
)
from tests.mocks.postgres import MockPostgresConnection


def _graph_schema() -> GraphSchema:
    return GraphSchema.model_validate(
        {
            "nodes": {
                "Product": {
                    "uniqueKey": "productId",
                    "source": {"schema": "production", "table": "product"},
                    "properties": {
                        "productId": {"type": "INTEGER", "sourceColumn": "productid"},
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
                "Supplier": {
                    "uniqueKey": "supplierId",
                    "source": {"schema": "purchasing", "table": "vendor"},
                    "properties": {
                        "supplierId": {
                            "type": "INTEGER",
                            "sourceColumn": "businessentityid",
                        },
                        "name": {"type": "STRING", "sourceColumn": "name"},
                    },
                },
            },
            "relationships": {},
        }
    )


def test_resolve_entity_returns_entity_when_product_found() -> None:
    """질의에서 추출한 제품명이 DB에 있으면 productId를 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Touring-1000 Yellow, 54": (956, "Touring-1000 Yellow, 54")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."})

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert openai_client.calls[0]["reasoning_effort"] == "none"


def test_resolve_entity_returns_entity_when_supplier_found() -> None:
    """질의에서 추출한 업체명이 DB에 있으면 supplierId를 확정한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "supplier", "entityName": "Allenson Cycles"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={"Allenson Cycles": (1494, "Allenson Cycles")}
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "공급업체 Allenson Cycles가 공급하는 부품을 알려줘."})

    assert result == {
        "entity": {"supplierId": 1494, "supplierName": "Allenson Cycles"}
    }


def test_resolve_entity_returns_none_entity_when_no_entity_mentioned() -> None:
    """특정 대상을 지칭하지 않는 질의는 DB 조회 없이 entity=None으로 통과한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result == {"entity": None}
    assert postgres_connection.last_query is None


def test_resolve_entity_raises_when_entity_not_found_and_no_similar_names() -> None:
    """추출된 이름이 DB에 없고 유사한 이름도 없으면 EntityNotFoundError를 발생시킨다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "존재하지 않는 제품"},
        )
    )
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    with pytest.raises(EntityNotFoundError):
        node({"query": "존재하지 않는 제품의 정가를 알려줘."})


def test_resolve_entity_requires_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENAI_MODEL이 없으면 추출 요청 전에 즉시 실패한다."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    node = make_resolve_entity_node(
        openai_client,
        MockPostgresConnection(rows_by_name={}),
        _graph_schema(),
    )

    with pytest.raises(KeyError, match="OPENAI_MODEL"):
        node({"query": "제품의 정가를 알려줘."})

    assert openai_client.calls == []
```

`backend/tests/orchestrator/test_graph.py`에서 다음 두 곳을 수정한다:

- `test_graph_resolves_entity_then_routes_to_sql`의 `make_tool_call_response("extract_product_name", {"productName": "Touring-1000 Yellow, 54"})`를
  `make_tool_call_response("extract_entity", {"entityType": "product", "entityName": "Touring-1000 Yellow, 54"})`로 변경
- `test_graph_routes_to_graph_for_relationship_query`의 `make_tool_call_response("extract_product_name", {"productName": "Paint - Black"})`를
  `make_tool_call_response("extract_entity", {"entityType": "product", "entityName": "Paint - Black"})`로 변경

(`test_graph_generates_sql_without_entity_for_aggregate_query`는 `make_content_response`를 쓰므로 변경하지 않는다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/orchestrator/test_resolve_entity.py backend/tests/orchestrator/test_graph.py -v`
Expected: FAIL — `TypeError: make_resolve_entity_node() missing 1 required positional argument: 'graph_schema'`

- [ ] **Step 3: 최소 구현**

`backend/orchestrator/nodes/resolve_entity.py`를 다음 내용으로 전부 교체한다 (퍼지 폴백은 Task 4에서 추가):

```python
import json
import logging
import os
from collections.abc import Callable
from typing import Any

from agents.cypher.schema.entity_types import NamedEntityType, list_named_entity_types
from agents.cypher.schema.models import GraphSchema
from orchestrator.errors import EntityNotFoundError
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "사용자 질의에 특정 대상을 지칭하는 이름이 있으면 "
    "extract_entity를 호출한다. 없으면 아무 도구도 호출하지 않는다."
)


def _build_extract_entity_tool(entity_types: list[NamedEntityType]) -> dict:
    """엔티티 타입 목록으로 Function Calling 도구 정의를 만든다."""
    return {
        "type": "function",
        "function": {
            "name": "extract_entity",
            "description": (
                "자연어 질의에서 특정 대상을 지칭하는 이름과 그 종류를 추출한다. "
                "질의가 특정 대상을 가리키지 않으면 호출하지 않는다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entityType": {
                        "type": "string",
                        "enum": [entity.entity_type for entity in entity_types],
                    },
                    "entityName": {
                        "type": "string",
                        "description": "질의에 등장하는 이름 문자열 그대로",
                    },
                },
                "required": ["entityType", "entityName"],
            },
        },
    }


def _extract_entity(
    query: str, openai_client: Any, extract_tool: dict
) -> tuple[str, str] | None:
    """LLM Function Calling으로 질의에서 엔티티 타입과 이름을 추출한다."""
    response = openai_client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        tools=[extract_tool],
        reasoning_effort="none",
    )
    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        return None
    arguments = json.loads(tool_calls[0].function.arguments)
    return arguments["entityType"], arguments["entityName"]


def _entity_type_config(
    entity_type: str, entity_types: list[NamedEntityType]
) -> NamedEntityType:
    """엔티티 타입 이름으로 조회 설정을 찾는다."""
    for config in entity_types:
        if config.entity_type == entity_type:
            return config
    raise ValueError(f"Unknown entity type: {entity_type}")


def _find_entity_by_name(
    entity_type: str,
    name: str,
    postgres_connection: Any,
    entity_types: list[NamedEntityType],
) -> dict | None:
    """엔티티 타입별 테이블·컬럼으로 이름을 정확 일치 조회한다."""
    config = _entity_type_config(entity_type, entity_types)
    cursor = postgres_connection.execute(
        f"SELECT {config.id_column}, {config.name_column} "
        f"FROM {config.table} WHERE {config.name_column} = %s",
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {config.id_field: row[0], config.name_field: row[1]}


def make_resolve_entity_node(
    openai_client: Any, postgres_connection: Any, graph_schema: GraphSchema
) -> Callable[[OrchestratorState], dict]:
    """OpenAI/PostgreSQL 클라이언트와 그래프 스키마를 주입받은 resolve_entity 노드를 만든다."""
    entity_types = list_named_entity_types(graph_schema)
    extract_tool = _build_extract_entity_tool(entity_types)

    def resolve_entity(state: OrchestratorState) -> dict:
        extraction = _extract_entity(state["query"], openai_client, extract_tool)
        if extraction is None:
            logger.info(
                "resolve_entity: query=%r -> entity=None (대상 미언급)", state["query"]
            )
            return {"entity": None}

        entity_type, entity_name = extraction
        entity = _find_entity_by_name(
            entity_type, entity_name, postgres_connection, entity_types
        )
        if entity is None:
            logger.info(
                "resolve_entity: query=%r -> entityType=%r entityName=%r 조회 실패 "
                "(EntityNotFoundError)",
                state["query"],
                entity_type,
                entity_name,
            )
            raise EntityNotFoundError()

        logger.info("resolve_entity: query=%r -> entity=%s", state["query"], entity)
        return {"entity": entity}

    return resolve_entity
```

`backend/orchestrator/graph.py`의 import부터 수정한다. 기존 `from agents.cypher.schema.models import GraphQueryPolicy`를 다음으로 교체한다(그래프 스키마 객체 자체를 다루므로 `GraphSchema`가 필요하고, `GraphQueryPolicy`는 이 파일에서 더 이상 타입으로 쓰이지 않아 제거한다):

```python
from agents.cypher.schema.models import GraphSchema
```

이어서 `_load_schema_context`와 `build_orchestrator_graph`를 수정한다:

```python
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
```

`build_orchestrator_graph` 안에서 `_load_schema_context()` 호출부와 `resolve_entity` 노드 등록부를 수정한다:

```python
    sql_schema_text, cypher_schema_text, cypher_schema = _load_schema_context()
    cypher_query_policy = cypher_schema.query_policy
    assert cypher_query_policy is not None

    graph = StateGraph(OrchestratorState)
    graph.add_node(
        "resolve_entity",
        make_resolve_entity_node(
            openai_client, postgres_connection, cypher_schema
        ),  # type: ignore[call-overload]
    )
```

(`make_generate_queries_node`는 `cypher_query_policy` 값을 그대로 전달받을 뿐 `graph.py`가 `GraphQueryPolicy`를 타입으로 참조하지는 않으므로, 이 파일에서 해당 import를 제거해도 `ruff`의 미사용 import 검사에 걸리지 않는다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests/orchestrator/test_resolve_entity.py backend/tests/orchestrator/test_graph.py backend/tests/orchestrator/test_generate_queries.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/orchestrator/nodes/resolve_entity.py backend/orchestrator/graph.py backend/tests/orchestrator/test_resolve_entity.py backend/tests/orchestrator/test_graph.py
git commit -m "feat: resolve_entity 정확 일치를 product 외 엔티티 타입으로 일반화"
```

---

### Task 4: pg_trgm 퍼지 폴백 + EntityAmbiguousError 연결

**Files:**
- Modify: `backend/orchestrator/nodes/resolve_entity.py`
- Modify: `backend/tests/mocks/postgres.py`
- Modify: `backend/tests/orchestrator/test_resolve_entity.py`

**Interfaces:**
- Consumes: `EntityAmbiguousError`(`backend/orchestrator/errors.py`, 기존 구현 재사용)
- Produces: 없음 (resolve_entity의 최종 동작)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/mocks/postgres.py`를 다음 내용으로 교체한다:

```python
"""엔티티 정확 일치·유사도 조회 결과를 반환하는 PostgreSQL 테스트 mock."""

from typing import Any


class _MockCursor:
    def __init__(
        self,
        row: tuple[Any, ...] | None,
        rows: list[tuple[Any, ...]],
    ) -> None:
        self._row = row
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class MockPostgresConnection:
    """이름별 정확 일치·유사도 조회 결과를 반환하고 마지막 execute 호출을 기록한다."""

    def __init__(
        self,
        rows_by_name: dict[str, tuple[Any, ...]],
        similar_rows_by_name: dict[str, list[tuple[Any, ...]]] | None = None,
    ) -> None:
        self._rows_by_name = rows_by_name
        self._similar_rows_by_name = similar_rows_by_name or {}
        self.last_query: tuple[str, tuple[Any, ...]] | None = None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _MockCursor:
        self.last_query = (query, params)
        if not params:
            return _MockCursor(None, [])
        name = params[0]
        if "similarity(" in query:
            return _MockCursor(None, self._similar_rows_by_name.get(name, []))
        return _MockCursor(self._rows_by_name.get(name), [])
```

`backend/tests/orchestrator/test_resolve_entity.py` 상단의 `from orchestrator.errors import EntityNotFoundError`를 다음으로 교체한다:

```python
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError
```

파일 끝에 테스트를 추가한다:

```python
def test_resolve_entity_raises_ambiguous_with_similar_candidates() -> None:
    """정확 일치가 없고 유사한 이름이 있으면 EntityAmbiguousError로 후보를 제시한다."""
    openai_client = MockOpenAIClient(
        make_tool_call_response(
            "extract_entity",
            {"entityType": "product", "entityName": "터치링 자전거"},
        )
    )
    postgres_connection = MockPostgresConnection(
        rows_by_name={},
        similar_rows_by_name={
            "터치링 자전거": [
                (956, "Touring-1000 Yellow, 54", 0.62),
                (957, "Touring-2000 Blue, 60", 0.41),
            ]
        },
    )
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    with pytest.raises(EntityAmbiguousError) as excinfo:
        node({"query": "터치링 자전거 정가 알려줘."})

    assert excinfo.value.candidates == [
        {
            "id": 956,
            "name": "Touring-1000 Yellow, 54",
            "entityType": "product",
            "score": 0.62,
        },
        {
            "id": 957,
            "name": "Touring-2000 Blue, 60",
            "entityType": "product",
            "score": 0.41,
        },
    ]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/orchestrator/test_resolve_entity.py -v`
Expected: FAIL — `Failed: DID NOT RAISE <class 'orchestrator.errors.EntityAmbiguousError'>` (현재는 EntityNotFoundError가 발생)

- [ ] **Step 3: 최소 구현**

`backend/orchestrator/nodes/resolve_entity.py`에서 import에 `EntityAmbiguousError`를 추가하고, 상수와 함수를 추가한 뒤 `resolve_entity` 본문을 수정한다:

```python
from orchestrator.errors import EntityAmbiguousError, EntityNotFoundError

_SIMILARITY_THRESHOLD = 0.3
_MAX_CANDIDATES = 5


def _find_similar_entities(
    entity_type: str,
    name: str,
    postgres_connection: Any,
    entity_types: list[NamedEntityType],
) -> list[dict]:
    """엔티티 타입별 테이블·컬럼으로 유사한 이름을 유사도 순으로 조회한다."""
    config = _entity_type_config(entity_type, entity_types)
    cursor = postgres_connection.execute(
        f"SELECT {config.id_column}, {config.name_column}, "
        f"similarity({config.name_column}, %s) AS score "
        f"FROM {config.table} "
        f"WHERE similarity({config.name_column}, %s) >= %s "
        f"ORDER BY score DESC LIMIT %s",
        (name, name, _SIMILARITY_THRESHOLD, _MAX_CANDIDATES),
    )
    return [
        {
            "id": row[0],
            "name": row[1],
            "entityType": entity_type,
            "score": row[2],
        }
        for row in cursor.fetchall()
    ]
```

`resolve_entity` 함수 안, `entity is None` 분기를 다음으로 교체한다:

```python
        if entity is None:
            candidates = _find_similar_entities(
                entity_type, entity_name, postgres_connection, entity_types
            )
            if candidates:
                logger.info(
                    "resolve_entity: query=%r -> entityName=%r 후보 %d개 "
                    "(EntityAmbiguousError)",
                    state["query"],
                    entity_name,
                    len(candidates),
                )
                raise EntityAmbiguousError(candidates)

            logger.info(
                "resolve_entity: query=%r -> entityType=%r entityName=%r 조회 실패 "
                "(EntityNotFoundError)",
                state["query"],
                entity_type,
                entity_name,
            )
            raise EntityNotFoundError()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests/orchestrator/test_resolve_entity.py backend/tests/orchestrator/test_graph.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/orchestrator/nodes/resolve_entity.py backend/tests/mocks/postgres.py backend/tests/orchestrator/test_resolve_entity.py
git commit -m "feat: 정확 일치 실패 시 pg_trgm 유사도 폴백과 EntityAmbiguousError 연결"
```

---

### Task 5: pg_trgm 확장 부트스트랩

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `get_connection`(`backend/core/postgres.py`, 기존 구현 재사용)
- Produces: 없음 (앱 시작 시 DB 부수효과)

이 작업은 실제 PostgreSQL 연결이 있어야 검증되고 기존에 `main.py`를 대상으로 한 자동화 테스트가 없으므로, 자동화된 pytest 대신 수동 확인 절차로 작성한다.

- [ ] **Step 1: 구현**

`backend/main.py`의 `lifespan` 함수를 다음으로 교체한다:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    get_driver()
    connection = get_connection()
    connection.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    connection.commit()
    yield
    close_driver()
    close_connection()
```

- [ ] **Step 2: 수동 확인**

```bash
docker compose up -d postgres
docker exec -it postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\dx"
```

Expected: 출력 목록에 `pg_trgm`이 포함됨 (백엔드를 한 번 기동해 lifespan이 실행된 뒤 확인)

- [ ] **Step 3: 커밋**

```bash
git add backend/main.py
git commit -m "feat: 앱 시작 시 pg_trgm 확장을 보장"
```

---

### Task 6: confirmed_entity 재진입 필드 추가

**Files:**
- Modify: `backend/orchestrator/state.py`
- Modify: `backend/orchestrator/nodes/resolve_entity.py`
- Modify: `backend/api/chat.py`
- Modify: `backend/tests/orchestrator/test_resolve_entity.py`
- Create: `backend/tests/api/__init__.py`
- Create: `backend/tests/api/test_chat.py`

**Interfaces:**
- Consumes: `make_resolve_entity_node`(Task 3), `build_orchestrator_graph`(`backend/orchestrator/graph.py`)
- Produces: `OrchestratorState.confirmed_entity: dict | None`, `ChatRequest.confirmed_entity: dict | None`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/orchestrator/test_resolve_entity.py`에 추가:

```python
def test_resolve_entity_returns_confirmed_entity_without_matching() -> None:
    """confirmed_entity가 있으면 매칭 없이 그대로 확정한다."""
    openai_client = MockOpenAIClient(make_no_tool_call_response())
    postgres_connection = MockPostgresConnection(rows_by_name={})
    node = make_resolve_entity_node(openai_client, postgres_connection, _graph_schema())

    result = node(
        {
            "query": "그 제품 정가 알려줘.",
            "confirmed_entity": {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
            },
        }
    )

    assert result == {
        "entity": {"productId": 956, "productName": "Touring-1000 Yellow, 54"}
    }
    assert openai_client.calls == []
    assert postgres_connection.last_query is None
```

Create `backend/tests/api/__init__.py` (빈 파일).

Create `backend/tests/api/test_chat.py`:

```python
"""POST /chat 핸들러가 confirmed_entity를 오케스트레이터에 전달하는 동작을 테스트한다."""

import asyncio

import pytest

import api.chat as chat_module
from api.chat import ChatRequest, chat
from tests.mocks.openai import MockOpenAIClient, make_content_response
from tests.mocks.postgres import MockPostgresConnection


def test_chat_passes_confirmed_entity_to_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirmed_entity가 있으면 매칭 없이 바로 라우팅으로 넘어간다."""
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
        lambda: MockPostgresConnection(rows_by_name={}),
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
    assert len(openai_client.calls) == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest backend/tests/orchestrator/test_resolve_entity.py backend/tests/api/test_chat.py -v`
Expected: FAIL — `test_resolve_entity_returns_confirmed_entity_without_matching`는 `openai_client.calls == []` 실패(실제로는 1회 호출됨), `test_chat_passes_confirmed_entity_to_orchestrator`는 `ChatRequest`가 `confirmed_entity`를 모르는 필드로 거부(`ValidationError`)

- [ ] **Step 3: 최소 구현**

`backend/orchestrator/state.py`의 `OrchestratorState`에 필드 추가:

```python
    # 이전 턴에 사용자가 확인한 entity (있으면 resolve_entity가 매칭을 건너뜀)
    confirmed_entity: NotRequired[dict | None]
```

`backend/orchestrator/nodes/resolve_entity.py`의 `resolve_entity` 함수 맨 앞에 추가:

```python
    def resolve_entity(state: OrchestratorState) -> dict:
        confirmed_entity = state.get("confirmed_entity")
        if confirmed_entity is not None:
            logger.info(
                "resolve_entity: query=%r -> confirmed_entity=%s (재진입)",
                state["query"],
                confirmed_entity,
            )
            return {"entity": confirmed_entity}

        extraction = _extract_entity(state["query"], openai_client, extract_tool)
```

`backend/api/chat.py`를 다음으로 교체한다:

```python
from fastapi import APIRouter
from pydantic import BaseModel

from core.openai_client import get_openai_client
from core.postgres import get_connection
from orchestrator.graph import build_orchestrator_graph

router = APIRouter()


class ChatRequest(BaseModel):
    query: str
    confirmed_entity: dict | None = None


@router.post("/chat")
async def chat(request: ChatRequest):
    graph = build_orchestrator_graph(get_openai_client(), get_connection())
    result = graph.invoke(
        {"query": request.query, "confirmed_entity": request.confirmed_entity}
    )
    return {
        "query": result["query"],
        "entity": result.get("entity"),
        "tool_plan": result.get("tool_plan"),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest backend/tests -v`
Expected: 전체 PASS (`integration` 마크는 기본 설정으로 제외됨)

- [ ] **Step 5: 커밋**

```bash
git add backend/orchestrator/state.py backend/orchestrator/nodes/resolve_entity.py backend/api/chat.py backend/tests/orchestrator/test_resolve_entity.py backend/tests/api/__init__.py backend/tests/api/test_chat.py
git commit -m "feat: confirmed_entity로 resolve_entity 매칭을 건너뛰는 재진입 흐름 추가"
```

---

## 참고: 이번 플랜에서 다루지 않는 것

- self-correction 뼈대, 세션/이력 — 각각 별도 플랜/브랜치
- `production.productcategory`(category) 엔티티 타입
- `EntityAmbiguousError`의 `candidates`를 프론트에서 실제로 렌더링하는 UI
