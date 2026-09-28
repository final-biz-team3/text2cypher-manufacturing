# 0015. 다층 쿼리 가드와 읽기 전용 실행 경계

## 상태

확정 (2026-09-14)

## 한 줄 요약

> LLM이 만든 SQL/Cypher는 사용자 요청 가드, 실행 직전 문법·스키마 가드, DB 읽기 트랜잭션/reader 계정, 시간·행 제한의 여러 경계를 모두 통과해야 실행된다. 한 계층의 실수에 다른 계층이 의존하지 않도록 fail-closed와 최소 권한을 함께 적용한다.

---

## 배경 — 왜 이 결정이 필요했나

이 서비스는 사용자 자연어에서 SQL과 Cypher를 생성해 실제 데이터베이스에 실행한다. 프롬프트의 “읽기 전용” 지시만으로는 `DELETE`, `CREATE`, procedure 호출, 파일 접근 함수, 미허가 테이블/라벨, 다중 문장, 과도한 결과를 막을 수 없다.

또한 코드 가드만 믿으면 파서 누락이나 미래 변경의 영향을 모두 떠안게 된다. 반대로 DB 권한만 믿으면 잘못된 쿼리를 생성·전송하고 재시도하는 비용과 스키마 노출을 막지 못한다. PostgreSQL과 Neo4j의 권한·트랜잭션 모델이 다르므로 각 DB에 맞는 강제 장치도 필요하다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. 사용자 요청 단계에서 명시적 변경 의도를 차단한다

`backend/orchestrator/nodes/guard_request.py`는 Orchestrator의 첫 노드다. 한국어·영어 명령형 변경 의도와 SQL 형태의 쓰기 구문을 감지하면 `REQUEST_POLICY_BLOCKED` 안전 실패를 만들고, 이후 분류·엔티티 해석·생성 단계로 보내지 않는다. 판정은 `query_guard_audit.jsonl`에 별도 기록한다.

이 계층은 자연어 의도에 대한 조기 차단이며, 뒤의 쿼리 가드를 대체하지 않는다. “삭제 건수를 보여줘” 같은 조회 문장을 과도하게 막지 않도록 실제 명령형·구문 중심의 휴리스틱으로 한정한다.

### 2. SQL은 파싱 기반으로 쓰기·위험 함수·미허가 테이블을 차단한다

`backend/orchestrator/guards/sql_guard.py`는 `sqlparse`로 하나의 문장만 허용하고 다음을 확인한다.

- `SELECT` 또는 읽기 전용 `WITH` 형태인지
- DDL/DML 및 관리 키워드가 없는지
- advisory lock, sleep, 서버 파일 접근, backend 제어, large object, dblink 등 위험 함수 호출이 없는지
- `schema.table`이 SQL schema YAML의 허용 목록에 있는지
- CTE, 재귀 CTE, JOIN, 중첩 SELECT, `unnest` 같은 명시 허용 source를 해석할 수 있는지

테이블 참조나 구문을 확실히 해석할 수 없으면 통과시키지 않는다. 문자열 리터럴·식별자·주석 안의 키워드를 오탐하지 않도록 공유 마스킹/토큰 처리를 사용한다.

### 3. Cypher는 쓰기 절과 Label/RelationshipType 화이트리스트를 함께 검사한다

`backend/orchestrator/guards/cypher_guard.py`는 `CREATE`, `MERGE`, `DELETE`, `SET`, `LOAD`, `CALL` 등을 금지하고 `graph_schema.yaml`에 정의된 노드 Label과 관계 Type만 허용한다. Cypher 전용 성숙 파서가 없는 범위는 문자 단위 스캔으로 처리한다.

맵 리터럴의 `:`와 Label/Type의 `:`를 구분하고, 문자열·주석·백틱 식별자, label expression, `EXISTS`/`COUNT`/`COLLECT` 서브쿼리 표현식을 고려한다. 지원하지 않는 label expression이나 닫히지 않은 식별자처럼 해석 신뢰도가 부족한 경우는 `UNRECOGNIZED_LABEL_SYNTAX`로 fail-closed 한다.

### 4. 실행 직전 가드 실패는 실행하지 않고 안전하게 기록한다

Specialist의 공통 재시도 루프는 executor 호출 전에 가드와 쿼리 계약을 수행한다. 차단된 쿼리는 DB에 전달되지 않으며, 가드의 상세 사유는 내부 로그에만 남긴다. 외부에는 정책 차단 코드와 일반화된 안내만 반환해 실제 테이블·라벨명을 반복 시도에서 유추할 수 없게 한다.

공통 금지 키워드는 `orchestrator/guards/shared.py`에 두며, Cypher 가드와 평가 하네스는 같은 텍스트 마스킹도 사용한다. SQL 가드는 `sqlparse` 토큰을 기준으로 검사한다. 이로써 금지 키워드 목록이 별도로 드리프트하지 않게 한다.

### 5. PostgreSQL은 읽기 전용 풀과 rollback으로 강제한다

`backend/core/postgres.py`의 조회 풀은 새 연결마다 `set_read_only(True)`와 statement timeout을 설정한다. 신뢰된 앱 쓰기(대화 기록 등)는 별도 write pool만 사용하므로, LLM 실행 경로의 풀 설정을 완화할 이유가 없다.

`execute_sql_with_pool()`은 `fetchmany(row_limit + 1)`로 행 상한을 확인하고, 성공·실패와 무관하게 `rollback()`한다. 따라서 생성 쿼리가 가드를 우회하더라도 조회 풀의 DB 트랜잭션 경계가 추가 방어선이 된다.

### 6. Neo4j는 reader 계정과 읽기 트랜잭션을 함께 사용한다

`execute_cypher()`는 관리자 드라이버와 별도인 `NEO4J_READER_USER`/`NEO4J_READER_PASSWORD` 드라이버를 사용하고, 항상 `session.execute_read()` 안에서 실행한다. 기동 시 `verify_connectivity()`와 `verify_reader_is_read_only()`로 reader 계정이 실제 쓰기를 거절하는지 확인한다.

실행 함수는 `neo4j.unit_of_work(timeout=...)`로 트랜잭션 timeout을 걸고 `fetch(row_limit + 1)`로 결과를 자른다. reader 계정 권한, read transaction, 실행 전 Cypher 가드는 동일한 위험을 서로 다른 층에서 막는 중복 방어다.

### 7. 감사 기록은 실행 흐름과 분리한다

가드의 ALLOW/BLOCK 결정은 `orchestrator/guards/audit.py`의 JSON Lines 전용 logger에 기록한다. 동기 파일 I/O는 threadpool로 옮기고, 감사 로그 실패가 가드 판정이나 DB 실행 흐름을 바꾸지 않게 한다. 기본은 파일 전용이며 환경 설정으로 콘솔 전파를 켤 수 있다.

## 검토했으나 채택하지 않은 대안

**프롬프트 지시만으로 읽기 전용 보장.** LLM 출력은 신뢰 경계 밖에 있으므로, 지시 누락·모델 오류·프롬프트 주입에 취약하다. 실행 전 코드 검사와 DB 권한을 반드시 둔다.

**DB 권한만으로 방어.** 권한은 최후 방어로 중요하지만, 미허가 스키마 참조·위험 함수·대량 결과를 일찍 설명하고 재생성할 수 없으며, 잘못된 쿼리가 DB까지 도달한다. 코드 가드를 함께 둔다.

**정규식만으로 SQL/Cypher를 파싱.** 주석·문자열·CTE·중첩 서브쿼리·백틱·맵/서브쿼리 표현식에서 우회와 오탐이 누적된다. SQL은 토큰 파서를 사용하고, Cypher는 지원 범위를 명시한 스캐너와 해석 불가 시 차단 원칙을 택했다.

**관리자 Neo4j 계정을 모든 읽기 실행에 사용.** `execute_read()`만으로도 현재 코드의 쓰기를 막지만, 향후 호출 방식 변경이나 설정 실수의 피해 범위가 커진다. 실행 전용 reader 계정을 분리하고 시작 시 역할 검증을 추가했다.

## 결과 및 트레이드오프

- 사용자 요청부터 DB 권한까지 연속된 방어선이 생겨 한 계층의 누락이 곧 데이터 변경으로 이어지지 않는다.
- 스키마 화이트리스트와 결과/시간/행 제한으로 LLM 실행 범위가 줄어든다.
- 가드 차단은 DB 실행 전에 재시도 피드백으로 활용할 수 있고, 내부 상세 정보는 외부에 노출하지 않는다.
- 트레이드오프: 보수적인 fail-closed 정책은 아직 지원하지 않는 합법 SQL/Cypher 표현도 차단할 수 있다. 새 표현을 허용할 때는 우회 사례와 정상 사례를 함께 테스트한 뒤 파서를 확장해야 한다.
- TODO: reader 권한 검증 프로브는 잘못된 관리자 설정을 검출하는 안전 점검이므로, 검증용 식별자가 기존 그래프 데이터를 절대 건드리지 않는지 계속 테스트로 보장한다. 평가용 동기 Neo4j 실행기도 reader 자격증명 사용 여부를 운영 경로와 주기적으로 대조한다.
