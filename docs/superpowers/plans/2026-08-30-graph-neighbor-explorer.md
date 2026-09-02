# Neo4j 그래프 이웃 탐색기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로그인한 사용자가 이름으로 엔티티를 검색하고, 그 엔티티의 실제 Neo4j 1-hop 이웃을 인터랙티브한 force-directed 그래프로 볼 수 있는 `/explore` 화면을 만든다.

**Architecture:** 백엔드에 `GET /graph/search`(이름 검색)와 `GET /graph/{entityType}/{id}/neighbors`(1-hop 이웃 조회, 관계 타입별 상한+총개수) 두 엔드포인트를 추가한다. `resolve_entity.py`의 이름 검색 로직을 `orchestrator/entity_search.py`로 뽑아 두 곳에서 공유한다. 프론트는 이미 있는 `PathGraphView`(BOM 경로 그래프뷰)를 일반화해서(실제 라벨별 색상, 임의 관계 라벨, 중심 노드 강조, 자유 배치 모드) 새 `/explore` 화면에서 재사용한다.

**Tech Stack:** FastAPI, neo4j async driver(`execute_query`), psycopg_pool, React, react-force-graph-2d(이미 설치돼 있음 - 스펙 작성 시점엔 미설치였으나 이번 세션에 다른 작업으로 이미 추가됨), zod, vitest.

**Spec:** `docs/superpowers/specs/2026-08-27-graph-neighbor-explorer-design.md`

## Global Constraints

- 관계별 이웃 상한은 `GRAPH_NEIGHBOR_LIMIT` 환경변수(기본값 50)로 조정 가능해야 한다 — spec 1-3.
- 이름 검색 유사도 임계값 0.3, 최대 후보 5개(기존 `resolve_entity.py` 상수 그대로 재사용) — spec 1-1.
- `resolve_entity.py`를 리팩터링해도 `tests/orchestrator/test_resolve_entity.py`는 수정 없이 그대로 통과해야 한다(순수 리팩터링) — spec 1-1.
- 새 엔드포인트 둘 다 로그인 필요(`Depends(get_current_user)`), 그 외 권한 제한 없음 — spec 1-2, 1-3.
- `WorkOrder`/`RoutingOperation`은 검색 시작점이 될 수 없다(name 속성 없음) — spec 1-2. `list_resolvable_entity_types`가 이미 이 필터링을 한다(entity_types.py:47 `if "name" not in node.properties: continue`).
- 이번 범위에 없는 것(비목표): 이웃 노드 클릭 후 확장(멀티홉), 상한 초과분 스크롤 리스트, 쓰기, Postgres 원본 데이터 화면.
- 프론트 유닛 테스트는 vitest로 순수 로직(`buildNeighborGraph` 등)만 커버한다. UI/렌더링은 자동화 테스트 없이 브라우저로 수동 확인한다(스펙 작성 시점엔 "vitest 자체가 없다"고 돼 있었지만, 이번 세션에 `pathGraph.test.ts`로 이미 도입됐다 — 이 부분만 스펙 대비 갱신).

---

## File Structure

**백엔드**
- Create: `backend/orchestrator/entity_search.py` — 이름 검색 순수 로직(`find_entity_by_name`, `find_similar_entities`, 상수)
- Modify: `backend/orchestrator/nodes/resolve_entity.py` — 위 로직을 로컬 정의 대신 import
- Create: `backend/core/json_safe.py` — Neo4j temporal 값을 다루는 JSON 안전 변환 유틸(`api/chat.py`에서 추출)
- Modify: `backend/api/chat.py` — `_to_json_safe` 대신 `core/json_safe.to_json_safe` 사용
- Create: `backend/api/graph.py` — `GET /graph/search`, `GET /graph/{entityType}/{id}/neighbors`
- Modify: `backend/main.py` — `graph_router` 등록
- Modify: `backend/tests/mocks/neo4j.py` — `driver.execute_query(...)`를 흉내내는 mock 추가
- Create: `backend/tests/orchestrator/test_entity_search.py`
- Create: `backend/tests/api/test_graph.py`
- Modify: `.env.example` — `GRAPH_NEIGHBOR_LIMIT` 문서화

**프론트엔드**
- Modify: `frontend/src/lib/pathGraph.ts` — `PathGraphNode`에 `entityLabel`/`properties` 추가, `PathGraphEdge`에 `label` 추가(그래프 전체 `relationshipLabel` 제거)
- Modify: `frontend/src/lib/pathGraph.test.ts` — 위 변경에 맞춰 갱신
- Modify: `frontend/src/screens/Dashboard.tsx` — `PathGraphView`에 `dagMode="lr"` 명시적으로 전달
- Modify: `frontend/src/components/result/PathGraphView.tsx` — `dagMode`/`centerNodeId` 선택적 prop, 실제 라벨 기반 색상+범례, 속성 기반 상세 패널
- Modify: `frontend/src/lib/schemas.ts` — `GraphSearchResultSchema`, `GraphNeighborsResponseSchema`
- Create: `frontend/src/lib/graph.ts` — `searchGraphEntities`, `fetchGraphNeighbors`, `buildNeighborGraph`
- Create: `frontend/src/lib/graph.test.ts`
- Create: `frontend/src/screens/GraphExplorer.tsx`
- Modify: `frontend/src/App.tsx` — `/explore` 라우트(보호됨)
- Modify: `frontend/src/components/layout/TopBar.tsx` — 탐색 화면 진입 버튼(로그인 시에만)

---

### Task 1: 이름 검색 로직을 `entity_search.py`로 추출

**Files:**
- Create: `backend/orchestrator/entity_search.py`
- Modify: `backend/orchestrator/nodes/resolve_entity.py:1-19,139-194` (import로 교체, 함수 본문 삭제)
- Test: `backend/tests/orchestrator/test_entity_search.py`

**Interfaces:**
- Produces: `find_entity_by_name(config: NamedEntityType, name: str, pool: AsyncConnectionPool) -> dict | None`, `find_similar_entities(config: NamedEntityType, name: str, pool: AsyncConnectionPool) -> list[dict]`, `SIMILARITY_THRESHOLD: float = 0.3`, `MAX_CANDIDATES: int = 5` — Task 6(`api/graph.py`)이 이 네 개를 그대로 가져다 쓴다.

- [ ] **Step 1: 새 모듈에 기존 로직을 그대로 옮겨 쓴다(동작 변경 없음)**

`backend/orchestrator/entity_search.py`:

```python
"""이름으로 엔티티를 정확 일치·유사도로 조회하는 순수 로직.
resolve_entity.py(LLM 추출 이름 확정)와 api/graph.py(자유 텍스트 검색)가
공유한다."""

import logging

import psycopg
from psycopg_pool import AsyncConnectionPool

from orchestrator.entity_types import NamedEntityType

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.3
MAX_CANDIDATES = 5


async def find_entity_by_name(
    config: NamedEntityType,
    name: str,
    pool: AsyncConnectionPool,
) -> dict | None:
    """엔티티 타입별 테이블·컬럼으로 이름을 정확 일치 조회한다.
    이 함수(와 아래 조회 함수들)는 여기서 명시적으로 commit/rollback을
    호출하지 않는다 - pool.connection()이 `async with conn:`으로 커넥션을
    감싸 블록을 정상 종료할 때 자동으로 commit한다(psycopg 표준 동작).
    SELECT뿐이라 commit이든 rollback이든 결과에 차이가 없다."""
    async with pool.connection() as conn:
        cursor = await conn.execute(
            f"SELECT {config.id_column}, {config.name_column} "
            f"FROM {config.table} WHERE {config.name_column} = %s",
            (name,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {config.id_field: row[0], config.name_field: row[1]}


async def find_similar_entities(
    config: NamedEntityType,
    name: str,
    pool: AsyncConnectionPool,
) -> list[dict]:
    """엔티티 타입별 테이블·컬럼으로 유사한 이름을 유사도 순으로 조회한다.
    pg_trgm을 쓸 수 없으면 롤백 후 후보 없음으로 처리한다."""
    async with pool.connection() as conn:
        try:
            cursor = await conn.execute(
                f"SELECT {config.id_column}, {config.name_column}, "
                f"similarity({config.name_column}, %s) AS score "
                f"FROM {config.table} "
                f"WHERE similarity({config.name_column}, %s) >= %s "
                f"ORDER BY score DESC LIMIT %s",
                (name, name, SIMILARITY_THRESHOLD, MAX_CANDIDATES),
            )
        except psycopg.errors.UndefinedFunction:
            await conn.rollback()
            logger.warning(
                "entity_search: pg_trgm 유사도 검색을 사용할 수 없어 후보 없음으로 처리"
            )
            return []
        rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "entityType": config.entity_type,
            "score": row[2],
            "entity": {config.id_field: row[0], config.name_field: row[1]},
        }
        for row in rows
    ]
```

- [ ] **Step 2: `resolve_entity.py`가 새 모듈을 import해서 쓰도록 고친다**

`backend/orchestrator/nodes/resolve_entity.py`에서:
- 기존 `import psycopg`, `from psycopg_pool import AsyncConnectionPool` 줄 제거(더 이상 이 파일에서 직접 안 씀).
- `from orchestrator.entity_search import find_entity_by_name, find_similar_entities` 추가.
- `_SIMILARITY_THRESHOLD`/`_MAX_CANDIDATES` 상수 정의 제거(더 이상 이 파일에서 안 씀 - `_find_similar_entities` 본문에서만 쓰였다).
- `_find_entity_by_name`/`_find_similar_entities` 함수 정의(139-194줄) 전체 삭제.
- 함수 본문에서 `_find_entity_by_name(...)`를 호출하던 322번째 줄 근처, `_find_similar_entities(...)`를 호출하던 332번째 줄 근처를 각각 `find_entity_by_name(...)`, `find_similar_entities(...)`로 바꾼다(앞의 언더스코어만 제거).

- [ ] **Step 3: 기존 회귀 테스트가 그대로 통과하는지 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/orchestrator/test_resolve_entity.py -v`
Expected: 수정 전과 동일하게 전부 PASS(리팩터링이라 동작 변화 없어야 함).

- [ ] **Step 4: 추출된 모듈을 독립적으로 검증하는 새 테스트 작성**

`backend/tests/orchestrator/test_entity_search.py`:

```python
"""entity_search.py의 이름 검색 로직을 독립적으로 검증한다."""

import psycopg

from orchestrator.entity_search import find_entity_by_name, find_similar_entities
from orchestrator.entity_types import NamedEntityType
from tests.mocks.postgres import MockAsyncPostgresPool

_PRODUCT_CONFIG = NamedEntityType(
    entity_type="product",
    table="production.product",
    id_column="productid",
    name_column="name",
    id_field="productId",
    name_field="productName",
)


async def test_find_entity_by_name_returns_exact_match() -> None:
    pool = MockAsyncPostgresPool(rows_by_name={"Bike": (1, "Bike")})

    result = await find_entity_by_name(_PRODUCT_CONFIG, "Bike", pool)

    assert result == {"productId": 1, "productName": "Bike"}


async def test_find_entity_by_name_returns_none_when_not_found() -> None:
    pool = MockAsyncPostgresPool(rows_by_name={})

    result = await find_entity_by_name(_PRODUCT_CONFIG, "Unknown", pool)

    assert result is None


async def test_find_similar_entities_returns_scored_candidates() -> None:
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similar_rows_by_name={"Bik": [(1, "Bike", 0.6), (2, "Biker", 0.4)]},
    )

    results = await find_similar_entities(_PRODUCT_CONFIG, "Bik", pool)

    assert results == [
        {
            "id": 1,
            "name": "Bike",
            "entityType": "product",
            "score": 0.6,
            "entity": {"productId": 1, "productName": "Bike"},
        },
        {
            "id": 2,
            "name": "Biker",
            "entityType": "product",
            "score": 0.4,
            "entity": {"productId": 2, "productName": "Biker"},
        },
    ]


async def test_find_similar_entities_returns_empty_when_pg_trgm_missing() -> None:
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similarity_error=psycopg.errors.UndefinedFunction("similarity"),
    )

    results = await find_similar_entities(_PRODUCT_CONFIG, "Bik", pool)

    assert results == []
    assert pool.rollback_called is True
```

- [ ] **Step 5: 새 테스트 실행**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/orchestrator/test_entity_search.py -v`
Expected: 4개 전부 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/entity_search.py backend/orchestrator/nodes/resolve_entity.py backend/tests/orchestrator/test_entity_search.py
git commit -m "Refactor: 이름 검색 로직을 entity_search.py로 분리"
```

---

### Task 2: Neo4j temporal 값 JSON 변환을 공유 유틸로 추출

**Files:**
- Create: `backend/core/json_safe.py`
- Modify: `backend/api/chat.py:19-37` (로컬 정의 제거, import로 교체)
- Test: `backend/tests/core/test_json_safe.py`

**Interfaces:**
- Produces: `to_json_safe(value: Any) -> Any` — Task 6(`api/graph.py`)이 노드 속성(`neo4j.time.*` 포함 가능)을 JSON 응답으로 내보낼 때 그대로 재사용한다.

- [ ] **Step 1: 실패하는 테스트부터 작성**

`backend/tests/core/test_json_safe.py`:

```python
"""to_json_safe가 Neo4j temporal 타입을 ISO 문자열로 바꾸는지 검증한다."""

import neo4j.time

from core.json_safe import to_json_safe


def test_to_json_safe_converts_neo4j_date() -> None:
    value = {"createdAt": neo4j.time.Date(2026, 8, 19)}

    result = to_json_safe(value)

    assert result == {"createdAt": "2026-08-19"}


def test_to_json_safe_passes_through_plain_values() -> None:
    value = {"name": "Bike", "count": 3, "active": True}

    assert to_json_safe(value) == value
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_json_safe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.json_safe'`

- [ ] **Step 3: `api/chat.py`의 기존 구현을 그대로 새 모듈로 옮긴다**

`backend/core/json_safe.py`:

```python
"""Decimal(SQL)과 neo4j.time.*(Cypher)처럼 plain json.dumps가 못 다루는 타입을
순수 JSON 타입으로 미리 바꾼다. HTTP 응답과 대화기록 저장(json.dumps) 양쪽에서
같은 실패를 겪던 걸 한 곳에서 해결한다."""

from typing import Any

import neo4j.time
from fastapi.encoders import jsonable_encoder

# fastapi.encoders.jsonable_encoder는 Decimal은 알아서 float로 바꾸지만
# neo4j.time.DateTime/Date/Time/Duration은 모르는 타입이라 __dict__를
# 그대로 덤프해버린다(예: {"_DateTime__date": {...}} 같은 내부 속성명이
# 그대로 샌다 - 실측으로 확인함). ISO 문자열로 명시적으로 바꿔준다.
_NEO4J_TEMPORAL_ENCODERS: dict[type, Any] = {
    neo4j.time.DateTime: lambda v: v.iso_format(),
    neo4j.time.Date: lambda v: v.iso_format(),
    neo4j.time.Time: lambda v: v.iso_format(),
    neo4j.time.Duration: str,
}


def to_json_safe(value: Any) -> Any:
    return jsonable_encoder(value, custom_encoder=_NEO4J_TEMPORAL_ENCODERS)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_json_safe.py -v`
Expected: PASS

- [ ] **Step 5: `api/chat.py`가 새 유틸을 쓰도록 고친다**

`backend/api/chat.py`에서 19-37번째 줄(`_NEO4J_TEMPORAL_ENCODERS` 정의부터 `_to_json_safe` 함수 정의까지)을 전부 지우고, 대신:

```python
from core.json_safe import to_json_safe
```

를 다른 `core.*` import들 옆에 추가한다. 파일 안에서 `_to_json_safe(...)`를 호출하던 두 곳(71번째 줄 근처 `response = _to_json_safe(...)`)을 `to_json_safe(...)`로 바꾼다.

- [ ] **Step 6: 기존 chat 테스트가 그대로 통과하는지 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_chat.py -v`
Expected: 수정 전과 동일하게 전부 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/core/json_safe.py backend/api/chat.py backend/tests/core/test_json_safe.py
git commit -m "Refactor: Neo4j temporal JSON 변환 유틸을 core/json_safe.py로 분리"
```

---

### Task 3: `tests/mocks/neo4j.py`에 `execute_query()` mock 추가

**Files:**
- Modify: `backend/tests/mocks/neo4j.py`

**Interfaces:**
- Produces: `MockNeo4jNode(properties: dict, labels: set[str])`, `MockAsyncExecuteQueryDriver(query_results: list[list[dict]])` — `.execute_query(query, **params)`가 호출될 때마다 `query_results`에서 순서대로 하나씩 꺼내 반환한다. `.executed_queries: list[tuple[str, dict]]`에 실행된 쿼리 텍스트와 파라미터를 기록한다. Task 6(`test_graph.py`)이 이 클래스로 `api/graph.py`의 Cypher 실행을 스텁한다.

- [ ] **Step 1: 기존 파일 끝에 새 mock 클래스를 추가한다**

`backend/tests/mocks/neo4j.py` 파일 끝(마지막 줄, `MockAsyncNeo4jDriver` 클래스 뒤)에 이어서 추가:

```python
class MockNeo4jNode:
    """neo4j.graph.Node 흉내 - dict(node)로 속성을 뽑고 .labels로 라벨을 읽는
    두 가지만 지원한다(api/graph.py가 실제로 쓰는 것만)."""

    def __init__(self, properties: dict[str, Any], labels: set[str]) -> None:
        self._properties = properties
        self.labels = frozenset(labels)

    def keys(self):
        return self._properties.keys()

    def __getitem__(self, key: str) -> Any:
        return self._properties[key]


class _MockQueryRecord:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class _MockEagerResult:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = [_MockQueryRecord(record) for record in records]


class MockAsyncExecuteQueryDriver:
    """driver.execute_query(query, **params)를 흉내낸다(session/tx 없이 한 번에
    실행하는 최신 neo4j 드라이버 API - api/graph.py가 쓰는 방식). query_results는
    호출될 때마다 하나씩 소비되는 큐라, 한 요청 안에서 여러 번 실행되는
    (중심 노드 조회 → 이웃 조회 → count 조회) 순서를 각각 다르게 스텁할 수 있다."""

    def __init__(self, query_results: list[list[dict[str, Any]]]) -> None:
        self._queue = list(query_results)
        self.executed_queries: list[tuple[str, dict[str, Any]]] = []

    async def execute_query(self, query: str, **params: Any) -> _MockEagerResult:
        self.executed_queries.append((query, params))
        records = self._queue.pop(0) if self._queue else []
        return _MockEagerResult(records)
```

- [ ] **Step 2: 아직 아무것도 이 mock을 안 쓰니, 기존 테스트가 안 깨지는지만 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/orchestrator/execution/test_cypher_executor.py -v`
Expected: 전부 PASS(기존 `MockAsyncNeo4jDriver`는 그대로 있고 새 클래스만 추가됐으니 영향 없음)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/mocks/neo4j.py
git commit -m "Test: execute_query() 기반 Neo4j 드라이버 mock 추가"
```

---

### Task 4: `GET /graph/search` 엔드포인트

**Files:**
- Create: `backend/api/graph.py`
- Test: `backend/tests/api/test_graph.py`

**Interfaces:**
- Consumes: `find_entity_by_name`, `find_similar_entities`, `SIMILARITY_THRESHOLD`, `MAX_CANDIDATES`(Task 1), `list_resolvable_entity_types`(기존 `orchestrator/entity_types.py`), `CurrentUser`/`get_current_user`(기존 `core/auth.py`), `get_pool`(기존 `core/postgres.py`)
- Produces: `router = APIRouter(prefix="/graph")`, `get_graph_schema() -> GraphSchema`(모듈 캐시) — Task 6이 같은 라우터에 `/graph/{entityType}/{id}/neighbors`를 추가하고 `get_graph_schema`를 재사용한다. Task 7이 `router`를 `main.py`에 등록한다.

- [ ] **Step 1: 실패하는 테스트부터 작성**

`backend/tests/api/test_graph.py` 새로 생성:

```python
"""GET /graph/search, GET /graph/{entityType}/{id}/neighbors 핸들러를 테스트한다."""

from typing import Any

import api.graph as graph_module
from api.graph import search_graph_entities
from core.auth import CurrentUser
from tests.mocks.postgres import MockAsyncPostgresPool


def _schema() -> Any:
    from agents.cypher.schema.models import GraphSchema

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
                    # 일부러 "name" 속성을 안 넣는다 - list_resolvable_entity_types는
                    # name 속성이 있는 노드만 검색 대상으로 삼으므로(entity_types.py:47),
                    # Supplier는 Task 4 테스트(/graph/search)에서 검색 후보로 안 뜬다.
                    # MockAsyncPostgresPool은 테이블을 구분 못 하고 이름만 보고
                    # 매치시키므로, Product/Supplier 둘 다 검색 대상이면 같은 이름이
                    # 두 타입 모두에서 매치된 것처럼 나와버린다 - 그래서 Task
                    # 4에서는 Product 하나만 검색 대상으로 두고, Supplier는(Task
                    # 5에서 SUPPLIES 관계의 이웃 라벨로만) 그래프 노드로서만 쓴다.
                    "properties": {
                        "supplierId": {
                            "type": "INTEGER",
                            "sourceColumn": "businessentityid",
                        },
                    },
                },
            },
            "relationships": {
                "SUPPLIES": {
                    "from": "Supplier",
                    "to": "Product",
                    "properties": {},
                },
            },
        }
    )


async def test_search_graph_entities_returns_exact_match(monkeypatch: Any) -> None:
    monkeypatch.setattr(graph_module, "get_graph_schema", _schema)
    pool = MockAsyncPostgresPool(rows_by_name={"Bike": (1, "Bike")})
    monkeypatch.setattr(graph_module, "get_pool", lambda: pool)

    result = await search_graph_entities(
        q="Bike", user=CurrentUser(username="kim.quality", role="user")
    )

    assert result == {
        "candidates": [
            {"entityType": "product", "id": 1, "name": "Bike", "score": 1.0}
        ]
    }


async def test_search_graph_entities_falls_back_to_similarity(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(graph_module, "get_graph_schema", _schema)
    pool = MockAsyncPostgresPool(
        rows_by_name={},
        similar_rows_by_name={"Bik": [(1, "Bike", 0.6)]},
    )
    monkeypatch.setattr(graph_module, "get_pool", lambda: pool)

    result = await search_graph_entities(
        q="Bik", user=CurrentUser(username="kim.quality", role="user")
    )

    assert result == {
        "candidates": [
            {"entityType": "product", "id": 1, "name": "Bike", "score": 0.6}
        ]
    }


async def test_search_graph_entities_returns_empty_when_nothing_matches(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(graph_module, "get_graph_schema", _schema)
    pool = MockAsyncPostgresPool(rows_by_name={})
    monkeypatch.setattr(graph_module, "get_pool", lambda: pool)

    result = await search_graph_entities(
        q="Nothing", user=CurrentUser(username="kim.quality", role="user")
    )

    assert result == {"candidates": []}
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.graph'`

- [ ] **Step 3: `api/graph.py`에 검색 엔드포인트를 구현한다**

`backend/api/graph.py`:

```python
"""이름 검색과 1-hop 이웃 조회 엔드포인트."""

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.models import GraphSchema
from core.auth import CurrentUser, get_current_user
from core.postgres import get_pool
from orchestrator.entity_search import MAX_CANDIDATES, find_entity_by_name, find_similar_entities
from orchestrator.entity_types import list_resolvable_entity_types

router = APIRouter(prefix="/graph")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_graph_schema_cache: GraphSchema | None = None


def get_graph_schema() -> GraphSchema:
    """graph_schema.yaml을 첫 호출 시 한 번만 읽어 프로세스 동안 캐싱한다
    (core/neo4j.py의 드라이버 싱글턴 패턴과 동일)."""
    global _graph_schema_cache
    if _graph_schema_cache is None:
        _graph_schema_cache = load_graph_schema(
            _PROJECT_ROOT / "schema" / "graph_schema.yaml"
        )
    return _graph_schema_cache


@router.get("/search")
async def search_graph_entities(
    q: str,
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
) -> dict[str, list[dict[str, Any]]]:
    schema = get_graph_schema()
    entity_types = list_resolvable_entity_types(schema)
    pool = get_pool()

    exact_matches = await asyncio.gather(
        *(find_entity_by_name(config, q, pool) for config in entity_types)
    )
    exact_candidates = [
        {
            "entityType": config.entity_type,
            "id": entity[config.id_field],
            "name": entity[config.name_field],
            "score": 1.0,
        }
        for config, entity in zip(entity_types, exact_matches, strict=True)
        if entity is not None
    ]
    if exact_candidates:
        return {"candidates": exact_candidates}

    similar_lists = await asyncio.gather(
        *(find_similar_entities(config, q, pool) for config in entity_types)
    )
    merged = [
        {
            "entityType": candidate["entityType"],
            "id": candidate["id"],
            "name": candidate["name"],
            "score": candidate["score"],
        }
        for candidates in similar_lists
        for candidate in candidates
    ]
    merged.sort(key=lambda c: c["score"], reverse=True)
    return {"candidates": merged[:MAX_CANDIDATES]}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_graph.py -v`
Expected: 3개 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/graph.py backend/tests/api/test_graph.py
git commit -m "Feat: GET /graph/search 이름 검색 엔드포인트 추가"
```

---

### Task 5: `GET /graph/{entityType}/{id}/neighbors` 엔드포인트

**Files:**
- Modify: `backend/api/graph.py` (Task 4에 이어서 추가)
- Modify: `backend/tests/api/test_graph.py` (Task 4에 이어서 추가)
- Modify: `.env.example`

**Interfaces:**
- Consumes: `MockAsyncExecuteQueryDriver`, `MockNeo4jNode`(Task 3), `get_driver`(기존 `core/neo4j.py`), `to_json_safe`(Task 2), `EntityNotFoundError`(기존 `orchestrator/errors.py`)
- Produces: `router`에 `GET /{entity_type}/{entity_id}/neighbors` 라우트 추가.

- [ ] **Step 1: 실패하는 테스트부터 작성**

`backend/tests/api/test_graph.py` 파일 끝에 이어서 추가(위쪽 import에 `from api.graph import get_graph_neighbors`, `from orchestrator.errors import EntityNotFoundError`, `from tests.mocks.neo4j import MockAsyncExecuteQueryDriver, MockNeo4jNode` 추가):

```python
async def test_get_graph_neighbors_returns_node_and_neighbors(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(graph_module, "get_graph_schema", _schema)
    center = MockNeo4jNode({"productId": 1, "name": "Bike"}, {"Product"})
    neighbor = MockNeo4jNode({"supplierId": 9, "name": "Acme"}, {"Supplier"})
    driver = MockAsyncExecuteQueryDriver(
        query_results=[
            [{"n": center}],  # 중심 노드 조회
            [{"m": neighbor}],  # SUPPLIES incoming 이웃 조회
            [{"total": 1}],  # SUPPLIES incoming count
        ]
    )
    monkeypatch.setattr(graph_module, "get_driver", lambda: driver)
    monkeypatch.setenv("GRAPH_NEIGHBOR_LIMIT", "50")

    result = await get_graph_neighbors(
        entity_type="product",
        entity_id=1,
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result["node"] == {
        "entityType": "product",
        "id": 1,
        "properties": {"productId": 1, "name": "Bike"},
    }
    assert result["neighbors"] == [
        {
            "relationshipType": "SUPPLIES",
            "direction": "incoming",
            "node": {
                "entityType": "supplier",
                "id": 9,
                "properties": {"supplierId": 9, "name": "Acme"},
            },
        }
    ]
    assert result["counts"] == {"SUPPLIES": {"returned": 1, "total": 1}}


async def test_get_graph_neighbors_raises_not_found_for_unknown_entity_type(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(graph_module, "get_graph_schema", _schema)

    try:
        await get_graph_neighbors(
            entity_type="unknownType",
            entity_id=1,
            user=CurrentUser(username="kim.quality", role="user"),
        )
        raise AssertionError("EntityNotFoundError를 기대했지만 발생하지 않음")
    except EntityNotFoundError:
        pass


async def test_get_graph_neighbors_raises_not_found_when_center_missing(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(graph_module, "get_graph_schema", _schema)
    driver = MockAsyncExecuteQueryDriver(query_results=[[]])  # 중심 노드 없음
    monkeypatch.setattr(graph_module, "get_driver", lambda: driver)

    try:
        await get_graph_neighbors(
            entity_type="product",
            entity_id=999,
            user=CurrentUser(username="kim.quality", role="user"),
        )
        raise AssertionError("EntityNotFoundError를 기대했지만 발생하지 않음")
    except EntityNotFoundError:
        pass
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_graph.py -v`
Expected: 새 테스트 3개 FAIL — `ImportError: cannot import name 'get_graph_neighbors'`

- [ ] **Step 3: `api/graph.py`에 이웃 조회 엔드포인트를 추가한다**

`backend/api/graph.py`의 기존 import 블록에 아래를 추가:

```python
from core.json_safe import to_json_safe
from core.neo4j import get_driver
from orchestrator.errors import EntityNotFoundError
```

파일 끝에 이어서 추가:

```python
def _entity_type_to_label(entity_type: str) -> str:
    """엔티티 타입 문자열(lowerCamelCase, 예: "workOrder")을 Neo4j 노드
    라벨(예: "WorkOrder")로 되돌린다. entity_types.py가 라벨 첫 글자만
    소문자로 바꿔 entity_type을 만드는 것의 역변환이다."""
    return entity_type[0].upper() + entity_type[1:]


@router.get("/{entity_type}/{entity_id}/neighbors")
async def get_graph_neighbors(
    entity_type: str,
    entity_id: int,
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    schema = get_graph_schema()
    entity_types = list_resolvable_entity_types(schema)
    config = next(
        (c for c in entity_types if c.entity_type == entity_type), None
    )
    if config is None:
        raise EntityNotFoundError()

    label = _entity_type_to_label(entity_type)
    driver = get_driver()
    limit = int(os.getenv("GRAPH_NEIGHBOR_LIMIT", "50"))

    center_result = await driver.execute_query(
        f"MATCH (n:{label} {{{config.id_field}: $id}}) RETURN n", id=entity_id
    )
    if not center_result.records:
        raise EntityNotFoundError()
    center_node = center_result.records[0]["n"]

    # 같은 라벨이 from/to 양쪽에 다 나오는 자기참조 관계(REQUIRES_COMPONENT)는
    # 두 방향을 각각 이웃 조회해야 하므로, (방향, 상대 라벨, 패턴) 목록을
    # 관계마다 최대 2개까지 만든다.
    directions: list[tuple[str, str, str, str]] = []
    for rel_name, rel in schema.relationships.items():
        if rel.from_node == label:
            directions.append(
                (rel_name, "outgoing", rel.to_node, f"(n)-[:{rel_name}]->(m)")
            )
        if rel.to_node == label:
            directions.append(
                (rel_name, "incoming", rel.from_node, f"(n)<-[:{rel_name}]-(m)")
            )
    self_referencing = {
        rel_name
        for rel_name, rel in schema.relationships.items()
        if rel.from_node == label and rel.to_node == label
    }

    neighbors: list[dict[str, Any]] = []
    counts: dict[str, dict[str, int]] = {}
    for rel_name, direction, neighbor_label, pattern in directions:
        neighbor_unique_key = schema.nodes[neighbor_label].unique_key
        neighbor_entity_type = neighbor_label[0].lower() + neighbor_label[1:]

        returned_result = await driver.execute_query(
            f"MATCH (n:{label} {{{config.id_field}: $id}}) MATCH {pattern} "
            f"RETURN m ORDER BY m.{neighbor_unique_key} LIMIT $limit",
            id=entity_id,
            limit=limit,
        )
        count_result = await driver.execute_query(
            f"MATCH (n:{label} {{{config.id_field}: $id}}) MATCH {pattern} "
            f"RETURN count(m) AS total",
            id=entity_id,
        )
        total = count_result.records[0]["total"]
        returned = len(returned_result.records)
        # 자기참조 관계는 방향별로 셀 수가 다르므로(부품 사용처 수 != 하위
        # 부품 수) 방향을 키에 포함해 구분한다. 그 외에는 관계당 방향이
        # 하나뿐이라 방향 없이 표시하는 편이 더 읽기 쉽다.
        count_key = f"{rel_name}:{direction}" if rel_name in self_referencing else rel_name
        counts[count_key] = {"returned": returned, "total": total}

        for record in returned_result.records:
            node = record["m"]
            properties = dict(node)
            neighbors.append(
                {
                    "relationshipType": rel_name,
                    "direction": direction,
                    "node": {
                        "entityType": neighbor_entity_type,
                        "id": properties[neighbor_unique_key],
                        "properties": properties,
                    },
                }
            )

    return to_json_safe(
        {
            "node": {
                "entityType": entity_type,
                "id": entity_id,
                "properties": dict(center_node),
            },
            "neighbors": neighbors,
            "counts": counts,
        }
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_graph.py -v`
Expected: 6개(Task 4의 3개 + 이번 3개) 전부 PASS

- [ ] **Step 5: `GRAPH_NEIGHBOR_LIMIT`을 `.env.example`에 문서화**

`.env.example`의 `# 실행 결과 상한` 섹션(`SQL_ROW_LIMIT=changeme` 다음 줄)에 추가:

```
GRAPH_NEIGHBOR_LIMIT=changeme
```

- [ ] **Step 6: Commit**

```bash
git add backend/api/graph.py backend/tests/api/test_graph.py .env.example
git commit -m "Feat: GET /graph/{entityType}/{id}/neighbors 이웃 조회 엔드포인트 추가"
```

---

### Task 6: `graph_router`를 `main.py`에 등록

**Files:**
- Modify: `backend/main.py:10-13,95-98`

**Interfaces:**
- Consumes: `router`(Task 4/5, `backend/api/graph.py`)

- [ ] **Step 1: import와 등록 줄을 추가한다**

`backend/main.py`의 기존 router import들 옆에:

```python
from api.graph import router as graph_router
```

`app.include_router(...)` 호출들 옆(98번째 줄 `history_router` 다음)에:

```python
app.include_router(graph_router, tags=["Graph"])
```

- [ ] **Step 2: 서버가 정상적으로 뜨는지 확인**

Run: `backend/venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'backend'); from main import app; print([r.path for r in app.routes if 'graph' in r.path])"`
Expected: `['/graph/search', '/graph/{entity_type}/{entity_id}/neighbors']`가 출력됨(import 에러 없이 라우트 등록 확인 - 실제 서버 기동 없이 앱 객체만 로드해서 확인).

- [ ] **Step 3: 기존 백엔드 테스트 스위트 전체가 여전히 통과하는지 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests -v`
Expected: 전부 PASS(이번 플랜에서 건드린 파일 외 회귀 없음)

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "Feat: /graph 라우터를 앱에 등록"
```

---

### Task 7: `PathGraph` 타입 일반화 (엔티티 라벨·속성·엣지별 라벨)

**Files:**
- Modify: `frontend/src/lib/pathGraph.ts`
- Modify: `frontend/src/lib/pathGraph.test.ts`
- Modify: `frontend/src/screens/Dashboard.tsx`

**Interfaces:**
- Produces: `PathGraphNode { id, label, entityLabel?: NodeLabel, properties?: Record<string, unknown> }`, `PathGraphEdge { source, target, label?: string }`(그래프 전체 `relationshipLabel` 필드 제거, 대신 각 엣지가 `label`을 가짐) — Task 9(`GraphExplorer.tsx`)가 이 shape으로 `PathGraph` 객체를 직접 만들고, Task 8(`PathGraphView.tsx`)이 `entityLabel`/`properties`/`edge.label`을 읽는다.

- [ ] **Step 1: 타입과 `extractPathGraph`를 고친다**

`frontend/src/lib/pathGraph.ts` 맨 위 import 추가:

```typescript
import type { NodeLabel } from '@/types/query'
```

`PathGraphNode`/`PathGraphEdge`/`PathGraph` 인터페이스를 교체:

```typescript
export interface PathGraphNode {
  id: string
  label: string
  // 실제 Neo4j 라벨을 알 때만 채워진다(예: 이웃 탐색기) - BOM 경로 결과는
  // 결과 행에 타입 정보가 없어 이 필드가 비어있고, PathGraphView가 대신
  // 역할(root/leaf/middle) 기반 색상으로 폴백한다.
  entityLabel?: NodeLabel
  // 실제 속성을 알 때만 채워진다(예: 이웃 탐색기). 있으면 상세 패널이
  // ID 하나 대신 이 전체를 보여준다.
  properties?: Record<string, unknown>
}

export interface PathGraphEdge {
  source: string
  target: string
  // 이 구간의 관계 타입(예: REQUIRES_COMPONENT). BOM 경로는 모든 엣지가
  // 같은 값을 갖고(관계 타입이 하나뿐이라), 이웃 탐색기는 엣지마다 다를 수 있다.
  label?: string
}

export interface PathGraph {
  nodes: PathGraphNode[]
  edges: PathGraphEdge[]
  // 그래프를 만들 때 원래 몇 개의 경로(행)가 있었는지, 그중 일부만 그렸는지
  totalPaths?: number
  truncated?: boolean
}
```

`extractPathGraph` 함수 안에서 엣지를 만드는 부분을 고친다(관계 타입을 미리 한 번 구해서 각 엣지에 넣는다):

```typescript
export function extractPathGraph(
  rows: Record<string, unknown>[],
  cypherQuery?: string,
): PathGraph | null {
  if (rows.length === 0) return null
  const columns = findPathColumns(rows[0])
  if (!columns) return null
  const { idKey, nameKey } = columns

  const truncated = rows.length > MAX_GRAPH_PATHS
  const usedRows = truncated ? rows.slice(0, MAX_GRAPH_PATHS) : rows
  const relationshipLabel = cypherQuery ? extractRelationshipLabel(cypherQuery) : undefined

  const nodeLabels = new Map<string, string>()
  const seenEdges = new Set<string>()
  const edges: PathGraphEdge[] = []

  for (const row of usedRows) {
    const idPath = row[idKey]
    const namePath = row[nameKey]
    if (
      !Array.isArray(idPath) ||
      !Array.isArray(namePath) ||
      idPath.length !== namePath.length ||
      idPath.length < 2
    ) {
      continue
    }
    for (let i = 0; i < idPath.length; i++) {
      const id = String(idPath[i])
      if (!nodeLabels.has(id)) nodeLabels.set(id, String(namePath[i]))
    }
    for (let i = 0; i < idPath.length - 1; i++) {
      const source = String(idPath[i])
      const target = String(idPath[i + 1])
      const edgeKey = `${source}->${target}`
      if (!seenEdges.has(edgeKey)) {
        seenEdges.add(edgeKey)
        edges.push({ source, target, label: relationshipLabel })
      }
    }
  }

  if (nodeLabels.size === 0) return null
  const nodes = Array.from(nodeLabels, ([id, label]) => ({ id, label }))
  return { nodes, edges, totalPaths: rows.length, truncated }
}
```

- [ ] **Step 2: 기존 테스트에서 `relationshipLabel` 검증을 `edges[i].label` 검증으로 옮긴다**

`frontend/src/lib/pathGraph.test.ts`에서 아래 세 테스트를 찾아 고친다:

```typescript
it('reads the relationship type out of the accompanying Cypher query', () => {
  const rows = [{ productIdPath: [1, 2], productNamePath: ['Bike', 'Frame'] }]
  const query =
    "MATCH (root:Product {name:'Bike'})-[:REQUIRES_COMPONENT*1..4]->(part:Product) RETURN root, part"
  const graph = extractPathGraph(rows, query)
  expect(graph?.edges[0].label).toBe('REQUIRES_COMPONENT')
})

it('also reads a relationship type bound to a variable', () => {
  const rows = [{ productIdPath: [1, 2], productNamePath: ['Bike', 'Frame'] }]
  const query = 'MATCH (a:Product)-[r:HAS_COMPONENT]->(b:Product) RETURN a, b'
  const graph = extractPathGraph(rows, query)
  expect(graph?.edges[0].label).toBe('HAS_COMPONENT')
})

it('leaves edge labels undefined when no query text is given', () => {
  const rows = [{ productIdPath: [1, 2], productNamePath: ['Bike', 'Frame'] }]
  const graph = extractPathGraph(rows)
  expect(graph?.edges[0].label).toBeUndefined()
})
```

(세 테스트 다 `graph?.relationshipLabel` → `graph?.edges[0].label`로 바뀐 것 외엔 동일)

- [ ] **Step 3: 테스트 실행해서 통과 확인**

Run: `cd frontend && npx vitest run src/lib/pathGraph.test.ts`
Expected: 12개 전부 PASS

- [ ] **Step 4: `tsc`/`eslint`로 나머지 참조도 다 고쳤는지 확인**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: `frontend/src/components/result/PathGraphView.tsx`에서 `graph.relationshipLabel` 참조가 타입 에러로 남아있을 것 — Task 8에서 고친다(이 Step에서는 에러가 나는 게 정상이며, Task 8까지 끝나야 깨끗해진다). 지금은 `pathGraph.ts`/`pathGraph.test.ts` 자체에는 에러가 없는지만 확인한다: `npx eslint src/lib/pathGraph.ts src/lib/pathGraph.test.ts` → 에러 없음.

- [ ] **Step 5: Commit**

커밋은 Task 8과 함께 한다(Task 8을 끝내야 `tsc`가 전체적으로 깨끗해지므로, 이 Task 단독으로는 커밋하지 않고 다음 Task로 이어간다).

---

### Task 8: `PathGraphView` 일반화 (실제 라벨 색상·중심 노드 강조·자유 배치 모드)

**Files:**
- Modify: `frontend/src/components/result/PathGraphView.tsx`
- Modify: `frontend/src/screens/Dashboard.tsx`

**Interfaces:**
- Consumes: `PathGraph`/`PathGraphNode`/`PathGraphEdge`(Task 7), `NODE_COLOR_CLASS`(기존 `lib/nodeColors.ts` - Tailwind 클래스라 canvas에는 못 쓰지만 THEME_COLORS에 대응하는 hex 값을 새로 정의), `NodeGlyphBadge`(기존 `components/common/NodeGlyphBadge.tsx`)
- Produces: `PathGraphViewProps { graph: PathGraph, dagMode?: 'lr', centerNodeId?: string }` — Task 9(`GraphExplorer.tsx`)가 `dagMode` 없이(자유 배치) `centerNodeId`를 넘겨서 쓴다.

- [ ] **Step 1: 실제 라벨별 색상 팔레트를 추가한다**

`frontend/src/components/result/PathGraphView.tsx`의 `THEME_COLORS` 정의 바로 아래에 추가(canvas는 Tailwind 클래스를 못 읽으므로 `nodeColors.ts`의 색과 같은 값을 hex로 다시 정의한다 - `index.css`의 `--node-*` 커스텀 프로퍼티와 동일한 값):

```typescript
const ENTITY_LABEL_COLOR: Record<NodeLabel, string> = {
  Product: '#0072b2',
  Supplier: '#009e73',
  WorkOrder: '#e69f00',
  RoutingOperation: '#cc79a7',
  Location: '#56b4e9',
  ScrapReason: '#d55e00',
}
```

import 줄에 `NodeLabel` 타입 추가:

```typescript
import { computeNodeRoles, MAX_GRAPH_PATHS, type NodeRole, type PathGraph } from '@/lib/pathGraph'
import type { NodeLabel } from '@/types/query'
import { NodeGlyphBadge } from '@/components/common/NodeGlyphBadge'
import { SCHEMA_NODES } from '@/lib/schemaNodes'
```

- [ ] **Step 2: props에 `dagMode`/`centerNodeId`를 추가하고 색상 결정 로직을 바꾼다**

`PathGraphViewProps`와 컴포넌트 시그니처를 고친다:

```typescript
interface PathGraphViewProps {
  graph: PathGraph
  // 있으면 이 방향으로 dagLevelDistance를 강제한다(BOM 경로). 없으면
  // 완전 자유 배치(물리 시뮬레이션 - 이웃 탐색기가 이 기본값을 쓴다).
  dagMode?: 'lr'
  // 있으면 이 노드를 항상 굵은 테두리로 강조한다(이웃 탐색기의 검색 중심 노드).
  centerNodeId?: string
}
```

```typescript
export function PathGraphView({ graph, dagMode, centerNodeId }: PathGraphViewProps) {
```

`GraphNodeData`에 `entityLabel`/`properties`를 추가:

```typescript
interface GraphNodeData {
  label: string
  role: NodeRole
  entityLabel?: NodeLabel
}
```

`graphData` 빌드 부분(nodes/links 매핑)을 고친다:

```typescript
  const graphData = useMemo(
    () => ({
      nodes: graph.nodes.map((n) => ({
        id: n.id,
        label: n.label,
        role: roles.get(n.id) ?? ('middle' as NodeRole),
        entityLabel: n.entityLabel,
      })),
      links: graph.edges.map((e) => ({ source: e.source, target: e.target, label: e.label })),
    }),
    [graph, roles],
  )
```

노드 색상 결정 로직을 실제 라벨 우선으로 바꾼다(`roleColor` 정의부 전체를 아래로 교체):

```typescript
  const roleColor: Record<NodeRole, string> = {
    root: colors.info,
    leaf: colors.success,
    middle: colors.borderStrong,
  }
  const nodeColor = (node: GraphNode): string =>
    node.entityLabel ? ENTITY_LABEL_COLOR[node.entityLabel] : roleColor[node.role]
```

`nodeCanvasObject` 안에서 `ctx.fillStyle = roleColor[n.role]`로 돼 있는 줄을 `ctx.fillStyle = nodeColor(n)`로 바꾼다. 그리고 같은 함수 안, 원을 그리는 반지름을 중심 노드일 때 더 크게(6 대신 9) 만든다:

```typescript
              const isCenter = centerNodeId != null && String(n.id) === centerNodeId
              ctx.beginPath()
              ctx.arc(n.x, n.y, isCenter ? 9 : 6, 0, 2 * Math.PI)
              ctx.fillStyle = nodeColor(n)
              ctx.fill()
              if (isSelected || isCenter) {
                ctx.lineWidth = (isCenter ? 3 : 2) / globalScale
                ctx.strokeStyle = colors.text
                ctx.stroke()
              }
```

(바로 위 `ctx.beginPath()`/`ctx.arc(...)`/`ctx.fillStyle = roleColor[n.role]`/`ctx.fill()`/`if (isSelected) { ... }` 블록 전체를 이걸로 교체한다.)

- [ ] **Step 3: 엣지 라벨을 `graph.relationshipLabel` 대신 `link.label`에서 읽는다**

`linkCanvasObject` 안 첫 줄:

```typescript
            linkCanvasObject={(link, ctx, globalScale) => {
              const l = link as unknown as {
                label?: string
                source: GraphNode & { x: number; y: number }
                target: GraphNode & { x: number; y: number }
              }
              if (!l.label) return
              if (typeof l.source !== 'object' || typeof l.target !== 'object') return
              const midX = (l.source.x + l.target.x) / 2
              const midY = (l.source.y + l.target.y) / 2

              const fontSize = 8 / globalScale
              ctx.font = `${fontSize}px sans-serif`
              const textWidth = ctx.measureText(l.label).width
              const padding = 2 / globalScale
              ctx.fillStyle = colors.panel
              ctx.fillRect(
                midX - textWidth / 2 - padding,
                midY - fontSize / 2 - padding,
                textWidth + padding * 2,
                fontSize + padding * 2,
              )
              ctx.textAlign = 'center'
              ctx.textBaseline = 'middle'
              ctx.fillStyle = colors.textFaint
              ctx.fillText(l.label, midX, midY)
            }}
```

(기존 `if (!graph.relationshipLabel) return`으로 시작하던 블록 전체를 이걸로 교체 - `graph.relationshipLabel` 대신 `l.label`을 쓰고, `graph.relationshipLabel`을 참조하던 나머지 줄들도 전부 `l.label`로 바뀐다.)

- [ ] **Step 4: `dagMode`/`dagLevelDistance`를 prop 기반으로 바꾼다**

`ForceGraph2D`에 넘기던 `dagMode={DAG_MODE}` / `dagLevelDistance={120}`을:

```typescript
            dagMode={dagMode}
            dagLevelDistance={dagMode ? 120 : undefined}
```

로 바꾸고, 파일 위쪽의 `const DAG_MODE = 'lr'` 상수 정의는 삭제한다(더 이상 안 씀).

반발력 튜닝(두 번째 `useEffect`)도 `dagMode` 유무에 따라 분기한다:

```typescript
  useEffect(() => {
    const fg = fgRef.current
    if (!fg) return
    if (dagMode) {
      fg.d3Force('charge')?.strength(-400)
      fg.d3Force('link')?.distance(50)
    } else {
      fg.d3Force('charge')?.strength(-140)
      fg.d3Force('link')?.distance(60)
    }
    fg.d3ReheatSimulation()
  }, [graphData, dagMode])
```

(자유 배치 쪽 `-140`/`60`은 첫 시도 값이다 - Task 11의 수동 확인에서 이웃 노드 수가 많을 때 겹치면 조정한다.)

- [ ] **Step 5: 범례를 실제 라벨 유무에 따라 바꾸고, 상세 패널이 속성을 보여주게 한다**

범례 부분(`<div className="mb-1.5 flex flex-wrap ...">`) 전체를 교체:

```typescript
      <div className="mb-1.5 flex flex-wrap items-center gap-3 px-1 text-[10.5px] text-text-faint">
        {(() => {
          const entityLabels = Array.from(
            new Set(graph.nodes.map((n) => n.entityLabel).filter((v): v is NodeLabel => v != null)),
          )
          if (entityLabels.length > 0) {
            return entityLabels.map((entityLabel) => {
              const schemaNode = SCHEMA_NODES.find((n) => n.label === entityLabel)
              return (
                <span key={entityLabel} className="flex items-center gap-1">
                  <NodeGlyphBadge nodeLabel={entityLabel} glyph={schemaNode?.glyph ?? '?'} size={11} />
                  {schemaNode?.description ?? entityLabel}
                </span>
              )
            })
          }
          return (
            <>
              <span className="flex items-center gap-1">
                <span className="size-2 rounded-full bg-info" />
                시작(최상위)
              </span>
              <span className="flex items-center gap-1">
                <span className="size-2 rounded-full bg-success" />
                최하위 부품
              </span>
            </>
          )
        })()}
        {graph.truncated ? (
          <span>
            전체 {graph.totalPaths}건 중 상위 {MAX_GRAPH_PATHS}개 경로만 그래프로 표시했습니다. 전체
            결과는 아래 표를 확인하세요.
          </span>
        ) : null}
      </div>
```

상세 패널(`{selectedNode ? (...) : null}`) 부분을 교체:

```typescript
        {selectedNode ? (
          <div className="w-[160px] shrink-0 rounded-md border border-border bg-panel-2 p-2 text-[11px]">
            <p className="mb-1 font-semibold text-text">{selectedNode.label}</p>
            {selectedNode.properties ? (
              Object.entries(selectedNode.properties).map(([key, value]) => (
                <div key={key} className="mb-0.5">
                  <p className="text-text-faint">{key}</p>
                  <p className="truncate font-mono text-text">{String(value)}</p>
                </div>
              ))
            ) : (
              <>
                <p className="text-text-faint">ID</p>
                <p className="font-mono text-text">{selectedNode.id}</p>
              </>
            )}
          </div>
        ) : null}
```

- [ ] **Step 6: `Dashboard.tsx`가 BOM 경로에 명시적으로 `dagMode="lr"`을 넘기게 한다**

`frontend/src/screens/Dashboard.tsx`에서 `<PathGraphView key={result.query} graph={result.pathGraph} />` 부분을:

```tsx
              {result.pathGraph ? (
                <PathGraphView key={result.query} graph={result.pathGraph} dagMode="lr" />
              ) : null}
```

로 바꾼다(`dagMode="lr"` 추가 - 이게 없으면 BOM 경로도 자유 배치가 돼서 지금까지 만든 dagMode 튜닝이 무의미해진다).

- [ ] **Step 7: 타입체크·린트 확인**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음

Run: `cd frontend && npm run lint`
Expected: 에러 없음

- [ ] **Step 8: 기존 pathGraph 유닛 테스트가 여전히 통과하는지 확인**

Run: `cd frontend && npx vitest run src/lib/pathGraph.test.ts`
Expected: 12개 전부 PASS

- [ ] **Step 9: 브라우저로 BOM 경로 화면이 예전과 똑같이 보이는지 수동 확인**

Browser 도구로 `preview_start`(`frontend-dev`) 후, 이전 세션에서 썼던 것과 같은 방식(임시 `/dev-preview` 라우트에 `extractPathGraph`로 만든 mock `PathGraph`를 `<PathGraphView graph={graph} dagMode="lr" />`로 렌더링)으로 확인한다. 확인 후 임시 라우트/파일은 반드시 제거한다.

Expected: dagMode="lr" 트리 모양, 노드 색(역할 기반: 시작=파랑, 최하위=초록), 관계 라벨, 확대/축소/맞춤/초기화 버튼, 클릭 시 라벨+ID 상세 패널 — Task 8 이전과 시각적으로 동일해야 한다(상세 패널에 `properties`가 없으므로 ID만 나오는 옛날 방식 그대로 나오는 게 맞다).

- [ ] **Step 10: Commit (Task 7 포함)**

```bash
git add frontend/src/lib/pathGraph.ts frontend/src/lib/pathGraph.test.ts frontend/src/components/result/PathGraphView.tsx frontend/src/screens/Dashboard.tsx
git commit -m "Feat: PathGraphView가 실제 엔티티 라벨 색상·중심 노드 강조·자유 배치를 지원하도록 일반화"
```

---

### Task 9: 프론트 API 클라이언트 (`lib/schemas.ts`, `lib/graph.ts`)

**Files:**
- Modify: `frontend/src/lib/schemas.ts`
- Create: `frontend/src/lib/graph.ts`
- Create: `frontend/src/lib/graph.test.ts`

**Interfaces:**
- Consumes: `api`(기존 `lib/api.ts`), `PathGraph`/`PathGraphNode`/`PathGraphEdge`(Task 7)
- Produces: `searchGraphEntities(q: string): Promise<GraphCandidate[]>`, `fetchGraphNeighbors(entityType: string, id: number): Promise<GraphNeighborsResponse>`, `buildNeighborGraph(response: GraphNeighborsResponse): PathGraph` — Task 10(`GraphExplorer.tsx`)이 이 세 함수를 쓴다.

- [ ] **Step 1: zod 스키마를 추가한다**

`frontend/src/lib/schemas.ts` 파일 끝에 추가:

```typescript
export const GraphCandidateSchema = z.object({
  entityType: z.string(),
  id: z.number(),
  name: z.string(),
  score: z.number(),
})
export type GraphCandidate = z.infer<typeof GraphCandidateSchema>

export const GraphSearchResultSchema = z.object({
  candidates: z.array(GraphCandidateSchema),
})

const GraphNodeSchema = z.object({
  entityType: z.string(),
  id: z.number(),
  properties: z.record(z.string(), z.unknown()),
})

export const GraphNeighborsResponseSchema = z.object({
  node: GraphNodeSchema,
  neighbors: z.array(
    z.object({
      relationshipType: z.string(),
      direction: z.enum(['incoming', 'outgoing']),
      node: GraphNodeSchema,
    }),
  ),
  counts: z.record(z.string(), z.object({ returned: z.number(), total: z.number() })),
})
export type GraphNeighborsResponse = z.infer<typeof GraphNeighborsResponseSchema>
```

- [ ] **Step 2: 실패하는 테스트부터 작성 (`buildNeighborGraph`)**

`frontend/src/lib/graph.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { buildNeighborGraph } from './graph'
import type { GraphNeighborsResponse } from './schemas'

describe('buildNeighborGraph', () => {
  it('builds a center node connected to its neighbors with direction-correct edges', () => {
    const response: GraphNeighborsResponse = {
      node: { entityType: 'product', id: 1, properties: { name: 'Bike' } },
      neighbors: [
        {
          relationshipType: 'SUPPLIES',
          direction: 'incoming',
          node: { entityType: 'supplier', id: 9, properties: { name: 'Acme' } },
        },
        {
          relationshipType: 'REQUIRES_COMPONENT',
          direction: 'outgoing',
          node: { entityType: 'product', id: 2, properties: { name: 'Frame' } },
        },
      ],
      counts: {},
    }

    const graph = buildNeighborGraph(response)

    expect(graph.nodes).toEqual([
      {
        id: 'product:1',
        label: 'Bike',
        entityLabel: 'Product',
        properties: { name: 'Bike' },
      },
      {
        id: 'supplier:9',
        label: 'Acme',
        entityLabel: 'Supplier',
        properties: { name: 'Acme' },
      },
      {
        id: 'product:2',
        label: 'Frame',
        entityLabel: 'Product',
        properties: { name: 'Frame' },
      },
    ])
    expect(graph.edges).toEqual([
      { source: 'supplier:9', target: 'product:1', label: 'SUPPLIES' },
      { source: 'product:1', target: 'product:2', label: 'REQUIRES_COMPONENT' },
    ])
  })

  it('falls back to "type #id" as the label when properties has no name', () => {
    const response: GraphNeighborsResponse = {
      node: { entityType: 'workOrder', id: 5, properties: {} },
      neighbors: [],
      counts: {},
    }

    const graph = buildNeighborGraph(response)

    expect(graph.nodes[0].label).toBe('workOrder #5')
  })
})
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `cd frontend && npx vitest run src/lib/graph.test.ts`
Expected: FAIL — `graph.ts` 모듈이 없음

- [ ] **Step 4: `lib/graph.ts` 구현**

```typescript
import { api } from './api'
import {
  GraphNeighborsResponseSchema,
  GraphSearchResultSchema,
  type GraphCandidate,
  type GraphNeighborsResponse,
} from './schemas'
import type { NodeLabel } from '@/types/query'
import type { PathGraph } from './pathGraph'

// entityType(lowerCamelCase, 예: "workOrder")을 Neo4j 라벨(예: "WorkOrder")로
// 되돌린다. 백엔드 api/graph.py의 _entity_type_to_label과 같은 변환이다.
function entityTypeToLabel(entityType: string): NodeLabel {
  return (entityType.charAt(0).toUpperCase() + entityType.slice(1)) as NodeLabel
}

function nodeDisplayLabel(entityType: string, id: number, properties: Record<string, unknown>): string {
  return typeof properties.name === 'string' ? properties.name : `${entityType} #${id}`
}

// 이름으로 그래프 엔티티를 검색한다(정확 일치 우선, 없으면 유사도 후보).
export async function searchGraphEntities(q: string): Promise<GraphCandidate[]> {
  const res = await api.get('/graph/search', { params: { q } })
  return GraphSearchResultSchema.parse(res.data).candidates
}

// 엔티티 하나의 실제 1-hop 이웃(관계 타입별 상한 적용)을 조회한다.
export async function fetchGraphNeighbors(
  entityType: string,
  id: number,
): Promise<GraphNeighborsResponse> {
  const res = await api.get(`/graph/${entityType}/${id}/neighbors`)
  return GraphNeighborsResponseSchema.parse(res.data)
}

// /graph/{entityType}/{id}/neighbors 응답을 PathGraphView가 그릴 수 있는
// PathGraph로 바꾼다. id는 entityType별로만 유일하므로("product" id=1과
// "supplier" id=1이 동시에 있을 수 있다) 노드 id를 "entityType:id"로 합성해
// 충돌을 막는다.
export function buildNeighborGraph(response: GraphNeighborsResponse): PathGraph {
  const centerId = `${response.node.entityType}:${response.node.id}`
  const nodes: PathGraph['nodes'] = [
    {
      id: centerId,
      label: nodeDisplayLabel(response.node.entityType, response.node.id, response.node.properties),
      entityLabel: entityTypeToLabel(response.node.entityType),
      properties: response.node.properties,
    },
  ]
  const edges: PathGraph['edges'] = []

  for (const neighbor of response.neighbors) {
    const neighborId = `${neighbor.node.entityType}:${neighbor.node.id}`
    nodes.push({
      id: neighborId,
      label: nodeDisplayLabel(neighbor.node.entityType, neighbor.node.id, neighbor.node.properties),
      entityLabel: entityTypeToLabel(neighbor.node.entityType),
      properties: neighbor.node.properties,
    })
    edges.push(
      neighbor.direction === 'outgoing'
        ? { source: centerId, target: neighborId, label: neighbor.relationshipType }
        : { source: neighborId, target: centerId, label: neighbor.relationshipType },
    )
  }

  return { nodes, edges }
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd frontend && npx vitest run src/lib/graph.test.ts`
Expected: 2개 전부 PASS

- [ ] **Step 6: 타입체크**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/schemas.ts frontend/src/lib/graph.ts frontend/src/lib/graph.test.ts
git commit -m "Feat: 그래프 검색/이웃 조회 API 클라이언트와 PathGraph 변환 추가"
```

---

### Task 10: `GraphExplorer` 화면

**Files:**
- Create: `frontend/src/screens/GraphExplorer.tsx`

**Interfaces:**
- Consumes: `searchGraphEntities`, `fetchGraphNeighbors`, `buildNeighborGraph`(Task 9), `PathGraphView`(Task 8), `GraphCandidate`(Task 9)
- Produces: `GraphExplorer` 컴포넌트 — Task 11이 `/explore` 라우트에 연결한다.

- [ ] **Step 1: 화면 컴포넌트를 작성한다**

`frontend/src/screens/GraphExplorer.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { PathGraphView } from '@/components/result/PathGraphView'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/store/useAuthStore'
import { useHealthStore } from '@/store/useHealthStore'
import { buildNeighborGraph, fetchGraphNeighbors, searchGraphEntities } from '@/lib/graph'
import type { GraphCandidate } from '@/lib/schemas'
import type { PathGraph } from '@/lib/pathGraph'

const SEARCH_DEBOUNCE_MS = 300
const READ_ONLY = true

// 이름으로 엔티티를 검색해 그 실제 Neo4j 1-hop 이웃을 그래프로 보여주는 화면.
// /chat의 질의응답 결과와 달리, 특정 질문 없이 그래프 데이터 자체를 자유롭게
// 둘러보는 용도다(멀티홉 확장은 이번 범위 밖 - docs/superpowers/specs/
// 2026-08-27-graph-neighbor-explorer-design.md 참고).
export function GraphExplorer() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const neo4jConnected = useHealthStore((s) => s.neo4jConnected)

  const [queryText, setQueryText] = useState('')
  const [candidates, setCandidates] = useState<GraphCandidate[]>([])
  const [searchError, setSearchError] = useState('')
  const [graph, setGraph] = useState<PathGraph | null>(null)
  const [centerNodeId, setCenterNodeId] = useState<string | null>(null)
  const [counts, setCounts] = useState<Record<string, { returned: number; total: number }>>({})
  const [neighborsError, setNeighborsError] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 검색어가 바뀔 때마다 디바운스 후 후보를 새로 불러온다
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const trimmed = queryText.trim()
    if (!trimmed) {
      setCandidates([])
      setSearchError('')
      return
    }
    debounceRef.current = setTimeout(() => {
      searchGraphEntities(trimmed)
        .then((results) => {
          setCandidates(results)
          setSearchError(results.length === 0 ? '일치하는 결과가 없습니다.' : '')
        })
        .catch(() => setSearchError('검색 중 오류가 발생했습니다.'))
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [queryText])

  const handleSelectCandidate = async (candidate: GraphCandidate) => {
    setCandidates([])
    setQueryText(candidate.name)
    setNeighborsError('')
    try {
      const response = await fetchGraphNeighbors(candidate.entityType, candidate.id)
      setGraph(buildNeighborGraph(response))
      setCenterNodeId(`${candidate.entityType}:${candidate.id}`)
      setCounts(response.counts)
    } catch {
      setGraph(null)
      setCenterNodeId(null)
      setCounts({})
      setNeighborsError('이웃을 불러오지 못했습니다.')
    }
  }

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar
        connected={neo4jConnected}
        readOnly={READ_ONLY}
        onNavigateHome={() => {}}
        username={user?.username}
        onLogout={logout}
      />
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
        <div className="relative w-full max-w-md">
          <Input
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            placeholder="제품·공급업체·작업장 등 이름으로 검색"
          />
          {candidates.length > 0 ? (
            <ul className="absolute z-10 mt-1 w-full rounded-md border border-border bg-panel shadow-md">
              {candidates.map((candidate) => (
                <li key={`${candidate.entityType}:${candidate.id}`}>
                  <button
                    type="button"
                    onClick={() => handleSelectCandidate(candidate)}
                    className="flex w-full items-center justify-between px-3 py-2 text-left text-[13px] text-text hover:bg-panel-2"
                  >
                    <span>{candidate.name}</span>
                    <span className="text-[11px] text-text-faint">{candidate.entityType}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {searchError ? <p className="mt-1 text-[12px] text-text-faint">{searchError}</p> : null}
        </div>

        {neighborsError ? <p className="text-[13px] text-fail">{neighborsError}</p> : null}

        {graph ? (
          <>
            <PathGraphView key={centerNodeId} graph={graph} centerNodeId={centerNodeId ?? undefined} />
            <div className="flex flex-wrap gap-3 text-[11px] text-text-faint">
              {Object.entries(counts).map(([relationshipType, count]) => (
                <span key={relationshipType}>
                  {relationshipType}: 총 {count.total}건 중 상위 {count.returned}건 표시
                </span>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 타입체크·린트**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음(Task 11에서 라우트에 연결하기 전까지는 "사용되지 않는 export" 경고는 나지 않음 - named export라 타입체크 대상)

Run: `cd frontend && npm run lint`
Expected: 에러 없음

- [ ] **Step 3: Commit**

```bash
git add frontend/src/screens/GraphExplorer.tsx
git commit -m "Feat: 그래프 이웃 탐색기 화면(GraphExplorer) 추가"
```

---

### Task 11: 라우팅 + TopBar 진입 버튼 + 수동 검증

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/TopBar.tsx`
- Modify: `frontend/src/screens/Dashboard.tsx` (TopBar에 새 prop 전달)
- Modify: `frontend/src/screens/GraphExplorer.tsx` (TopBar에 새 prop 전달)

**Interfaces:**
- Consumes: `GraphExplorer`(Task 10), `ProtectedRoute`(기존)

- [ ] **Step 1: `TopBar`에 탐색 화면 진입 버튼을 추가한다**

`frontend/src/components/layout/TopBar.tsx`의 `TopBarProps`에 추가:

```typescript
interface TopBarProps {
  connected: boolean
  readOnly: boolean
  onNavigateHome: () => void
  onNavigateExplore?: () => void
  username?: string
  onLogout?: () => void
}
```

함수 시그니처에 `onNavigateExplore`를 받도록 추가하고, 로그아웃 버튼 앞(`{onLogout ? ... }` 바로 앞)에 버튼을 추가:

```tsx
        {onNavigateExplore ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="rounded-full border-border-strong bg-transparent hover:bg-panel-2"
            onClick={onNavigateExplore}
          >
            그래프 탐색
          </Button>
        ) : null}
```

- [ ] **Step 2: `App.tsx`에 `/explore` 라우트를 추가한다**

`frontend/src/App.tsx`의 `import` 블록에:

```typescript
import { GraphExplorer } from '@/screens/GraphExplorer'
```

`<Routes>` 안, `/login` 라우트 다음에:

```tsx
        <Route
          path="/explore"
          element={
            <ProtectedRoute>
              <GraphExplorer />
            </ProtectedRoute>
          }
        />
```

- [ ] **Step 3: `Dashboard.tsx`/`GraphExplorer.tsx`가 `onNavigateExplore`를 넘기게 한다**

`frontend/src/screens/Dashboard.tsx`의 `<TopBar ... />` 호출에 `onNavigateExplore={() => navigate('/explore')}` 추가 — 이 파일엔 아직 `react-router-dom`의 `useNavigate`가 없으므로 import와 훅 호출을 추가한다:

```typescript
import { useNavigate } from 'react-router-dom'
```

컴포넌트 안, 다른 store 훅들 옆에:

```typescript
  const navigate = useNavigate()
```

`<TopBar ... />`에:

```tsx
        onNavigateExplore={() => navigate('/explore')}
```

`frontend/src/screens/GraphExplorer.tsx`도 동일하게 `useNavigate`를 추가하고, `<TopBar ... />`에 `onNavigateHome={() => navigate('/')}` (지금 빈 함수 `() => {}`로 둔 걸 교체), `onNavigateExplore` prop은 생략(이미 탐색 화면 안에 있으니 다시 누를 버튼은 필요 없음 - `TopBar`가 optional prop이라 안 넘기면 자동으로 숨겨진다).

- [ ] **Step 4: 타입체크·린트**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음

Run: `cd frontend && npm run lint`
Expected: 에러 없음

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/layout/TopBar.tsx frontend/src/screens/Dashboard.tsx frontend/src/screens/GraphExplorer.tsx
git commit -m "Feat: /explore 라우트와 TopBar 진입 버튼 연결"
```

- [ ] **Step 6: 브라우저로 전체 흐름을 수동 검증한다**

`preview_start`(`frontend-dev`, 필요시 `backend-dev`도)로 로그인 후:
1. TopBar의 "그래프 탐색" 버튼 클릭 → `/explore`로 이동하는지
2. 실제 스키마에 있는 이름(예: 제품명)을 검색창에 입력 → 디바운스 후 후보 드롭다운이 뜨는지
3. 후보 클릭 → 그래프가 그려지고 중심 노드가 굵은 테두리로 강조되는지, 노드 색이 실제 라벨(`ENTITY_LABEL_COLOR`) 기준인지, 범례가 `NodeGlyphBadge` 칩으로 나오는지
4. 노드 클릭 → 우측 패널에 `properties` 전체가 나오는지
5. 관계 타입별 "총 N건 중 상위 M건" 텍스트가 나오는지
6. 확대/축소/맞춤/초기화 버튼이 동작하는지
7. 존재하지 않는 이름 검색 → "일치하는 결과가 없습니다." 안내가 뜨는지

Expected: 위 7가지가 전부 콘솔 에러 없이 동작. 문제가 있으면 해당 항목을 고치고 다시 확인한다(이 Task는 자동화 테스트가 없으므로 이 수동 확인이 유일한 검증 단계다).

---

## Self-Review 체크리스트 (계획 작성자용, 실행 시 참고만)

- **스펙 커버리지**: 1-1(엔티티 검색 공유화)=Task 1, 1-2(`/graph/search`)=Task 4, 1-3(`/graph/{entityType}/{id}/neighbors`)=Task 5, 2(프론트 화면·컴포넌트·API 클라이언트)=Task 7-11, 3(테스트)=각 Task에 포함. 스펙의 "확실하지 않은 부분"(멀티홉, 스크롤 리스트, 성능 실측)은 의도적으로 이번 플랜 범위 밖.
- **알아둘 것**: 스펙 3절은 "react-force-graph-2d 신규 의존성 추가"라고 돼 있지만 이미 설치돼 있어 설치 단계를 뺐다. "프론트 테스트 프레임워크가 없다"도 이젠 사실이 아니라(vitest 있음) 순수 로직(Task 7, 9)은 유닛 테스트를 추가했다.
