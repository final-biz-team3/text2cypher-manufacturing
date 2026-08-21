# Neo4j 정형 MVP 물리 설계 검증 보고서

> **2026-08-20 갱신 (PR #16 리뷰 josephuk77 3차 대응)**: 이 문서 제목과 "검증 대상"이
> 가리키는 파일(`schema/structured_mvp_graph_schema.yaml`,
> `schema/structured_mvp_constraints.cypher`)은 지금 이 저장소의 현재 활성 파일이다.
> 그래서 본문 수치도 **현재 구현 기준(업무 6노드·6관계, 제약조건 6개, 조회 인덱스
> 2개)으로 갱신**했다 - 이전엔 원 설계자가 검증한 11노드(업무 6 + 온톨로지 5) 기준
> 수치가 그대로 남아 있어서, 이 문서를 "지금 제출하는 검증 보고서"로 읽으면 실제
> 구현과 안 맞아 혼동을 준다는 지적을 받았다. 원 설계자의 11노드 검증 기록은
> 지우지 않고 문서 맨 아래 "부록"으로 분리해 그대로 보존했다.

## 최종 판정

**조건부 구현 승인**으로 판단한다. 업무 그래프 6개 노드·6개 관계가 Q12~Q20 계약을 지원하며, 제약조건·인덱스 DDL은 실제 Neo4j 5.26.29 Enterprise에서 실행 및 재실행에 성공했다(`etl/run_structured_mvp_sync.py` 라이브 실행으로 반복 검증, 매 실행마다 새 데이터베이스에 적용).

조건은 다음과 같다.

1. 온톨로지(Term/BusinessConcept/QuestionIntent/EntityType/QueryTemplate) 5노드·6관계는 이번 구현에 아예 포함되지 않았다 - RQ01~RQ20 쿼리 계약 어디에도 온톨로지 참조가 없어 Q12~Q20 커버리지에는 영향이 없지만, 향후 온톨로지가 필요해지면 스키마부터 다시 추가해야 한다(`schema/structured_mvp_graph_schema.yaml`의 원본 참고 사본에 구조만 보존돼 있음).

## 검증 대상

- `schema/graph_schema.yaml` (활성 스키마, 업무 6노드·6관계)
- `schema/structured_mvp_constraints.cypher`
- `docs/etl/1-structured_mvp_source_mapping.md`
- `docs/etl/2-structured_mvp_loading_rules.md`
- `queries/query_contracts.json`
- `queries/query_parameters.json`

## 구조 검증

| 항목 | 결과 |
|---|---|
| 업무 노드 | 6종 일치 |
| 업무 관계 | 6종 일치 |
| 온톨로지 | 이번 구현에 미포함(해당 없음) |
| 끊어진 관계 참조 | 0건(쓰기 전 검증에서 매 실행 확인) |
| 고유키 없는 노드 | 0건 |
| SQL 전용 질문의 그래프 혼입 | 0건 |
| Q12~Q17 Graph 커버리지 | 6/6 |
| Q18~Q20 Hybrid 커버리지 | 3/3 |
| Vector·문서 인덱스 | 0개 |

## 인덱스 검증

실제 `neo4j:5.26.29-enterprise`(패치 버전 고정) 컨테이너에서 DDL을 실행했다. `schema/structured_mvp_constraints.cypher`의 DDL 문법 자체는 Community 호환(UNIQUE만 사용, NODE KEY/RELATIONSHIP KEY 미사용)이다.

| 구분 | 수량 | 결과 |
|---|---:|---|
| UNIQUE 제약조건 | 6 | 전체 생성 성공 |
| 제약조건 backing RANGE 인덱스 | 6 | 전체 ONLINE |
| 조회용 RANGE 인덱스 | 2 | 전체 ONLINE |
| 기본 LOOKUP 인덱스 | 2 | 전체 ONLINE |
| DDL 반복 실행 | 성공 | `IF NOT EXISTS`로 재실행해도 오류 없음(실제로는 매 실행마다 새 데이터베이스에 적용, 라이브 검증 다수 완료) |

UNIQUE 제약조건 6개: `product_id`, `supplier_id`, `work_order_id`, `routing_operation_key`, `location_id`, `scrap_reason_id`.

조회용 인덱스는 다음 두 개로 제한했다(온톨로지 `Term.normalizedText`는 이번 구현에 없음).

- `Product.name`
- `Supplier.name`

WorkOrder, RoutingOperation 등은 UNIQUE 제약조건의 backing index가 시작점 조회를 지원하므로 동일 속성의 추가 RANGE 인덱스를 만들지 않았다.

## 적재 규칙 검증

| 규칙 | 결과 |
|---|---|
| 원본 business key로 MERGE | 구현됨 |
| 노드 선적재 후 관계 적재 | 구현됨(쓰기 전 전체 추출·검증 이후) |
| 활성 공급업체만 SUPPLIES 생성 | 구현됨 |
| BOM 전체 이력 적재 | 구현됨 |
| BOM 기준일 조회 필터 | 구현됨 |
| 폐기 관계 생성 조건 | 구현됨 |
| 참조 누락 시 실패 | 구현됨(쓰기 시작 전에 확인, Neo4j 안 건드림) |
| 새 데이터베이스 적재 후 검증, 통과 시에만 기본 데이터베이스로 승격 | 구현됨(prune 대신, `docs/etl/2` 정정 노트 참고) |
| 동일 스냅샷 재실행 시 건수 동일 | 확인됨(로컬/원격 각각 반복 실행으로 검증) |

## 자동 테스트

```text
48 passed
Ruff: All checks passed
```

검증 항목에는 배치 분할, DB 이름 생성, PostgreSQL 추출·정규화, 참조 무결성·중복 키·NULL 키 사전 검증, syncRunId 스코프 건수/픽스처 검증, 부동소수점 허용 오차 비교, 복원 확인 게이트가 포함된다.

## 설계상 남은 가정

### RoutingOperation 합성키

`workorderid-productid-operationsequence`는 현재 스냅샷에서 적합하다. 운영 중 공정 순서가 변경되면 동일 공정이 새 키로 인식될 수 있으므로 실제 운영 원본에 안정적인 공정 ID가 있는지 재검토해야 한다.

### 온톨로지

이번 구현에는 온톨로지 노드·관계 자체가 없다(원본 구조는 `schema/structured_mvp_graph_schema.yaml` 참고 사본에 보존). Gold 쿼리 확정 시점에 필요하면 스키마·제약조건·적재 스펙에 별도 작업으로 추가한다.

### 실제 적재 성능

PostgreSQL 68개 테이블 복원 후 Neo4j 노드·관계 실데이터(WorkOrder 72,591건 등) 적재까지 로컬·원격 양쪽에서 실측했다 - 수 초~수십 초 수준으로 이번 규모에서는 문제없음을 확인했다. 대규모 확장 시 배치 크기 재조정이 필요할 수 있다.

## 구현자에게 전달할 결론

현재 파일을 기준으로 업무 그래프 적재 구현이 완료됐다. 임의로 노드·관계·속성을 추가하지 말고 Query Contract 변경이 생길 때만 스키마 변경을 검토한다. 구현 완료 판정 기준(실제 데이터 반복 적재, business key 비교, Q12~Q20 시작점·경로 검증)은 모두 통과했다.

---

## 부록: 원 설계자 검증 기록 (2026-08-19, 11노드 기준, Neo4j 5.26.12 Community)

> 이 부록은 원 설계자가 `neo4j_structured_mvp_design/` 패키지 전달 시점에 수행한 검증
> 기록을 그대로 보존한다(업무 6 + 온톨로지 5 = 11노드 기준). 위 본문(현재 구현 기준)과
> 다른 별개 기록이다.

**조건부 구현 승인**으로 판단한다. 업무 그래프 6개 노드·6개 관계와 경량 온톨로지 5개 노드·6개 관계가 Q12~Q20 계약을 지원하며, 제약조건·인덱스 DDL은 실제 Neo4j 5.26.12 Community에서 실행 및 재실행에 성공했다.

조건은 다음 두 가지였다.

1. 적재 구현자는 `syncRunId`를 사용한 검증 후 prune 순서를 지켜야 한다.
2. 온톨로지 실제 seed와 QueryTemplate registry는 Gold 쿼리 확정 후 별도로 적재해야 한다.

### 검증 대상(원본)

- `schema/structured_mvp_graph_schema.yaml`(11노드 원본)
- `schema/structured_mvp_constraints.cypher`(원본, 11개 제약)
- `docs/etl/1-structured_mvp_source_mapping.md`
- `docs/etl/2-structured_mvp_loading_rules.md`
- `queries/query_contracts.json`
- `queries/query_parameters.json`

### 구조 검증(원본)

| 항목 | 결과 |
|---|---|
| 업무 노드 | 6종 일치 |
| 업무 관계 | 6종 일치 |
| 온톨로지 노드 | 5종 일치 |
| 온톨로지 관계 | 6종 일치 |
| 끊어진 관계 참조 | 0건 |
| 고유키 없는 노드 | 0건 |
| SQL 전용 질문의 그래프 혼입 | 0건 |
| Q12~Q17 Graph 커버리지 | 6/6 |
| Q18~Q20 Hybrid 커버리지 | 3/3 |
| Vector·문서 인덱스 | 0개 |

### 인덱스 검증(원본)

실제 `neo4j:5.26.12-community` 컨테이너에서 DDL을 실행했다.

| 구분 | 수량 | 결과 |
|---|---:|---|
| UNIQUE 제약조건 | 11 | 전체 생성 성공 |
| 제약조건 backing RANGE 인덱스 | 11 | 전체 ONLINE |
| 조회용 RANGE 인덱스 | 3 | 전체 ONLINE |
| 기본 LOOKUP 인덱스 | 2 | 전체 ONLINE |
| DDL 두 번째 실행 | 성공 | 수량 변화 없음 |

조회용 인덱스는 다음 세 개로 제한했다.

- `Product.name`
- `Supplier.name`
- `Term.normalizedText`

WorkOrder, RoutingOperation 등은 UNIQUE 제약조건의 backing index가 시작점 조회를 지원하므로 동일 속성의 추가 RANGE 인덱스를 만들지 않았다.

### 적재 규칙 검증(원본)

| 규칙 | 결과 |
|---|---|
| 원본 business key로 MERGE | 명시됨 |
| 노드 선적재 후 관계 적재 | 명시됨 |
| 활성 공급업체만 SUPPLIES 생성 | 명시됨 |
| BOM 전체 이력 적재 | 명시됨 |
| BOM 기준일 조회 필터 | 명시됨 |
| 폐기 관계 생성 조건 | 명시됨 |
| 참조 누락 시 실패 | 명시됨 |
| 새 적재 검증 후 stale prune | 명시됨 |
| 온톨로지 노드 prune 제외 | 명시됨 |
| 동일 스냅샷 재실행 검증 | 완료 조건에 포함 |

### 자동 테스트(원본)

```text
19 passed
Ruff: All checks passed
```

검증 항목에는 YAML 구조, 노드·관계 참조, 원본 컬럼 매핑, Query Contract 라우팅, 핵심 loadCondition, 수치 중복 방지, 인덱스 최소성, YAML·DDL 이름 일치가 포함된다.

### 설계상 남은 가정(원본)

**RoutingOperation 합성키**: `workorderid-productid-operationsequence`는 현재 스냅샷에서 적합하다. 운영 중 공정 순서가 변경되면 동일 공정이 새 키로 인식될 수 있으므로 실제 운영 원본에 안정적인 공정 ID가 있는지 재검토해야 한다.

**온톨로지 seed**: 노드·관계·인덱스 구조는 구현 가능하지만 실제 Term, BusinessConcept, QuestionIntent 연결값은 아직 없다. 이는 업무 그래프 적재를 막지는 않으며 Gold 쿼리와 함께 별도 작업으로 진행한다.

**실제 적재 성능**: 이번 검증은 설계와 DDL까지다. PostgreSQL 68개 테이블 복원 후 Neo4j 노드·관계 실데이터 적재 시간, 배치 크기와 prune 시간은 적재 담당자 구현 후 측정해야 한다.

### 구현자에게 전달할 결론(원본)

현재 파일을 기준으로 업무 그래프 적재 구현을 시작해도 된다. 임의로 노드·관계·속성을 추가하지 말고 Query Contract 변경이 생길 때만 스키마 변경을 검토한다. 구현 완료 판정은 실제 데이터 2회 적재, business key 비교와 Q12~Q20 시작점·경로 검증까지 통과한 시점으로 한다.
