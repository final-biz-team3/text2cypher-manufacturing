# 정형 MVP Neo4j 적재 규칙

## 1. 적재 원칙

- PostgreSQL 복원이 완료되고 원본 검증이 통과한 후 Neo4j를 동기화한다.
- PostgreSQL이 원본이며 Neo4j는 재생성 가능한 read model이다.
- 이름이 아니라 원본 business key로 `MERGE`한다.
- 적재는 멱등해야 한다. 같은 스냅샷을 두 번 실행해도 건수가 증가하지 않아야 한다.
- 노드와 관계마다 `syncRunId`를 기록하여 이번 동기화에 없는 데이터만 안전하게 정리한다.

## 2. 실행 순서

```text
1. schema/structured_mvp_constraints.cypher 실행
2. 모든 인덱스가 ONLINE인지 확인
3. syncRunId 생성
4. Product, Supplier, WorkOrder, RoutingOperation, Location, ScrapReason 적재
5. SUPPLIES, REQUIRES_COMPONENT, PRODUCES 적재
6. HAS_OPERATION, PERFORMED_AT, SCRAPPED_DUE_TO 적재
7. 원본 참조 누락 검사
8. stale 관계 prune
9. stale 업무 노드 prune
10. 건수·business key·질의 fixture 검증
11. 동기화 성공 상태 기록
```

관계를 먼저 삭제하지 않는다. 모든 새 데이터 적재와 검증이 끝난 뒤 `syncRunId`가 다른 stale 데이터만 정리한다.

## 3. 배치 처리

- 시작 배치 크기: 1,000행
- 권장 범위: 1,000~5,000행
- 한 트랜잭션에서 전체 데이터를 처리하지 않는다.
- 재시도 시 동일한 `syncRunId`와 business key로 다시 `MERGE`할 수 있어야 한다.

예시:

```cypher
UNWIND $rows AS row
MERGE (p:Product {productId: row.productId})
SET p.name = row.name,
    p.sellableFinishedGood = row.sellableFinishedGood,
    p.sourceModifiedAt = localdatetime(row.sourceModifiedAt),
    p.syncRunId = $syncRunId;
```

## 4. 관계 MERGE 기준

### 고유 원본 키가 있는 관계

```cypher
MATCH (assembly:Product {productId: row.assemblyProductId})
MATCH (component:Product {productId: row.componentProductId})
MERGE (assembly)-[r:REQUIRES_COMPONENT {bomId: row.bomId}]->(component)
SET r.quantityPerAssembly = row.quantityPerAssembly,
    r.startDate = date(row.startDate),
    r.endDate = CASE WHEN row.endDate IS NULL THEN NULL ELSE date(row.endDate) END,
    r.syncRunId = $syncRunId;
```

### 끝점으로 유일한 관계

`PRODUCES`, `HAS_OPERATION`, `PERFORMED_AT`, `SCRAPPED_DUE_TO`는 시작·도착 노드 조합으로 `MERGE`한다.

## 5. 참조 누락 처리

관계 적재 전에 다음을 계산한다.

- Product가 없는 BOM assembly/component
- Supplier 또는 Product가 없는 ProductVendor
- WorkOrder가 없는 WorkOrderRouting
- Location이 없는 WorkOrderRouting
- ScrapReason이 없는 폐기 WorkOrder

하나라도 존재하면 관계를 조용히 버리지 않고 적재를 실패시킨다. 실패한 business key 목록을 로그에 남긴다.

## 6. Prune 규칙

### 관계

이번 동기화에 포함되지 않은 업무 관계를 삭제한다.

```cypher
MATCH ()-[r:SUPPLIES|REQUIRES_COMPONENT|PRODUCES|HAS_OPERATION|PERFORMED_AT|SCRAPPED_DUE_TO]->()
WHERE r.syncRunId <> $syncRunId OR r.syncRunId IS NULL
DELETE r;
```

### 노드

관계 prune 이후 stale 업무 노드를 삭제한다. 온톨로지 노드는 이 단계에서 삭제하지 않는다.

```cypher
MATCH (n)
WHERE any(label IN labels(n) WHERE label IN $businessLabels)
  AND (n.syncRunId <> $syncRunId OR n.syncRunId IS NULL)
DETACH DELETE n;
```

`$businessLabels`는 정확히 다음 여섯 개로 제한한다.

```text
Product, Supplier, WorkOrder, RoutingOperation, Location, ScrapReason
```

## 7. 성공 검증

### 필수 검사

- 노드 business key 중복 0건
- 관계 원본 키 중복 0건
- 고아 관계 입력 0건
- 같은 적재 두 번 실행 후 노드·관계 건수 동일
- Q12~Q20 fixture의 시작 노드 전부 존재
- BOM 기준일 `2014-08-08` 유효 경로 존재
- 작업지시 17747의 공정 순서 1, 6과 작업장 10, 50 존재
- Product 680에서 Product 492 필요수량이 10개 생산 기준 80으로 계산됨

### 인덱스 검사

```cypher
SHOW CONSTRAINTS;
SHOW INDEXES YIELD name, state, type, labelsOrTypes, properties
WHERE name IN ['product_name', 'supplier_name', 'term_normalized_text']
RETURN *;
```

모든 인덱스의 `state`가 `ONLINE`이어야 한다.

## 8. 실패와 롤백

- prune 이전 실패: 기존 정상 그래프를 유지하고 같은 `syncRunId`로 재시도한다.
- prune 이후 실패: 동기화 완료 상태를 기록하지 않고 이전 백업에서 재생성한다.
- 현재 규모에서는 Neo4j read model 전체를 다시 만드는 방식도 허용한다.
- PostgreSQL 원본은 Neo4j 롤백 과정에서 절대 변경하지 않는다.

