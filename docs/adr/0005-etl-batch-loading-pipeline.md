# 5. CSV → Neo4j 배치 적재 파이프라인 (워터마크 · Bolt 단일화)

## 상태

폐기 (2026-08-20) — 구조화 MVP 전환으로 대체됨

> **2026-08-20 정정**: 이 문서가 구성한 `etl/export_to_csv.py`/`etl/load_to_neo4j.py`/`etl/run_monthly.py`/`etl/reset_month.py` 등 CSV 기반 배치 적재 파이프라인은 삭제됐다. AdventureWorks(PostgreSQL)를 PostgreSQL 드라이버로 직접 읽어 Neo4j에 적재하는 새 파이프라인(`etl/postgres_restore.py`, `etl/structured_mvp_*.py` 예정)으로 대체됐다. 계획: `docs/superpowers/plans/2026-08-19-structured-mvp-data-loading.md`. 이 문서는 과거 결정 기록으로만 남긴다.

## 한 줄 요약

> AdventureWorks 데이터를 그래프 스키마(0004)에 따라 Neo4j에 적재하는 ETL 파이프라인을 `etl/` 폴더에 구성한다. 마스터는 1회, 트랜잭션은 배치 단위로 지속 적재하며, 평상시엔 워터마크 기반 증분 적재를, 정정·재처리가 필요할 땐 범위를 명시한 강제 재적재를 쓴다. 적재 경로는 Bolt 드라이버(`load_to_neo4j.py`) 하나로 통일한다.

---

## 배경 — 왜 이 결정이 필요했나

- 확정된 그래프 스키마(0004: 11개 노드·13개 관계, 마스터/트랜잭션 분리, 관계 그룹 A/B/C)를 기준으로 실제 데이터를 Neo4j에 적재해야 했다.
- **멘토 조언**: 발표 시연에서 "적재된 데이터 중 특정 기간을 지웠다가, 적재 스크립트를 다시 실행해서 채워지는" 것을 보여주기로 했다. 그러려면 트랜잭션 데이터를 임의 구간 단위로 삭제·재적재할 수 있는 구조가 필요하다.
- **발표 시연 시나리오**: 데이터 날짜를 발표일 근처로 조정해서, "지금 계속 새 데이터가 들어오는" 상황을 재현하기로 했다. 이때 아직 도래하지 않은 미래 날짜의 데이터가 미리 적재되면 시연이 성립하지 않으므로, 적재 시점 기준으로 "여기까지만" 끊어주는 상한이 필요했다.
- **실무 대비**: 대량 데이터·동시 실행·재처리 같은 질문에 답할 수 있는 구조가 필요했다. 이 프로젝트는 정적 과거 이력 데이터를 다루지만, 파이프라인 자체는 실제로 데이터가 계속 유입되는 상황을 가정하고 설계한다.
- **팀 결정 4가지**(폐기 사유 조회 시점 처리, 현재 데이터엔 폐기 사유 누락 없음, BOM 유효기간은 조회 시점 필터, 비활성 공급업체는 적재 전 제외)는 그대로 유효하며 이 파이프라인에 반영한다.

  | 결정 | 반영 위치 |
  | --- | --- |
  | 폐기 사유는 조회 시점 `OPTIONAL MATCH`로 처리 | `load_to_neo4j.py`는 단순 `MERGE`만 하고, 필터는 조회 쿼리 책임으로 둔다 |
  | 현재 데이터엔 폐기 사유 누락 없음 | `export_to_csv.py`에서 `dropna()`로 자연히 제외(별도 예외 분기 없음) |
  | BOM 유효기간은 조회 시점 필터 | `REQUIRES_COMPONENT`는 `startDate`/`endDate`만 적재하고, 유효기간 필터는 조회 쿼리 책임으로 둔다 |
  | 비활성 공급업체는 적재 전 제외 | `export_master()`에서 `SUPPLIES` CSV 생성 전에 `Supplier.active`가 `false`인 행을 제외 |

- **운영 환경**: Neo4j는 팀이 운영하는 원격 공유 서버(`.env`의 `NEO4J_URI`)이고, 그 서버 파일시스템에는 접근 권한이 없다. 적재 경로는 Bolt 프로토콜(네트워크)로만 접속 가능한 것을 전제로 설계한다.
- **리포 구조**: 모노레포로 구성했고(0001), `backend/requirements.txt`에는 pandas·openpyxl 같은 ETL 전용 라이브러리를 넣지 않는다. `backend/`는 API 서빙 전용으로 유지한다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. 마스터 / 트랜잭션 분리 (0004 참고)

11개 노드를 시간에 묶이는지 여부로 나눈다. 자세한 노드·관계 목록과 분류 근거는 0004에 정리돼 있다.

| 구분 | 노드 | 관계 |
| --- | --- | --- |
| 마스터(1회 적재) | Product, Supplier, ProductCategory, ProductSubcategory, Location, ScrapReason | SUPPLIES, REQUIRES_COMPONENT, STOCKED_AT, IN_SUBCATEGORY, IN_CATEGORY |
| 트랜잭션(배치 단위 지속 적재) | PurchaseOrder, PurchaseOrderLine, SalesOrder, WorkOrder, RoutingOperation | HAS_LINE, PLACED_WITH, FOR_PRODUCT, CONTAINS_PRODUCT, HAS_OPERATION, PERFORMED_AT, PRODUCES, SCRAPPED_DUE_TO |

`export_to_csv.py`가 `master`/`tx` 두 모드로 CSV를 만들고, `load_to_neo4j.py`가 적재 전 검사 → 제약조건 → 마스터 → 트랜잭션 순서로 적재한다(적재 전 검사는 10번 참고).

### 2. 관계 적재를 자연키 유무 · 다건 허용 여부로 3그룹 분류 (0004 참고)

재실행해도 중복이 안 생기게, `CREATE` 대신 그룹별로 다른 방식을 쓴다.

- **그룹 A**(자연키 있음): `MERGE`(자연키) + `SET`(나머지 속성) — SUPPLIES, REQUIRES_COMPONENT, STOCKED_AT, CONTAINS_PRODUCT
- **그룹 B**(속성 없음, 다건 허용): 단순 `MERGE` — HAS_LINE, HAS_OPERATION
- **그룹 C**(단일 타깃): 값이 바뀔 수 있는 마스터-마스터 관계(IN_SUBCATEGORY, IN_CATEGORY)만 지우고-다시-만들기, 나머지 트랜잭션-마스터 관계 5개는 생성 시 FK가 고정되고 재배정되지 않으므로 단순 `MERGE`

**그룹 A 중 마스터 관계(SUPPLIES, REQUIRES_COMPONENT, STOCKED_AT)는 prune을 추가한다.** `MERGE+SET`은 CSV에 있는 자연키를 만들거나 갱신하지만, CSV에서 빠진(원본에서 사라진) 자연키는 그대로 방치한다. 예를 들어 공급업체가 비활성화되면 팀 결정에 따라 `SUPPLIES` CSV에서 제외되지만, `MERGE`만으로는 기존 `SUPPLIES` 관계가 지워지지 않아 "비활성 공급업체인데 여전히 공급 관계가 있다"는 불일치가 남는다. 마스터 관계는 매번 export가 **그 시점의 전체 현황**이므로(3번 참고), 적재 직후 "이번 export에 없는 자연키를 가진 기존 관계 삭제" 단계를 추가한다.

```
UNWIND $currentKeys AS k WITH collect(k) AS keys
MATCH ()-[r:SUPPLIES]->() WHERE NOT r.supplyKey IN keys DELETE r
```

트랜잭션 관계(CONTAINS_PRODUCT)에는 이 prune을 적용하지 않는다. 트랜잭션 export는 워터마크 기반 부분 수집이라, "이번 배치에 없다"가 "더 이상 존재하지 않는다"를 의미하지 않기 때문이다(적용하면 다른 시점의 정상 데이터를 삭제하게 된다).

### 3. 트랜잭션 적재를 세 가지 모드로 나눈다 — 초기 백필 / 실시간 증분 / 강제 재적재

- **초기 백필** (`export_to_csv.py tx --before <AS_OF>`): 기준 시점(`AS_OF`) 이전의 전체 이력을 배치 크기로 잘라 CSV로 만든다. 최초 1회, 과거 데이터를 한 번에 채워 넣을 때 쓴다.
- **실시간 증분** (`export_to_csv.py tx --since-last --as-of <AS_OF>`): Bolt로 라벨별 `MAX(날짜 컬럼)`을 조회해 워터마크로 삼고, `watermark < date <= as_of` 범위만 뽑는다. 스케줄러가 주기적으로 실행하는 기본 동작이다.
- **강제 재적재** (`export_to_csv.py tx --month YYYY-MM`): 특정 기간을 명시적으로 지정해 다시 뽑는다. 삭제 후 재적재 시연, 데이터 정정·재처리(backfill/reprocessing)에 쓴다.

세 모드 모두 같은 `load_to_neo4j.py`(그룹 A/B/C MERGE 로직)를 그대로 재사용한다. 재실행 안전성은 "어떤 모드로 export했는가"가 아니라 "MERGE가 자연키/구조 기준으로 동작한다"는 사실에서 나오므로, 모드를 늘려도 로딩 쪽 로직은 손댈 필요가 없다.

`watermark < date <= as_of` 중 상한(`as_of`)이 필요한 이유: 발표 시연에서 데이터 날짜를 발표일 근처로 조정해두면, 하한(워터마크)만으로 거르는 경우 아직 오지 않은 미래 날짜 데이터까지 첫 실행에 한꺼번에 끌려 들어온다. `as_of`는 기본값이 실행 시점의 시스템 날짜이며, 리허설 등에서 명시적으로 오버라이드할 수 있다.

### 4. 적재는 Bolt(`load_to_neo4j.py`) 하나로 통일한다

CSV를 로컬에서 읽어 1,000행 단위로 `UNWIND $rows AS row ...` 배치를 만들어 Bolt 드라이버(`neo4j` 파이썬 패키지)로 전송한다. 제약조건 생성부터 검증까지 전 과정이 네트워크(Bolt) 접속만으로 끝나며, Neo4j 서버의 파일시스템에 접근할 필요가 없다.

파일시스템 기반 `LOAD CSV`를 쓰지 않는 이유: 이 방식은 Neo4j 서버가 실행 중인 머신 자신의 `/import` 디렉터리만 읽을 수 있는데, 팀이 운영하는 Neo4j는 원격 공유 서버(`.env`의 `NEO4J_URI`)이고 그 서버의 파일시스템 접근 권한이 없다. 로컬에 CSV를 아무리 준비해도 원격 서버의 `/import`에 넣을 방법이 없어 애초에 성립하지 않는 경로다. 실제 서비스 환경에서도 DB 서버와 ETL 실행 위치가 물리적으로 분리되는 게 일반적이므로, Bolt를 유일한 경로로 삼는다.

### 5. `etl/` 폴더 구성

```
etl/
├── data/                (원본 xlsx, git 추적 제외)
├── import/              (export 결과, git 추적 제외)
├── requirements.txt     (pandas, openpyxl, neo4j, PyYAML — backend와 별도 관리, neo4j는 backend와 버전 통일)
├── export_to_csv.py     (master / tx --before / tx --since-last / tx --month — 엑셀 -> CSV, 적재 파이프라인의 첫 단계)
├── graph_constraints.py (제약조건 생성 — 0007 참고)
├── load_to_neo4j.py     (Bolt 기반 적재 — 적재 전 검사 → 제약조건 → 마스터 → 트랜잭션 → 검증, 마스터 관계 prune 포함)
├── run_monthly.py       (오케스트레이터 — export_to_csv.py + load_to_neo4j.py를 이어서 실행, 동시 실행 방지 락 포함)
└── reset_month.py       (시연 · 복구용 — 특정 기간 삭제, Bolt 기반)
```

ETL은 API 서빙과 무관한 독립 배치 작업이라 `backend/`와 분리한다(근거는 0001과 동일 — 담당 영역별 폴더 분리). Bolt 경로만 쓰므로 `docker-compose.yml`에 Neo4j 컨테이너용 `/import`·`/etl` 마운트를 추가할 필요가 없다 — CSV는 항상 로컬(ETL 실행 위치)에서 읽어 드라이버로 전송하기 때문이다.

### 6. 환경변수는 루트 `.env`를 재사용

`.env.example`에 이미 `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_URI`가 정의돼 있고 `backend/core/config.py`가 이미 이 값을 쓴다. ETL도 같은 값을 그대로 재사용한다. 접속 정보를 두 곳에서 따로 관리하면 언젠가 어긋난다는 원칙(0001)을 그대로 따른다.

### 7. 원본 xlsx는 git에 커밋하지 않는다

`AdventureWorks_전체사슬_32시트_한글.xlsx`(약 32MB)는 `etl/data/`에 두되 `.gitignore`로 추적 제외한다. 팀원 각자 로컬에 파일을 받아서 이 경로에 두면 `export_to_csv.py`가 스크립트 기준 상대경로로 찾는다.

### 8. 위험 대응

| 위험 | 처리 |
| --- | --- |
| 동시 실행 충돌 | `run_monthly.py` 시작 시 `etl/.lock` 파일 존재 여부 확인, 있으면 종료 |
| 배치 도중 실패 · 네트워크 중단 | 1,000행 단위 트랜잭션 + `MERGE` 자체가 복구 전략(재시도 시 이미 반영된 배치는 no-op) |
| 지연 도착 데이터 | 워터마크에서 며칠 룩백(lookback) 후 재스캔(겹쳐도 `MERGE`라 안전) |
| 소스 컬럼 변경 | `load_to_neo4j.py`가 적재 시작 전(DB 접속 전) `check_csv_columns()`로 자동 검출 후 중단(10번 참고) |
| 그룹 A 마스터 관계가 삭제된 원본을 못 지우는 문제 | 2번의 prune 단계로 해결(마스터 한정) |

### 9. 시연 · 복구 시나리오

`reset_month.py --month <YYYY-MM>`로 해당 기간의 WorkOrder/RoutingOperation, PurchaseOrder/PurchaseOrderLine, SalesOrder만 삭제하고 마스터는 보존한다(삭제 전 미리보기, 기본적으로 사람 확인 `y`/`N`, `--yes`로 생략 가능). 삭제 후 3번의 강제 재적재 모드(`--month`)로 같은 기간을 다시 적재하면, MERGE 기반이라 중복 없이 채워진다.

`reset_month.py`의 삭제 쿼리(`CALL { WITH x ... } IN TRANSACTIONS OF 500 ROWS`)는 Neo4j 5.26에서 "변수 스코프 절 없는 `CALL` 서브쿼리" deprecation 경고가 뜬다(2026-08-14 확인). 동작엔 영향 없지만 `CALL (x) { ... }` 문법으로 갱신했다.

### 10. 적재 전 자동 검사를 `load_to_neo4j.py`에 내장 (2026-08-14)

원래 `check_csv_columns.py`는 별도 스크립트로 존재해서, `load_to_neo4j.py` 실행 전에 사람이 따로 기억해서 돌려야 했다. `run_monthly.py`로 트랜잭션 적재(특히 워터마크 증분)가 스케줄러 자동 실행으로 넘어가는 중이라, "체크를 깜빡하면 그만"인 수동 단계는 위험하다고 판단해 `load_to_neo4j.py` 안으로 흡수했다.

- `check_csv_columns.py` 파일은 삭제하고, 로직을 `load_to_neo4j.py`의 `check_csv_columns()` 함수로 그대로 옮겼다(기존 `MASTER_NODE_STEPS` 등 STEP 목록을 그대로 재사용하므로 별도 파일일 필요가 없어졌다).
- `main()`이 **DB에 연결하기 전에** 이 함수(와 제약조건 위반 검사 — 0007 참고)를 먼저 호출한다. 문제를 전부 모아서 어떤 라벨/파일의 어떤 컬럼이 문제인지 출력한 뒤 `SystemExit(1)`로 중단하므로, 원격 서버에 연결도 하지 않고 실패한다.
- `run_monthly.py`는 `subprocess.run` 결과가 실패면 이미 `SystemExit`로 멈추는 구조라 별도 배선 변경이 필요 없었다.

**실행 검증(2026-08-14)**: `tx_backfill`·`tx_2014-05`(둘 다 정상 데이터)에 대해 `check_csv_columns()`/`check_constraint_violations()` 둘 다 문제 0건을 확인했다. 그리고 스크래치 사본에 일부러 결함 3종(컬럼 누락 1건, 존재 위반 1건, 유일성 위반 1건)을 심어서 실행했더니 정확히 3건 다 잡아냈다(실제 `etl/import` 데이터는 건드리지 않음). `pytest backend/tests/graph_schema/` 21개도 재확인.

## 검토했으나 채택하지 않은 대안

**1. row 단위 watermark 없이, 매번 전체를 다시 export·적재.**

구현이 가장 단순하고, 지금 데이터 규모(마스터 수백~수천 건, 트랜잭션도 비슷한 규모)에서는 이 방식도 실질적으로 낭비가 아니다. 다만 실시간 증분(3번)을 표준으로 채택한 이유는 확장성 때문이다 — 데이터가 계속 쌓이는 실제 운영 상황을 가정하면 매번 전체를 다시 보내는 비용이 커진다. 그래서 초기 백필에는 여전히 "전체를 배치로 나눠 한 번에" 방식을 쓰고, 이후 반복 실행에는 워터마크를 쓰는 쪽으로 절충했다.

**2. 별도 상태 파일로 워터마크 관리.**

마지막 적재 시각/ID를 파일이나 별도 저장소에 기록하는 방법도 검토했지만, Neo4j 자체에 이미 적재된 데이터가 있으므로 `MATCH (n:Label) RETURN max(n.date)`로 워터마크를 즉시 구할 수 있다. 별도 상태를 관리하면 그래프와 상태 파일이 어긋날 위험만 늘어난다.

**3. 진짜 CDC(change data capture) 기반 증분 동기화.**

소스 시스템의 변경 이벤트를 스트리밍으로 받는 방식도 검토했지만, 이 프로젝트는 정적 과거 이력 데이터를 다루는 6주짜리 프로젝트라 구현 비용 대비 얻는 게 적다. 실시간 데이터가 실제로 계속 유입되는 서비스로 확장되는 시점에 재검토한다.

**4. 파일시스템 기반 `LOAD CSV` + cypher-shell 경로를 병행 지원.**

로컬 개발 환경에서는 여전히 유효한 방식이지만, 실제 운영 서버가 파일시스템 접근이 안 되는 원격 공유 서버라는 조건에서는 이 경로가 항상 성립하는 게 아니다. 두 경로를 병행하면 "지금 어느 경로를 써야 하는가"를 매번 판단해야 하는 분기 로직이 필요해지고, 로컬에서만 되는 경로가 있다는 것 자체가 팀 전체가 항상 쓸 수 있는 방법이 아니라는 뜻이다. Bolt 하나로 통일해 이 판단 자체를 없앴다.

## 결과 및 트레이드오프

- 그룹 A/B 관계는 전부 `MERGE` 기반(그룹 A 중 마스터는 prune 포함)이라, 어떤 모드(백필/증분/강제 재적재)로 실행해도 중복이 생기지 않는다.
- `etl/`이 `backend/`와 완전히 분리돼 있어 서빙 서버 배포에 ETL 의존성이 섞이지 않는다.
- Bolt 하나로 통일하면서 `docker-compose.yml`에 Neo4j용 CSV 마운트가 필요 없어졌다 — 어떤 환경(로컬/원격)에서도 동일한 스크립트로 적재한다.
- 워터마크는 날짜 컬럼(`startDate`/`orderDate`) 기준이라, 이미 지나간 워터마크보다 이전인 레코드의 후속 상태 변경(예: 진행 중이던 WorkOrder가 나중에 완료 처리됨)은 자동으로 재수집되지 않는다. 이런 정정은 3번의 강제 재적재 모드로 수동 처리해야 한다.
- 실시간 증분 모드(`--since-last`)는 사람이 스케줄러를 등록해야 주기적으로 돈다. 실제 등록(Windows 작업 스케줄러/cron)은 이번 범위에 포함하지 않고, 실행 명령만 준비해둔다.
- **실행 검증(2026-08-13)**: 로컬 docker-compose Neo4j(팀 공유 서버와 무관한 격리 환경)에 실제 xlsx 원본으로 검증했다.
  - 마스터: `export_to_csv.py master` → Product 504·Supplier 104·ProductCategory 4·ProductSubcategory 37·Location 14·ScrapReason 16, SUPPLIES 443(비활성 공급업체 17곳 제외)·REQUIRES_COMPONENT 2576·STOCKED_AT 1069·IN_SUBCATEGORY 295·IN_CATEGORY 37. 같은 CSV로 `load_to_neo4j.py`를 두 번 실행해도 DB 카운트가 그대로였고(idempotent), prune 단계도 `-0`(삭제 대상 없음)으로 정상 동작했다.
  - 초기 백필: `tx --before 2011-08-01` → PurchaseOrder 8·SalesOrder 479·WorkOrder 2434, CSV 행수와 적재 후 DB 카운트가 정확히 일치.
  - 실시간 증분: `tx --since-last --as-of 2011-10-01` → 워터마크(하한)~as-of(상한) 범위만 뽑혀 SalesOrder +439·WorkOrder +2423 추가됨을 확인. 같은 as-of로 재실행하면 0건(idempotent). as-of를 2011-12-01로 늘리면 그 구간만 추가로 뽑힘(SalesOrder +506·WorkOrder +2730) — 워터마크 하한과 as-of 상한이 둘 다 의도대로 동작함을 확인했다.
  - 시연·복구 시나리오: `reset_month.py --month 2011-09 --yes`로 WorkOrder 1207건·SalesOrder 157건 삭제(마스터 Product 504건은 그대로 보존) → `run_monthly.py --month 2011-09`(강제 재적재)로 재적재하니 WorkOrder·SalesOrder 카운트가 삭제 전과 정확히 동일하게 복원됨을 확인했다.
  - 동시 실행 방지: `etl/.lock` 파일이 있는 상태에서 `run_monthly.py`를 실행하면 즉시 종료(exit 1)함을 확인했다.
  - `check_csv_columns.py`를 `--dir tx_backfill`·`--dir tx_incremental`·`--month 2011-09` 세 경우 모두 실행해 전부 `OK`를 확인했다(이 스크립트는 이후 `load_to_neo4j.py`에 통합되고 삭제됨 — 10번 참고).
  - `graph_schema.yaml` 변경(태그 분리 등) 이후 `pytest backend/tests/graph_schema/` 21개 전부 통과 — PR #8(`feat/schema-serializer`) 구현에 영향이 없음을 재확인했다.

## 확실하지 않은 부분

- 워터마크가 날짜 컬럼 기준이라 발생하는 위 한계(진행 중 항목의 상태 변경 미반영)는, 지금 다루는 정적 과거 이력 데이터에는 해당하지 않지만 실제 운영 DB에 연결하는 시점에는 재검토가 필요하다. `modifiedAt` 같은 별도 컬럼을 워터마크 기준으로 함께 쓰는 방법이 후보다.
- 동시 실행 방지 락(`etl/.lock`)은 단일 머신 · 단일 스케줄러를 전제로 한다. 여러 머신에서 동시에 실행될 수 있는 환경으로 확장되면 파일 락으로는 부족하고, DB 기반 락이나 스케줄러 자체의 동시성 제어가 필요하다.
- `RoutingOperationKey`(합성키)가 공정 순서 재조정 시 안정성이 깨질 수 있는 문제는 여전히 남아 있다(0004 참고). 원본 시스템에 독립적인 불변 식별자가 있다면 그걸 키로 쓰는 게 근본적인 해결책이다.

## 참고 자료

- 0004 (그래프 스키마 설계, 마스터/트랜잭션 분리, 관계 그룹 A/B/C 근거)
- 0007 (그래프 스키마 제약조건 4종 — `load_to_neo4j.py`의 제약조건 생성 단계가 여기서 나온다)
- Neo4j Cypher Manual, "MERGE" — 재실행 안전한 적재 패턴
- Apache Airflow 공식 문서, "Backfill" — 특정 기간을 명시적으로 재실행하는 표준 패턴
- dbt 공식 문서, "Incremental models" — 평상시 증분 + `--full-refresh` 이원화 패턴
