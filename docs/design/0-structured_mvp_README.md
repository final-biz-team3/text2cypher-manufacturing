# Neo4j 정형데이터 MVP 적재 구현 전달 패키지

## 구현 범위

PostgreSQL AdventureWorks 전체 데이터 중 Q12~Q20의 관계 탐색에 필요한 업무 노드 6종과 관계 6종을 Neo4j에 동기화한다. PostgreSQL은 원본 사실과 수치의 기준이며 Neo4j는 관계 탐색용 read model이다.

## 기준 파일

| 파일 | 용도 |
|---|---|
| `schema/structured_mvp_graph_schema.yaml` | 노드·관계·속성·인덱스의 기계 판독 기준 |
| `schema/structured_mvp_constraints.cypher` | Neo4j 제약조건·인덱스 실행 DDL |
| `docs/design/1-structured_mvp_source_mapping.md` | PostgreSQL 테이블·컬럼 매핑 |
| `docs/design/2-structured_mvp_loading_rules.md` | 적재 순서·멱등성·prune·검증 규칙 |
| `queries/query_contracts.json` | Q01~Q20 결과 계약과 실제 fixture |
| `queries/reference/query_parameters.json` | 검증용 실제 파라미터 |

모든 경로는 저장소 루트를 기준으로 한다.

## 구현자가 지켜야 하는 핵심 규칙

1. 원본 ID로 `MERGE`하고 이름으로 노드를 생성하지 않는다.
2. UNIQUE 제약조건을 노드 적재 전에 생성한다.
3. 노드 6종을 모두 적재한 뒤 관계를 적재한다.
4. 비활성 공급업체의 `SUPPLIES` 관계를 만들지 않는다.
5. BOM은 모든 이력을 적재하고 쿼리에서 `bomAsOfDate`로 필터링한다.
6. 폐기 관계는 `scrappedqty > 0 AND scrapreasonid IS NOT NULL`일 때만 만든다.
7. 현재 재고와 폐기 수량은 Neo4j 정답 값으로 사용하지 않는다.
8. 적재는 재실행 가능하고 중복이 없어야 한다.
9. prune은 새 적재와 참조 검증이 끝난 뒤에만 수행한다.
10. 온톨로지 seed 적재는 업무 그래프 적재와 분리한다.

## 구현 완료 조건

- 제약조건과 RANGE 인덱스가 `ONLINE`
- 업무 노드 6종·관계 6종 적재 완료
- 고아 참조 0건
- 동일 스냅샷 2회 적재 후 건수 동일
- Q12~Q20 fixture 시작점과 경로 검증 통과
- PostgreSQL과 Neo4j의 business key 집합 검증 통과

## 이번 패키지에 포함되지 않은 작업

- Gold SQL·Gold Cypher 구현
- 온톨로지 실제 seed 값
- Text-to-SQL·Text-to-Cypher
- 문서 청킹·Vector Index
- 실시간 CDC

