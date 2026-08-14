# 0007. 그래프 스키마 제약조건 4종 — Neo4j Enterprise 전환과 graph_schema.yaml 기반 자동 생성

## 상태

확정 (2026-08-14)

## 한 줄 요약

> arrows.app 다이어그램(`ITDA-neo4j2`, 0004)이 KEY/REQUIRED/UNIQUE로 표시한 제약조건을 노드 키·관계 키·존재·유일성·속성 타입 4종으로 분류해 `schema/graph_schema.yaml`에 반영하고, `etl/graph_constraints.py`가 이 yaml을 읽어 Neo4j `CREATE CONSTRAINT` 문을 자동 생성한다. 이 제약조건 대부분이 Enterprise 전용이라, 로컬 docker-compose와 팀 원격 공유 서버 모두 Neo4j Enterprise(평가판)로 전환했다.

---

## 배경 — 왜 이 결정이 필요했나

- arrows.app 스키마 다이어그램(`etl/data/ITDA-neo4j2`, 0004의 근거 자료)에는 속성마다 KEY/REQUIRED/UNIQUE 표시가 이미 돼 있었지만, `schema/graph_schema.yaml`과 실제 Neo4j DB엔 이 정보가 온전히 반영돼 있지 않았다(유일성 제약 11개만 하드코딩돼 있었음).
- `graph_schema.yaml`은 백엔드·프론트엔드·LLM 프롬프트가 함께 참조하는 기준 문서(0001)인데, 정작 "이 속성이 필수인지/유일한지/타입이 뭔지"를 Neo4j가 실제로 강제하는지 여부가 이 파일에 빠져 있었다.
- Neo4j Community는 유일성(UNIQUE) 제약만 지원한다. 노드 키(NODE KEY)·관계 키(RELATIONSHIP KEY)·존재(existence)·속성 타입(`IS TYPED`) 제약은 전부 Enterprise 전용이라, 다이어그램의 제약을 온전히 걸려면 Neo4j 자체를 Enterprise로 바꿔야 했다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. Neo4j Community → Enterprise(평가판) 전환

로컬 `docker-compose.yml`을 `neo4j:5-enterprise`(`NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`)로 바꾸고, 팀 원격 공유 서버(`kosa165.iptime.org`, `.env`의 `NEO4J_URI`, 실제 버전 확인: Neo4j Kernel 5.26.29 enterprise)도 같은 이미지로 전환했다. 평가판이며, 상업적 운영엔 정식 라이선스가 필요하다.

### 2. `graph_schema.yaml`을 제약조건의 유일한 소스로 삼는다

| 제약 종류 | yaml 근거 |
| --- | --- |
| 노드 키 | 노드의 `uniqueKey`/`constraintName` (기존 필드 재사용, 의미만 UNIQUE → NODE KEY로 격상) |
| 관계 키 | 관계의 `naturalKey` (그룹 A 4종만 존재 — 0005 참고) |
| 존재 | `nullable: true`가 **없는** 속성 (다이어그램 REQUIRED에 대응) |
| 유일성 | `unique: true`가 있는 속성 (신규 필드, 8곳) |
| 속성 타입 | 모든 속성의 기존 `type` 필드 |

`backend/graph_schema`의 로더(`models.py`, `extra="ignore"`)는 `nodes.*.properties.*.type`과 `relationships.*.{from,to,properties.*.type}`만 읽고 나머지는 무시하므로, 이 필드들을 추가해도 백엔드/프론트엔드/LLM 프롬프트 동작에는 영향이 없다(`pytest backend/tests/graph_schema/` 21개로 재확인).

### 3. `etl/graph_constraints.py`(신규) — yaml → Cypher 자동 생성

`graph_schema.yaml`을 읽어 `CREATE CONSTRAINT ... IF NOT EXISTS` 문을 생성하는 순수 함수(`build_constraint_statements`) 하나로 구성했다. `etl/load_to_neo4j.py`(0005)는 하드코딩된 `CONSTRAINTS` 리스트 대신 이 함수를 호출한다. 제약조건을 바꾸려면 `graph_schema.yaml`만 고치면 된다.

이름 규칙은 라벨/관계타입을 snake_case로 변환해 자동 생성한다(예: `product_product_number_unique`, `stocked_at_quantity_type`) — yaml에 이름을 일일이 적지 않아도 된다.

### 4. 예외 4곳 — 다이어그램 REQUIRED와 실제 데이터가 어긋나는 속성은 존재 제약 제외

다이어그램은 REQUIRED로 표시했지만, 실제 백필 데이터 감사(2026-08-14, `etl/import/tx_backfill`·`etl/import/master` CSV 기준)에서 빈 값이 확인돼 존재 제약을 걸지 않고 `nullable: true`로 남겼다(속성 타입 제약은 유지).

| 속성 | 빈 값 비율 |
| --- | --- |
| `PurchaseOrder.modifiedAt` | 12/4012 |
| `PurchaseOrderLine.modifiedAt` | 57/8845 |
| `SUPPLIES.modifiedAt` | 54/443 |
| `STOCKED_AT.shelf` | 290/1069 (종전 yaml엔 이 nullable 표시 자체가 빠져 있었음 — 이번에 바로잡음) |

### 5. 제약조건 위반 사전 검사를 `load_to_neo4j.py`에 내장 (2026-08-14)

이번 4번(예외 4곳)은 손으로 CSV를 감사해서 찾아낸 것이다. `graph_schema.yaml`에 앞으로 `unique: true`나 존재 제약을 추가할 때마다 똑같이 손으로 감사해야 한다면 언젠가 빠뜨리게 되고, 그러면 실제 적재 때 배치가 통째로 실패한다. 이 위험을 자동화된 검사로 없앴다.

- `load_to_neo4j.py`에 `check_constraint_violations()` 함수를 추가했다. `graph_schema.yaml`의 존재(`nullable` 없음)·유일성(`unique`/`uniqueKey`/`naturalKey`) 대상 속성을 뽑아, 마스터 + 지정된 트랜잭션 폴더 CSV에서 빈 값·중복 건수를 센다. 파일명은 `graph_constraints.py`의 `_snake()`를 재사용해 라벨/관계타입에서 그대로 유도한다(새 매핑 불필요).
- `main()`이 **DB에 연결하기 전에** 이 함수(와 CSV 컬럼 검사 — 0005 10번)를 먼저 호출한다. 위반이 있으면 어떤 노드/관계의 어떤 속성이 몇 건 문제인지 전부 출력하고 `SystemExit(1)`로 중단한다 — 원격 서버에 연결도 하지 않는다.

## 검토했으나 채택하지 않은 대안

**1. 제약조건을 `load_to_neo4j.py`에 하드코딩(현행 방식 확장).**

지금까지는 유일성 제약 11개를 `CONSTRAINTS` 리스트에 직접 써넣는 방식이었다. 이 방식을 그대로 확장해 존재/타입/키 제약 250여 개를 손으로 추가하는 것도 가능했지만, `graph_schema.yaml`을 고쳐도 이 리스트엔 자동 반영이 안 돼 "yaml엔 있는데 실제 DB엔 없는" 괴리가 생기기 쉽고, 250여 개를 사람이 옮기다 보면 오타·누락 위험이 크다. `graph_schema.yaml`이 여러 곳에서 재사용되는 기준 문서라는 원칙(0001)에도 맞지 않아 기각했다.

**2. 다이어그램의 REQUIRED 표시를 예외 없이 그대로 강제.**

예외 4곳도 존재 제약을 걸고, 대신 적재 전에 빈 값을 채우거나 해당 행을 제외하는 정제 로직을 추가하는 방법도 검토했다. 하지만 이 4곳은 발표 시연에서 실제로 삭제·재적재하는 트랜잭션 데이터(`PurchaseOrder`/`PurchaseOrderLine`/`SUPPLIES`/`STOCKED_AT`)와 겹쳐서, 정제 로직을 잘못 건드리면 시연 데이터 자체가 달라질 위험이 있었다. 실제 데이터를 우선해 이 4곳만 예외로 남기는 쪽을 택했다(4번 참고).

**3. 유일성 제약을 이번 범위에서 제외하고 나중에 추가.**

유일성 후보 8곳이 text2cypher가 자주 조회하는 속성이라 인덱스가 필요했고, 실 데이터에 중복이 없음을 미리 확인했기 때문에 지금 걸어도 위험이 없었다. 그래서 나중으로 미루지 않고 이번에 존재/타입/키와 함께 반영했다.

## 결과 및 트레이드오프

- 노드 키·관계 키 제약이 존재+유일성을 동시에 강제하므로(`IS NODE KEY`/`IS RELATIONSHIP KEY`), 해당 속성엔 별도 존재/유일성 제약을 중복으로 걸지 않는다.
- 유일성·키 제약은 Neo4j가 백킹 인덱스(RANGE)를 자동 생성한다. 존재·타입 제약은 인덱스를 만들지 않는다. text2cypher가 자주 만드는 정확 매칭 조회(`MATCH (n:Label {prop: $value})`)를 가속하려면 유일성 제약이 걸린 속성을 우선 활용하면 된다.
- **실행 검증(2026-08-14, 팀 원격 공유 서버 `kosa165.iptime.org` 기준)**: 적재 전 DB가 완전히 비어 있음(노드 0·관계 0·제약조건 0)을 먼저 확인. `etl/graph_constraints.py`가 생성한 252개 제약(NODE_KEY 11·RELATIONSHIP_KEY 4·UNIQUENESS 7·RELATIONSHIP_UNIQUENESS 1·NODE_PROPERTY_EXISTENCE 72·RELATIONSHIP_PROPERTY_EXISTENCE 20·NODE_PROPERTY_TYPE 107·RELATIONSHIP_PROPERTY_TYPE 30)이 전부 성공(`SHOW CONSTRAINTS`로 확인). 유일성·키 제약은 RANGE 인덱스 23개를 자동 생성함을 `SHOW INDEXES`로 확인. 이 상태에서 마스터 + 전체 백필(`tx --before 2026-08-14`) 원본 엑셀을 처음부터 재적재 — 노드 184,723건·관계 355,021건(총 약 54만 행)을 **3분 5초**만에 위반 0건으로 적재 완료, 카운트는 0005에 기록된 기존 검증값과 정확히 일치.
- **발표 시연용 삭제→재적재 재검증(2026-08-14)**: 실 데이터가 있는 2014-05(WorkOrder 3421·PurchaseOrder 349·SalesOrder 2411)를 골라 `reset_month.py --month 2014-05 --yes`로 삭제 → `export_to_csv.py tx --month 2014-05` → `load_to_neo4j.py --month 2014-05`로 강제 재적재했다. 252개 제약이 걸린 상태에서도 에러 없이 성공했고, 최종 카운트(PurchaseOrder 4012·PurchaseOrderLine 8845·SalesOrder 31465·WorkOrder 72591·RoutingOperation 67131, 관계도 전부 원래 총합과 일치)가 삭제 전과 정확히 동일하게 복원됐다. 이유: 특정 월 데이터는 이미 위반 0건으로 검증된 전체 백필의 부분집합이라, 예외 4곳도 이미 존재 제약에서 빠져 있어 문제가 되지 않는다. 재적재에 쓰이는 `load_to_neo4j.py`가 5번의 검사를 자동으로 거치므로 이 시나리오는 그대로 유지해도 안전하다.
- **제약조건 위반 자동 검사 동작 확인(2026-08-14)**: `tx_backfill`·`tx_2014-05`(정상 데이터)엔 `check_constraint_violations()`가 0건을 보고했고, 스크래치 사본에 일부러 존재/유일성 위반을 심었더니 정확히 잡아냈다(0005 10번 "실행 검증"과 같은 테스트, 자세한 내용은 그쪽 참고).

## 참고 자료

- 0004 (그래프 스키마 설계, 마스터/트랜잭션 분리, 관계 그룹 A/B/C 근거)
- 0005 (CSV → Neo4j 배치 적재 파이프라인 — `load_to_neo4j.py`가 이 문서의 `etl/graph_constraints.py`를 호출)
- Neo4j Cypher Manual, "Constraints" — NODE KEY/RELATIONSHIP KEY/property existence/property type(`IS TYPED`) 제약 문법 및 Enterprise 요구사항 근거
