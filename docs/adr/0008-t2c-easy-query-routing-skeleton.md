# 0008. T2C 쉬운 질의 5개 라우팅 뼈대 — resolve_entity·route_query 범위 확정

## 상태

확정 (2026-08-20)

## 한 줄 요약

> `feat/t2c-easy-query-poc` 브랜치에, 자연어 질의 5개(RQ01~04 SQL / RQ12 GRAPH)가 `resolve_entity`(엔티티 확정) → `route_query`(SQL/GRAPH/HYBRID 분기)까지 올바르게 통과하는 LangGraph 2노드 서브그래프를 구성한다. 실제 SQL/Cypher 생성·실행·self-correction·응답 생성은 이번 범위에서 제외한다.

---

## 배경 — 왜 이 결정이 필요했나

이 프로젝트는 자연어 질의를 PostgreSQL(AdventureWorks, 수치·집계 기준 저장소)과 Neo4j(관계 탐색 read model)로 라우팅해 답하는 제조 데이터 챗봇이다. 팀이 이미 작성한 전체 아키텍처 설계서(Orchestrator Agent + SQL/Cypher Specialist Agent, 각자 독립된 self-correction 루프를 가진 LangGraph 멀티에이전트 구조)가 있는데, 이걸 한 세션에 전부 구현하기엔 범위가 너무 크다(Specialist Agent 내부의 생성·검증·실행·재시도 루프, 결과 통합, 보안 가드, API 계약, 평가 체계까지 포함). 그래서 어디까지를 "이번 뼈대"로 볼지부터 정해야 했다.

### 관련 브랜치 정리

- `dev`: 현재 그래프 스키마는 arrows.app 다이어그램 기반 구(舊) 버전. Postgres 연동 없음.
- `feat/t2c-easy-query-poc` (작업 브랜치, `dev`에서 분기): OpenAI/Postgres 연동, `.env` 설정 방식 통일까지만 되어 있고 T2C 파이프라인 자체는 없음.
- `pr-16` (`dev`에서 분기, 미병합): "구조화 MVP" — PostgreSQL 원본 기준 그래프 스키마, ETL, 그리고 **20개 질의 계약**(`queries/query_contracts.json`, `queries/query_parameters.json`)을 정의했다. 단 이 계약의 `routingPolicy` 문구("검증된 파라미터 SQL/Cypher 템플릿만 실행")는 이번에 확인한 팀의 최신 설계서(Orchestrator+Specialist Agent, LLM이 직접 SQL/Cypher를 생성하고 self-correction하는 구조)와는 결이 다르다 — `query_contracts.json`은 질의 계약(질문 패턴, 파라미터, 정답 판정 기준)의 출처로만 쓰고, 실행 방식은 최신 설계서를 따른다.

이번 세션은 `pr-16`의 `schema/structured_mvp_graph_schema.yaml`, `schema/structured_mvp_constraints.cypher`, `queries/query_contracts.json`, `queries/query_parameters.json`을 `feat/t2c-easy-query-poc`로 가져와 그 위에 이어서 작업한다.

### 대상 질의 5개

`query_contracts.json`의 `LOW_QUESTION_LOW_SCHEMA`(SQL, 7개 중 4개) + `LOW_QUESTION_HIGH_SCHEMA`(GRAPH, 6개 중 1개)에서 선택했다.

| ID | 라우트 | 질문 템플릿 | 파라미터 |
|---|---|---|---|
| RQ01 | SQL | `[제품명]`의 정가와 표준원가를 알려줘. | productName (EXACT) |
| RQ02 | SQL | `[제품명]`의 재고 위치와 위치별 수량을 알려줘. | productName (EXACT) |
| RQ03 | SQL | 현재 활성 상태인 공급업체 수를 알려줘. | 없음 |
| RQ04 | SQL | 외부에서 구매하는 부품 수를 알려줘. | 없음 |
| RQ12 | GRAPH | 부품 `[부품명]`을 사용하는 완제품을 최대 4단계까지 알려줘. | componentName (EXACT), bomAsOfDate, maxDepth |

## 결정 — 무엇을 어떻게 하기로 했나

### 1. 파이프라인 경계 ("분기까지만")

```
사용자 질의 → resolve_entity(엔티티 확정) → route_query(SQL/GRAPH/HYBRID 분기 결정)
                                                        │
                                          [이번 범위의 끝 — 여기서 END]
                                                        │
                              (다음 세션) run_agents → generate_answer
```

"5개 질의 통과"는 이번 범위에서 **각 질의가 올바른 `entity`로 확정되고 올바른 `tool_plan`으로 분기되는 것**을 의미한다. 실제 PostgreSQL/Neo4j 조회 결과가 정답과 일치하는지는 다음 세션(Specialist Agent 구현)에서 검증한다.

### 2. 아키텍처

LangGraph `StateGraph` 2노드 서브그래프:

```
START → resolve_entity → route_query → END
```

`OrchestratorState` (전체 설계서 4-2-1 그대로 선언, 이번에 안 쓰는 필드도 다음 세션이 이어받도록 타입만 미리 선언):

```python
class OrchestratorState(TypedDict):
    query: str
    entity: dict | None
    tool_plan: list[str]
    sql_result: dict | None    # 이번 범위 미사용
    graph_result: dict | None  # 이번 범위 미사용
    final_answer: str | None   # 이번 범위 미사용
    error: str | None
```

이번 세션은 HTTP 엔드포인트(`/chat`)를 만들지 않는다. `backend/orchestrator/graph.py`가 컴파일된 그래프를 노출하고, 테스트와 향후 `/chat` 구현이 이를 직접 호출한다. `/chat`은 `generate_answer`까지 나온 다음 세션에서 응답 계약에 맞춰 추가하는 것이 자연스럽다.

### 3. `resolve_entity` 노드

- 입력: 자연어 질의 원문
- LLM 함수 호출로 질의에서 제품명 문자열을 추출한다 (RQ03/RQ04처럼 대상 엔티티가 없는 질의는 추출 결과 없음 → 그대로 통과)
- 추출된 이름을 PostgreSQL `production.product.name`에 **정확 일치(EXACT)**로 조회한다 (대상 5개의 `query_parameters.json` fixture가 전부 EXACT 매칭이라, 유사 이름 매칭/오타 후보 제시는 이번 범위에서 제외 — `EntityNotFoundError`만 처리한다)
- 출력: `entity: {"productId": int, "productName": str} | None`

### 4. `route_query` 노드

- 전체 설계서 5-0 그대로: OpenAI에 few-shot 프롬프트(SQL/GRAPH 사용 조건 + 예시 3개)를 보내 `["sql"]` / `["graph"]` / `["sql","graph"]` 중 하나를 JSON 배열로 받는다
- 이번 5개는 전부 단일 툴이라 `["sql","graph"]`(HYBRID) 분기는 프롬프트에는 존재하되 5개 테스트 케이스에서는 나오지 않는다

### 5. 에러 처리

전체 설계서 7-2의 `AppError` 계층을 도입하되, 이번 범위에서 실제로 발생 가능한 것만 사용한다.

- `AppError` (공통 베이스: status_code, code, message)
- `EntityNotFoundError` (404) — 이번 범위에서 실제로 raise됨
- `EntityAmbiguousError`, `RetryExceededError` 등 나머지는 클래스만 정의해두고, 실제 사용(유사매칭, self-correction)은 다음 세션부터

### 6. 테스트

`backend/tests/orchestrator/`에 5개 질의 각각에 대해 `resolve_entity` → `route_query` 실행 후 `entity`와 `tool_plan`을 assert하는 통합 테스트를 작성한다. PostgreSQL은 팀 DB(이미 AdventureWorks 데이터 적재됨)를 대상으로 한다.

## 검토했으나 채택하지 않은 대안

**`run_agents`(Specialist Agent 실행)까지 이번에 포함.** 5개 질의가 실제로 PostgreSQL/Neo4j 조회 결과까지 반환하는 걸 "통과"로 보는 안. 실제로 답을 낼 수 있다는 장점이 있지만, SQL/Cypher 생성·검증(sqlparse/CyVer)·실행·self-correction(iteration_count 기반 재시도)까지 한 번에 구현하면 범위가 한 세션을 넘는다. 라우팅 판단(엔티티 확정 + 분기)과 실제 쿼리 실행은 설계서에서도 이미 Orchestrator/Specialist로 책임이 분리돼 있어, 이번엔 Orchestrator 앞부분만 떼어 먼저 검증하는 쪽을 택했다.

**SQL 3개+GRAPH 2개로 구성.** GRAPH 쪽 비중을 늘려 BOM 트래버설 패턴을 이번에 더 확인해보는 안. 이번 범위가 라우팅 판단 자체(엔티티 확정 + 분기)이지 그래프 트래버설 로직 검증이 아니라서, RQ12 하나로도 GRAPH 분기 경로는 충분히 확인된다고 보고 기각했다. GRAPH 질의를 더 늘리는 건 Specialist Agent 구현 세션에서 재검토할 수 있다.

**`resolve_entity`에 유사 이름 매칭(오타 후보 제시)까지 포함.** 설계서 5-0은 원래 오타 시 사용자 확인을 거치는 흐름을 포함한다. 대상 5개 질의의 `query_parameters.json` fixture가 전부 정확 일치(EXACT)라 이번 범위에서는 실익이 없고, `EntityAmbiguousError` 등 관련 로직을 더하면 범위가 커져서 정확 일치만 먼저 검증하기로 했다. 유사 매칭이 필요한 질의(RQ08 이후)를 다룰 때 재검토한다.

**`query_contracts.json`의 `routingPolicy`(고정 템플릿 실행) 방식 채택.** 초기엔 LLM이 route+queryId+파라미터만 추출하고 실행은 사전 작성된 파라미터화 쿼리를 쓰는 안으로 가려 했다. 이후 팀의 최신 설계서(Orchestrator+Specialist Agent, LLM이 SQL/Cypher를 직접 생성하고 self-correction하는 구조)를 확인하고 이 안을 기각했다 — `query_contracts.json`은 질의 계약(질문 패턴·파라미터·정답 판정)의 출처로만 남기고, 실행 방식은 최신 설계서를 따르기로 했다.

## 결과 및 트레이드오프

- 다음 세션(Specialist Agent 구현)이 이어받을 `OrchestratorState`, 파일 구조, 에러 계층을 이번에 미리 잡아두므로 이어붙이기 쉽다.
- 반면 이번 산출물만으로는 실제 질의에 답할 수 없다(HTTP 엔드포인트 없음, DB 조회 결과 미사용) — 순수하게 "질의 이해(엔티티 확정 + 라우팅)"만 검증하는 중간 산출물이다.

## 확실하지 않은 부분

- `pr-16`의 `query_contracts.json` routingPolicy 문구(템플릿 고정 실행)와 최신 설계서(LLM 자유 생성 + self-correction)가 서로 다른 시점의 설계로 보인다 — 이 문서는 최신 설계서를 따랐지만, 두 문서 중 어느 쪽이 팀의 최종 확정안인지는 재확인이 필요하다.
- `resolve_entity`의 제품명 추출을 LLM 함수 호출로 할지, 아니면 이번 5개 질의 패턴이 단순해 정규식/문자열 매칭으로 충분한지는 구현 단계에서 실측 후 결정한다.
