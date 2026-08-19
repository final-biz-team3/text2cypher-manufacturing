# Neo4j 정형 MVP 물리 설계 검증 보고서

## 최종 판정

**조건부 구현 승인**으로 판단한다. 업무 그래프 6개 노드·6개 관계와 경량 온톨로지 5개 노드·6개 관계가 Q12~Q20 계약을 지원하며, 제약조건·인덱스 DDL은 실제 Neo4j 5.26.12 Community에서 실행 및 재실행에 성공했다.

조건은 다음 두 가지다.

1. 적재 구현자는 `syncRunId`를 사용한 검증 후 prune 순서를 지켜야 한다.
2. 온톨로지 실제 seed와 QueryTemplate registry는 Gold 쿼리 확정 후 별도로 적재해야 한다.

## 검증 대상

- `schema/structured_mvp_graph_schema.yaml`
- `schema/structured_mvp_constraints.cypher`
- `docs/design/structured_mvp_source_mapping.md`
- `docs/design/structured_mvp_loading_rules.md`
- `queries/query_contracts.json`
- `queries/reference/query_parameters.json`

## 구조 검증

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

## 인덱스 검증

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

## 적재 규칙 검증

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

## 자동 테스트

```text
19 passed
Ruff: All checks passed
```

검증 항목에는 YAML 구조, 노드·관계 참조, 원본 컬럼 매핑, Query Contract 라우팅, 핵심 loadCondition, 수치 중복 방지, 인덱스 최소성, YAML·DDL 이름 일치가 포함된다.

## 설계상 남은 가정

### RoutingOperation 합성키

`workorderid-productid-operationsequence`는 현재 스냅샷에서 적합하다. 운영 중 공정 순서가 변경되면 동일 공정이 새 키로 인식될 수 있으므로 실제 운영 원본에 안정적인 공정 ID가 있는지 재검토해야 한다.

### 온톨로지 seed

노드·관계·인덱스 구조는 구현 가능하지만 실제 Term, BusinessConcept, QuestionIntent 연결값은 아직 없다. 이는 업무 그래프 적재를 막지는 않으며 Gold 쿼리와 함께 별도 작업으로 진행한다.

### 실제 적재 성능

이번 검증은 설계와 DDL까지다. PostgreSQL 68개 테이블 복원 후 Neo4j 노드·관계 실데이터 적재 시간, 배치 크기와 prune 시간은 적재 담당자 구현 후 측정해야 한다.

## 구현자에게 전달할 결론

현재 파일을 기준으로 업무 그래프 적재 구현을 시작해도 된다. 임의로 노드·관계·속성을 추가하지 말고 Query Contract 변경이 생길 때만 스키마 변경을 검토한다. 구현 완료 판정은 실제 데이터 2회 적재, business key 비교와 Q12~Q20 시작점·경로 검증까지 통과한 시점으로 한다.

