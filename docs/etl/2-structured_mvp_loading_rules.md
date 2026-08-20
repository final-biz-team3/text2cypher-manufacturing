# 정형 MVP Neo4j 적재 규칙

> **2026-08-20 정정 (PR #16 리뷰 josephuk77 3차 대응)**: 이 문서는 원 설계자가 전달한
> prune 기반 적재 설계이며 그대로 보존한다. 실제 구현은 이후 리뷰에서 "쓰기 도중
> 실패 시 라이브 그래프가 부분 갱신 상태로 남을 수 있다"는 지적을 받고 아래처럼
> 바뀌었다 - **prune을 완전히 없애고, 매 실행마다 새 Neo4j 데이터베이스를 만들어
> 그 안에서만 적재·검증한 뒤 통과했을 때만 기본 데이터베이스로 승격**하는 방식이다
> (`etl/run_structured_mvp_sync.py`, `etl/structured_mvp_load.py` 참고). 실패하면
> 기존 기본 데이터베이스는 한 번도 안 건드려진 채로 그대로 남는다(prune 자체가
> 필요 없어짐 - 매번 빈 데이터베이스에서 시작하므로 "stale 데이터"라는 개념이 없다).
>
> 아래 섹션별로 실제와 다른 부분:
> - **2절(실행 순서)**: 실제로는 (a) PostgreSQL에서 노드·관계 전부 추출 (b) 추출
>   결과만으로 쓰기 전 검증(0건/필수키 NULL/중복키/참조무결성) (c) 새 데이터베이스
>   생성 (d) 제약조건 적용 + 전체 적재 (e) 새 데이터베이스 대상 적재 후 검증
>   (f) 통과 시에만 기본 데이터베이스로 승격 순서다. 8~9번(prune)은 없다.
> - **6절(Prune 규칙)**: 전체가 더 이상 적용되지 않는다. 매 실행이 새 빈 데이터베이스에서
>   시작하므로 prune 대상 자체가 없다.
> - **8절(실패와 롤백)**: "prune 이전/이후 실패"라는 구분이 아니라 "쓰기 전 검증
>   실패"(Neo4j 완전히 안 건드림) vs "쓰기 후 검증 실패"(새 데이터베이스만 남고
>   기존 기본 데이터베이스는 그대로)로 바뀌었다. 두 경우 다 기존 기본 데이터베이스는
>   승격 전까지 절대 안 바뀐다.
> - **1·3·4·5·7절**: MERGE 방식, 배치 크기, 관계 키 기준, 참조 누락 검사, 성공
>   판정 기준(같은 스냅샷 두 번 실행 시 건수 동일 등)은 실제 구현과 개념적으로
>   동일하게 유지된다 - 검증이 언제(쓰기 전이냐 후냐) 일어나는지만 바뀌었다.

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

