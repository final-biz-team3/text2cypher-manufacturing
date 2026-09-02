# Neo4j 그래프 이웃 탐색기 설계

## 상태

브레인스토밍 확정 (2026-08-27), git 커밋은 사용자 최종 승인 후 진행

## 배경

`/chat`은 사용자가 질문을 던졌을 때 그 질문에 대한 답변(SQL/Cypher 실행 결과)만 보여준다. 프론트에 Neo4j에 실제로 어떤 데이터가 얼마나 연결돼 있는지 직접 둘러볼 수 있는 화면이 없다 — `PathGraphCanvas`는 "react-force-graph-2d 연동 예정"이라는 플레이스홀더 텍스트만 있고 어디에도 렌더링되지 않는 죽은 컴포넌트다.

"Neo4j가 연결됐다"는 걸 눈으로 확인하고 싶다는 요구에서 출발했다. 브레인스토밍 과정에서 범위를 좁혀왔다:

1. 처음 제안한 "스키마 레벨 그래프"(노드 6종/관계 6종 구조도)는 실제 데이터가 아니라서 기각.
2. "전체 데이터를 다 가져와서 보여주기"는 `WorkOrder`(~72,000건)/`RoutingOperation`(~67,000건) 같은 노드가 force-graph로 감당 안 되는 규모라 기각.
3. 최종적으로 **이름으로 검색 → 그 노드의 실제 1-hop 이웃만 Neo4j에서 조회해서 보여주는 방식**으로 확정. 이웃이 너무 많은 관계는 무제한 스크롤이 아니라 **관계 타입별 상한 + "총 N건 중 상위 M건" 표시**로만 처리한다(스크롤 리스트 병행은 이번 범위에서 뺌 — 최소로 만들어보고 판단하기로 함).

## 목표

- 로그인한 사용자가 이름으로 엔티티를 검색하고, 그 엔티티의 실제 Neo4j 이웃(1-hop)을 인터랙티브한 force-directed 그래프로 볼 수 있다.
- 이웃이 많은 관계는 상한을 걸고 실제 총 개수를 함께 보여준다.
- 기존 `resolve_entity`의 이름 검색 로직(정확 일치 + `pg_trgm` 유사도)을 재사용한다 — 중복 구현하지 않는다.

## 비목표 (이번 스코프 제외)

- 이웃 노드를 클릭해서 계속 확장(멀티홉 탐색) — 나중에 필요하면 별도 작업
- 상한을 넘는 이웃을 스크롤 리스트로 전부 보여주는 기능 — 이번엔 "총 N건 중 상위 M건" 표시만
- 그래프 데이터 수정/쓰기 — 읽기 전용
- Postgres(원본 데이터) 전체를 보여주는 기능 — Neo4j 그래프 한정

## 1. 백엔드

### 1-1. 이름 검색 로직 공유화 (재사용)

`orchestrator/nodes/resolve_entity.py`의 `_find_entity_by_name`/`_find_similar_entities`/`_entity_type_config` 로직을 새 모듈(`orchestrator/entity_search.py`)로 뽑아내고, `resolve_entity.py`와 새 `/graph/search` 엔드포인트가 이 모듈을 함께 쓴다. `resolve_entity.py`의 기존 동작(정확 일치 우선, 실패 시 `pg_trgm` 유사도, 임계값 0.3, 최대 5개)은 그대로 유지하고 테스트도 그대로 통과해야 한다 — 순수 리팩터링이다.

### 1-2. `GET /graph/search?q=<text>`

- `list_resolvable_entity_types(graph_schema)`로 이름 검색 가능한 전체 타입(Product/Supplier/Location/ScrapReason/productCategory)을 대상으로 정확 일치 → 유사도 검색.
- `WorkOrder`/`RoutingOperation`은 `name` 속성이 없어 애초에 이 목록에 없다 — 검색 시작점이 될 수 없고, 다른 노드의 이웃으로만 등장할 수 있다.
- 응답: `{"candidates": [{"entityType": str, "id": int, "name": str, "score": float}]}` (정확 일치면 `score`는 1.0 고정).
- 로그인 필요(`Depends(get_current_user)`), 그 외 권한 제한 없음.

### 1-3. `GET /graph/{entityType}/{id}/neighbors`

- `entityType`이 `list_resolvable_entity_types` 목록에 없으면 404.
- 해당 노드를 중심으로, `graph_schema.yaml`의 `relationships`에서 `from`/`to`가 이 노드 라벨과 일치하는 관계 전부를 순회하며 Cypher로 1-hop 이웃을 조회한다 — 관계별 방향(from/to)은 yaml에서 그대로 읽으므로 새 관계가 추가돼도 코드 변경이 필요 없다(기존 프로젝트가 `resolve_entity` 일반화 때 채택한 것과 같은 "스키마 기반 동적 조립" 원칙).
- 관계 타입별로 이웃 노드의 `uniqueKey` 오름차순으로 정렬해 `LIMIT {GRAPH_NEIGHBOR_LIMIT}`로 상한을 걸고(같은 검색에 같은 결과가 나오도록 정렬 기준을 고정), 별도로 `count()`로 실제 총 개수를 구한다. `GRAPH_NEIGHBOR_LIMIT`는 환경변수(기본값 50) — `answer_limits.py`의 `ANSWER_MAX_ROWS` 패턴과 동일하게 조정 가능하게 둔다.
- 응답:
  ```json
  {
    "node": {"entityType": "product", "id": 316, "properties": {...}},
    "neighbors": [
      {"relationshipType": "SUPPLIES", "direction": "incoming", "node": {"entityType": "supplier", "id": 52, "properties": {...}}}
    ],
    "counts": {
      "SUPPLIES": {"returned": 3, "total": 3},
      "REQUIRES_COMPONENT": {"returned": 50, "total": 214}
    }
  }
  ```
- Neo4j 접속은 `core/neo4j.get_driver()`(기존 `AsyncDriver`)를 그대로 쓰고, `driver.execute_query(...)`로 세션을 직접 관리하지 않는 단발성 읽기 쿼리를 실행한다. **이 엔드포인트가 이 프로젝트에서 실제로 Cypher를 실행하는 첫 코드**다 — self-correction의 `execute_cypher`는 여전히 스텁이라 이것과 무관하며, 서로 다른 관심사(고정된 조회 쿼리 vs LLM이 생성한 임의 쿼리의 검증·재시도)라 코드를 공유하지 않는다.
- 로그인 필요, 그 외 권한 제한 없음.

## 2. 프론트엔드

- 새 화면(라우트 `/explore`), `TopBar`에 진입 버튼 추가(로그인 후에만 노출).
- 검색창 + 자동완성 드롭다운(디바운스, `/graph/search` 호출, 후보에 타입 뱃지 표시).
- `react-force-graph-2d` 신규 의존성 추가. 캔버스에 중심 노드(강조 스타일) + 이웃 노드(`NODE_COLOR_CLASS` 재사용) + 관계 라벨 달린 화살표를 렌더링. 확대/축소/맞춤/리셋 컨트롤(라이브러리 내장 zoom/pan + `zoomToFit()` 리셋 버튼).
- 관계 타입별 "총 N건 중 상위 M건 표시" 텍스트를 캔버스 옆/아래에 나열.
- 노드 클릭 시 우측 패널에 그 노드의 실제 속성(`properties`) 표시.
- 상태: 검색 로딩/결과없음/에러, 이웃 조회 로딩/에러, 이웃 0건(고립 노드) 안내.
- 신규 파일: `lib/graph.ts`(`searchGraphEntities`, `fetchGraphNeighbors`), `schemas.ts`에 `GraphSearchResultSchema`/`GraphNeighborsResponseSchema` 추가.

## 3. 테스트

- 백엔드: `entity_search.py` 추출 후 기존 `test_resolve_entity.py`가 그대로 통과해야 함(리팩터링 회귀 확인). `/graph/search`, `/graph/{entityType}/{id}/neighbors`에 대한 새 테스트(Neo4j 드라이버 mock, 상한/총개수 계산 검증) 추가.
- 프론트: 이 프로젝트에 프론트 테스트 프레임워크(vitest/jest 등)가 아예 없어 신규 도입은 이번 범위 밖 — `tsc -b`/`eslint`로만 검증하고 수동으로 클릭 확인한다.

## 확실하지 않은 부분 / 향후 과제

- 멀티홉 확장(이웃의 이웃)은 이번에 안 만들고, 필요성이 확인되면 별도 스펙으로 다룬다.
- 상한을 넘는 이웃을 어떻게 추가로 보여줄지(스크롤 리스트 등)는 이번 최소 구현을 써본 뒤 판단한다.
- `react-force-graph-2d`의 실제 성능(특히 이웃이 상한(50건) 근처로 꽉 찼을 때 렌더링 속도)은 구현 후 실측이 필요하다.
