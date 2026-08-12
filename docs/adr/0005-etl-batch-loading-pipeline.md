# **0005. CSV → Neo4j 배치 적재 파이프라인**

## **상태**

진행 중 (2026-08-11 작성 / 2026-08-11 적재 실행 경로 보완)

## **한 줄 요약**

> AdventureWorks 데이터를 그래프 스키마 다이어그램 v2 기준으로 Neo4j에 적재하는 ETL 파이프라인을 `etl/` 폴더에 별도로 구성한다. 마스터(1회 적재)와 트랜잭션(월별 반복 적재)을 분리하고, 관계는 자연키 유무·다건 허용 여부에 따라 세 그룹(A/B/C)으로 나눠 재실행해도 안전하게 만든다.
> 

---

## **배경 — 왜 이 결정이 필요했나**

2주차 작업으로 확정된 그래프 스키마 다이어그램 v2(11개 노드·13개 관계, arrows.app으로 팀이 확정)를 기준으로 실제 데이터를 Neo4j에 적재해야 했다(설계 근거와 검토 대안은 `docs/adr/0004-graph-schema-v2.md` 참고). 이 결정에 영향을 준 조건은 다음과 같다.

- **멘토 조언**: 발표 시연에서 "적재된 데이터 중 한 달치를 지웠다가, 월별 적재 스크립트를 다시 실행해서 채워지는 걸 보여주는" 방식으로 하기로 했다. 그러려면 데이터를 한 번에 다 넣는 게 아니라 월 단위로 쪼개서 반복 적재할 수 있는 구조가 필요하다.
- **팀 결정 4가지**(폐기 사유 조회 시점 처리, 현재 데이터엔 폐기 사유 누락 없음, BOM 유효기간은 조회 시점 필터, 비활성 공급업체는 적재 전 제외)가 이미 확정되어 있어 이걸 스크립트에 반영해야 했다.
    
    
    | 결정 | 반영 위치 |
    | --- | --- |
    | 폐기 사유는 조회 시점 `OPTIONAL MATCH`로 처리 | `load.cypher`는 단순 `MERGE`만 하고, 필터는 조회 쿼리 책임으로 둔다 |
    | 현재 데이터엔 폐기 사유 누락 없음 | `export_to_csv.py`에서 `dropna()`로 자연히 제외(별도 예외 분기 없음) |
    | BOM 유효기간은 조회 시점 필터 | `REQUIRES_COMPONENT`는 `startDate`/`endDate`만 적재하고, 유효기간 필터는 조회 쿼리 책임으로 둔다 |
    | 비활성 공급업체는 적재 전 제외 | `export_master()`에서 `SUPPLIES` CSV 생성 전에 `Supplier.active`가 `false`인 행을 제외 |
- **리포 구조**: `docs/adr/0001`에서 모노레포로 정했고, `backend/requirements.txt`에는 pandas·openpyxl 같은 ETL 전용 라이브러리가 없다. `backend/`는 API 서빙 전용으로 유지하자는 게 기존 결정과 일치한다.
- **docker-compose.yml에 import 볼륨이 없었다**: 기존에는 `neo4j_data:/data`만 마운트돼 있어 `LOAD CSV`가 읽을 파일을 컨테이너 안에 넣을 방법이 없었다.

## **결정 — 무엇을 어떻게 하기로 했나**

### **1. 마스터 / 트랜잭션 분리 + 월별 배치**

11개 노드를 시간에 묶이는지 여부로 나눴다.

| 구분 | 노드 | 관계 |
| --- | --- | --- |
| 마스터(1회 적재) | Product, Supplier, ProductCategory, ProductSubcategory, Location, ScrapReason | SUPPLIES, REQUIRES_COMPONENT, STOCKED_AT, IN_SUBCATEGORY, IN_CATEGORY |
| 트랜잭션(월별 반복 적재) | PurchaseOrder, PurchaseOrderLine, SalesOrder, WorkOrder, RoutingOperation | HAS_LINE, PLACED_WITH, FOR_PRODUCT, CONTAINS_PRODUCT, HAS_OPERATION, PERFORMED_AT, PRODUCES, SCRAPPED_DUE_TO |

`export_to_csv.py`가 `master`/`tx --month YYYY-MM` 두 모드로 CSV를 만들고, `load.cypher`가 제약조건 → 마스터 → 트랜잭션(파라미터 `$month` 필요) 순서로 적재한다.

### **2. 관계 적재를 자연키 유무·다건 허용 여부로 3그룹 분류**

재실행해도 중복이 안 생기게 하려고, `CREATE` 대신 그룹별로 다른 방식을 쓴다.

- **그룹 A**(자연키 있음): `MERGE`(자연키) + `SET`(나머지 속성) — SUPPLIES, REQUIRES_COMPONENT, STOCKED_AT, CONTAINS_PRODUCT
- **그룹 B**(속성 없음, 다건 허용): 단순 `MERGE` — HAS_LINE, HAS_OPERATION
- **그룹 C**(단일 타깃): 값이 바뀔 수 있는 마스터-마스터 관계(IN_SUBCATEGORY, IN_CATEGORY)만 지우고-다시-만들기, 나머지 트랜잭션-마스터 관계 5개는 매달 새 ID만 생겨서 지울 대상이 없으므로 단순 `MERGE`로 다운그레이드

### **3. `etl/` 폴더를 신설해 `backend/`와 분리**

```
etl/
├── export_to_csv.py
├── load.cypher
├── load_to_neo4j.py        (결정 7 — 파일시스템 접근 불가할 때 쓰는 Bolt 드라이버 기반 적재)
├── demo_reset_month.cypher (특정 월 데이터 삭제 시, 시연 시나리오)
├── requirements.txt        (pandas, openpyxl, neo4j — backend와 별도 관리하되 neo4j는 backend와 버전 통일)
├── data/                   (원본 xlsx, git 추적 제외)
└── import/                 (export 결과, docker-compose의 /import에 마운트, git 추적 제외)
```

ETL은 API 서빙과 무관한 독립 배치 작업이고, pandas/openpyxl 처럼 서빙 서버가 평생 쓸 일 없는 무거운 의존성을 `backend/`에 얹으면 배포 이미지가 불필요하게 커진다. 이미 팀이 `backend/`, `frontend/` 폴더로 담당 영역을 나누는 관행을 갖고 있어서(`0001` 근거 3), 같은 방식으로 `etl/`을 분리하는 게 일관적이다.

### **4. `docker-compose.yml`에 볼륨 2개 추가**

```yaml
neo4j:
  volumes:
    - neo4j_data:/data
    - ./etl/import:/import
    - ./etl:/etl:ro
```

Neo4j 공식 Docker 문서(Mount points 표)에 컨테이너가 인식하는 표준 임포트 경로가 `/import`로 명시돼 있다. 호스트의 `etl/import/`를 그대로 마운트해두면, `export_to_csv.py`가 CSV를 쓰는 즉시 컨테이너 안에서도 보이므로 "CSV를 컨테이너로 복사하는" 별도 단계가 없어진다.

`./etl:/etl:ro`는 별도로 추가했다. `load.cypher`·`demo_reset_month.cypher`는 git으로 관리하는 소스 코드라 `etl/import/`(생성물, git 추적 제외)와는 성격이 다른데, `cypher-shell`을 컨테이너 안에서 실행(`docker compose exec neo4j cypher-shell -f /etl/load.cypher`)하려면 이 스크립트 파일도 컨테이너 안에서 보여야 한다. 이렇게 하면 cypher-shell을 호스트에 별도로 설치할 필요 없이 `neo4j:5-community` 이미지에 기본 포함된 것만 쓰면 되고, ADR `0002`가 이미 전제한 "Docker Desktop만 있으면 된다"는 원칙을 그대로 유지할 수 있다.

### **5. 환경변수는 새로 만들지 않고 루트 `.env`를 재사용**

`.env.example`에 이미 `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_URI`가 정의돼 있고 `docker-compose.yml`·`backend/core/config.py`가 이미 이 값을 쓴다. ETL(정확히는 `cypher-shell` 실행 단계)도 같은 값을 그대로 재사용한다. `0001`이 스키마 파일 하나로 기준을 통일하자고 한 것과 같은 이유로, 접속 정보도 두 곳에서 따로 관리하면 언젠가 어긋난다.

### **6. 원본 xlsx는 git에 커밋하지 않는다**

`AdventureWorks_전체사슬_32시트_한글.xlsx`(약 32MB)는 `etl/data/`에 두되 `.gitignore`로 추적 제외한다. 팀원 각자 로컬에 파일을 받아서 이 경로에 두면 `export_to_csv.py`가 스크립트 기준 상대경로로 찾는다.

### **7. 적재 실행 경로는 "Neo4j 서버 파일시스템 접근 가능 여부"로 선택한다**

`load.cypher`의 `LOAD CSV file:///`는 항상 **Neo4j 서버가 실행 중인 머신 자신의** `/import` 디렉터리만 읽는다. 이 문서를 처음 쓸 때는 "개발환경과 Neo4j 컨테이너를 같은 컴퓨터에서 함께 띄운다"를 암묵적으로 전제했지만, 실제로는 팀이 원격 공유 서버(`.env`의 `NEO4J_URI`)를 운영하면서 그 서버의 파일시스템에는 접근 권한이 없는 경우가 생겼다. 이때는 `LOAD CSV`가 원천적으로 동작할 수 없다(로컬에 CSV를 아무리 준비해도 원격 서버의 `/import`에 넣을 방법이 없음).

이 갈림길의 기준은 **컴퓨터 대수가 아니라, 적재 명령이 실행되는 위치가 Neo4j 서버와 파일시스템을 공유하는가**이다. SSH 등으로 원격 서버에 직접 들어가 그 안에서 명령을 실행하면 파일시스템 접근 관점에서는 "그 컴퓨터에서 직접 작업하는 것"과 동일해지므로, 컴퓨터가 두 대여도 아래 경로 A를 그대로 쓸 수 있다.

| 상황 | 판단 기준 | 사용 스크립트 |
| --- | --- | --- |
| A. 파일시스템 접근 가능 | 개발환경과 Neo4j가 같은 머신(로컬 docker-compose), 또는 원격 머신에 SSH 등으로 직접 들어가 그 안에서 실행 | `load.cypher` (`docker compose exec neo4j cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" -P '{month: "YYYY-MM"}' -f /etl/load.cypher`) |
| B. Bolt 접속만 가능 | 원격 공유 Neo4j 서버에 파일시스템/실행 권한 없이 네트워크(Bolt 포트)로만 접속 | `load_to_neo4j.py` (`python etl/load_to_neo4j.py --month YYYY-MM`) |

`load_to_neo4j.py`는 `load.cypher`의 MERGE/SET 로직·실행 순서(제약조건 → 마스터 노드 → 마스터 관계 → 트랜잭션 노드 → 트랜잭션 관계 → 검증)를 그대로 유지하되, CSV를 로컬에서 `csv.DictReader`로 읽어 1,000행 단위로 `UNWIND $rows AS row ...` 배치를 만들어 Bolt 드라이버(`neo4j` 파이썬 패키지, backend와 동일하게 `6.2.0`)로 전송한다. 파일시스템 접근이 전혀 필요 없으므로 Neo4j가 어디서 실행되든 동작한다. 두 스크립트는 한쪽이 다른 쪽의 대체재가 아니라 상황 A/B 각각에 대응하는 별개의 도구이므로 **둘 다 리포에 유지**한다.

## **검토했으나 채택하지 않은 대안**

**1. row 단위 watermark(마지막 동기화 시각) 기반의 진짜 증분 동기화.**

매달 "바뀐 행만" 골라내는 CDC 방식도 검토했지만, 이 프로젝트는 정적 과거 이력 데이터를 다루고 발표 시연 목적이 "월별로 업데이트 가능한 구조를 보여주는 것"이라, 월 단위 배치로 쪼개는 것만으로 충분하다. 진짜 CDC는 6주짜리 프로젝트에 들일 구현 비용 대비 얻는 게 적어서 기각했다(멘토 조언과도 일치). 단, 발표 시연을 넘어 실제 서비스로 확장돼서 데이터가 실시간으로 계속 쌓이는 상황이 되면 재검토 대상이다("추후 확장 검토 사안" 참고).

**2. ETL 스크립트를 `backend/` 안에 통합.**

백엔드 API 서버와 배포 단위를 같이 쓸 수 있다는 장점은 있지만, `backend/requirements.txt`에 pandas·openpyxl이 없는 기존 관행과 어긋나고, 서빙 서버 이미지에 ETL 전용 무거운 의존성이 섞이게 된다. 기각.

**3. xlsx 원본을 git에 커밋.**

리포 용량이 불필요하게 커지고, `.gitattributes`에 Git LFS 설정도 없는 상태라 바로 커밋하면 이후 히스토리 정리가 번거로워진다. 대신 로컬 배치 + `.gitignore`로 처리한다. 나중에 팀이 LFS 도입을 원하면 재검토 가능하다.

**4. cypher-shell 파라미터를 화살표 문법(`-P "month => '2014-05'"`)으로 전달.**

처음에 이렇게 안내했으나, Neo4j 공식 Cypher Shell 문서를 다시 확인한 결과 명령줄 `-P`/`--param` 플래그는 맵 리터럴 문법(`-P '{month: "2014-05"}'`)이 맞고, 화살표 문법은 대화형 셸의 `:param` 명령 전용이다. 맵 리터럴 문법으로 정정했다.

**5. cypher-shell을 팀원 각자 호스트에 설치해서 `bolt://localhost:7687`로 접속.**

클라이언트가 어디서 실행되든 `LOAD CSV`의 파일 경로 해석은 항상 서버(컨테이너) 쪽 `/import` 기준이라 방식 자체는 문제없다. 다만 `0002`가 이미 "Docker Desktop만 있으면 동일한 환경을 재현한다"는 원칙을 세워둔 상태에서, cypher-shell(Java 21 필요)까지 팀원 4명 각자 설치하게 만드는 건 새로운 설치 의존성을 추가하는 것이다. 대신 `./etl:/etl:ro` 마운트로 컨테이너 안에 이미 포함된 cypher-shell을 쓰는 쪽을 택했다.

**6. `apoc.meta.data()`로 Neo4j 그래프에서 `graph_schema.yaml`용 스키마 정보를 자동 추출.**

`docker-compose.yml`의 APOC 플러그인 설정(`NEO4J_PLUGINS`, `NEO4J_dbms_security_procedures_unrestricted`)이 있어서, 다음 작업자가 Neo4j에 적재된 데이터에서 LLM 프롬프트로 넘겨줄 스키마 정보를 추출할 때 `CALL apoc.meta.data()`로 라벨·관계타입·속성·타입 정보를 뽑아 `graph_schema.yaml` 등에 반영하는 방법을 검토했다. 실제로 원격 공유 서버(`bolt://kosa165.iptime.org:52003`)에 대해 `CALL apoc.meta.data()`를 실행해봤고(2026-08-12, 임시로 작성한 점검용 스크립트로 확인, 이후 삭제함), 노드 라벨 11개·관계 타입 13개가 `graph_schema.yaml`의 구성과 정확히 일치함을 확인했다. 그런데 다음 두 가지 한계가 드러났다: (1) apoc이 관계 타입도 노드의 "pseudo-property"로 같이 반환해서(예: `Product.STOCKED_AT: RELATIONSHIP`) 순수 속성 목록과 구분하는 필터링이 추가로 필요하고, (2) 타입 표기가 `graph_schema.yaml`과 다르다(apoc은 `LOCAL_DATE_TIME`, `graph_schema.yaml`은 `LOCAL_DATETIME`) — 그대로 매핑하면 안 되고 변환 테이블이 필요하다. 또한 근본적으로 apoc.meta.data()는 그래프에 실제로 들어있는 값을 보고 타입/존재 여부를 추론하는 방식이라, nullable 속성이 이번 배치에 마침 전부 비어 있으면 "속성이 없다"고 잘못 판단할 위험이 있다.

이후 `etl/data/ITDA-neo4j2`(arrows.app 원본 다이어그램 export, JSON)를 직접 확인해보니, 라벨·`sourceSheet`(caption)·속성명·타입·nullable·자연키 후보(`KEY` 태그)·관계 구조가 이미 정확한 형태로 다 들어있었다. 이건 실제 그래프 데이터를 샘플링해서 추론하는 게 아니라 diagram에 명시된 값을 그대로 읽는 것이라 위에서 발견한 두 한계가 없다. 그래서 apoc.meta.data() 기반 방식은 arrows.app 파일을 직접 파싱하는 방식보다 못하다고 보고 있다. 다만 이건 이번 논의에서 나온 방향 제안이고, 팀이 실제로 arrows.app 파싱 방식을 채택하기로 확정한 건 아니다.

## **결과 및 트레이드오프**

- 관계 적재가 전부 `MERGE` 기반(그룹 A/B) 또는 지우고-다시-만들기(그룹 C 중 마스터 2개)라, 같은 달을 실수로 두 번 적재해도 중복이 생기지 않는다.
- `etl/`이 `backend/`와 완전히 분리돼 있어, 서빙 서버 배포에 ETL 의존성이 섞이지 않는다.
- 반면 트랜잭션 적재는 매번 `$month` 파라미터를 사람이 직접 지정해야 한다(완전 자동화는 아직 아님). 매달 실행을 스케줄러로 자동화하려면 추후 별도 작업이 필요하다.
- 원본 xlsx가 git에 없으므로, 새 팀원이 리포를 받으면 이 파일을 별도로 구해서 `etl/data/`에 두는 안내가 필요하다(README 보완 필요).
- **실행 검증(export)**: `python etl/export_to_csv.py master` → Product 504건, Supplier 104건, ProductCategory 4건, ProductSubcategory 37건, Location 14건, ScrapReason 16건, SUPPLIES 443건(비활성 공급업체 4곳 제외 확인), REQUIRES_COMPONENT 2,576건, STOCKED_AT 1,069건. `python etl/export_to_csv.py tx --month 2014-05` → PurchaseOrder 349건, PurchaseOrderLine 734건, SalesOrder 2,411건, WorkOrder 3,421건, RoutingOperation 3,436건. `load.cypher`의 `LOAD CSV` 24개 블록이 참조하는 `row.*` 컬럼명을 생성된 CSV 헤더와 전부 대조해 불일치 없음을 확인했다.
- **실행 검증(실제 적재)**: 위 CSV를 결정 7의 경로 B(`load_to_neo4j.py`)로 원격 공유 Neo4j(`bolt://kosa165.iptime.org:52003`, 당시 노드 0건인 빈 상태)에 적재. 원격 공유 서버(`.env`의 `NEO4J_URI`)는 파일시스템 접근이 불가능해 경로 A(`LOAD CSV file:///`)가 애초에 성립하지 않아 경로 B를 썼다. 적재 후 라벨/관계 타입별 카운트가 export 단계 건수와 정확히 일치함을 확인했다(마스터 노드/관계, 2014-05 트랜잭션 노드/관계 전 종류 포함). 경로 A(`load.cypher` + `cypher-shell`)는 로컬 docker-compose Neo4j나 원격 호스트 SSH 접속 환경에서는 여전히 유효하지만, 이번 세션은 로컬 Docker Desktop 데몬이 꺼져 있어 검증하지 못했다.

## **확실하지 않은 부분**

- `schema/graph_schema.yaml`의 **파일 내용 자체는 이미 채워졌다**(수작업, 커밋 `8a743b5` "feat: graph_schema.yaml에 확정 스키마 반영" — `load.cypher`·`export_to_csv.py`·`0004` ADR을 기준으로 사람이 직접 옮겨 적음). 이 값을 그래프에서 자동으로 다시 뽑아내는 절차는 아직 없다 — 지금은 스키마가 바뀌면 사람이 다시 옮겨 적어야 한다. 자동화 후보로 APOC `apoc.meta.data()` 기반 추출과 arrows.app 원본 다이어그램(`etl/data/ITDA-neo4j2`) JSON 파싱을 검토했다("검토했으나 채택하지 않은 대안" 6번 참고) — 다만 둘 다 아직 실제로 구현되지는 않았다.

## **추후 확장 검토 사안**

이 ADR의 결정들은 **"정적 과거 이력 데이터 + 발표 시연"**이라는 지금 프로젝트 범위에 맞춰져 있다.

아래 항목들은 지금 당장 문제가 되지는 않지만, 프로젝트가 이 범위를 넘어설 때(실제 운영 데이터 연동, 팀 인프라 변경, 실시간 데이터 유입 등) 재검토가 필요하다.

- 월 기준 컬럼(예: `WorkOrder.startDate`)이 진행 중인 항목의 상태 변경(예: 시작은 이번 달, 종료는 다음 달)을 반영하지 못하는 문제와 `RoutingOperationKey`(합성키)가 공정 순서 재조정 시 안정성이 깨질 수 있는 문제는, 지금 다루는 정적 과거 이력 데이터에는 해당하지 않지만 실제 운영 DB에 연결하는 시점에는 재검토가 필요하다.
- **향후 Neo4j Community -> Enterprise 전환 시 재검토 필요**:

    현재 `docker-compose.yml`은 `neo4j:5-community` 이미지를 쓰고, 이 ADR의 모든 결정(APOC은 플러그인 목록 추가만으로 동작, `NEO4J_ACCEPT_LICENSE_AGREEMENT` 불필요 등)은 Community 기준이다. 팀이 이후 본작업 단계에서 Neo4j를 Enterprise로 바꾸면 이미지 태그(`neo4j:5-enterprise`), 라이선스 동의 환경변수(`NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`), Enterprise 전용 기능(예: 역할 기반 접근 제어, 클러스터링) 사용 여부를 다시 검토해야 한다. 지금은 이 사실만 남겨두고, 실제 `docker-compose.yml`/`.env`/이 ADR의 변경은 전환 시점에 별도로 진행한다.

- **월 단위 배치(결정 1) 재검토 시 함께 볼 것.**

    "검토했으나 채택하지 않은 대안" 1번의 재검토 조건이 여기 그대로 적용된다. 그 시점에는 최소 두 가지를 함께 고려해야 한다.

    1. **마스터 관계(그룹 A)가 원본에서 사라진 관계를 지우지 못하는 문제.**

        `SUPPLIES`(`REQUIRES_COMPONENT`, `STOCKED_AT`도 동일 구조)는 결정 2의 그룹 A라 `MERGE`+`SET`만 한다 — 새로 들어온 자연키(`supplyKey` 등)는 추가/갱신되지만, 이전엔 있었는데 이번 CSV에서 빠진 자연키는 그래프에 그대로 남는다. 예: 공급업체가 활성 → 비활성으로 바뀌면 `export_master()`가 팀 결정 4(비활성 공급업체 제외)에 따라 그 행을 CSV에서 걸러내고, 재적재하면 `Supplier.active` 노드 속성은 `SET`으로 정상 `false`가 되지만 기존 `SUPPLIES` 관계는 지워지지 않아 노드-관계 상태가 불일치하게 된다(반대 방향인 비활성 → 활성은 `MERGE`가 새로 만들어주므로 문제없다). watermark/CDC 방식은 보통 삭제·비활성화까지 이벤트로 포착하도록 설계되는 경우가 많아, 이 문제를 해결하는 작업과 자연스럽게 같이 갈 수 있다.

    2. **수동 실행 문제("결과 및 트레이드오프"에 이미 기록된 `--month` 수동 지정)까지 함께 해결.**

        실시간/주기적으로 새로 들어오는 데이터를 감지해서 자동으로 Neo4j에 적재하는 파이프라인(예: 스케줄러 + row-level watermark, 또는 이벤트/스트리밍 기반)으로 바꾸는 것까지 검토해야 한다.

    지금은 결정하지 않고, 프로젝트 확장이 실제로 논의되는 시점에 별도 ADR로 다룬다.
