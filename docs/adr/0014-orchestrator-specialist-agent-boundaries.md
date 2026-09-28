# 0014. Orchestrator와 SQL/Cypher Specialist Agent의 책임 경계

## 상태

확정 (2026-09-14)

## 한 줄 요약

> 자연어 질의의 정책 판정·이해·계획·결과 조합은 Orchestrator가 맡고, 실제 SQL/Cypher 생성·사전 검증·읽기 실행·자기수정은 각각의 Specialist Agent가 맡는다. 혼합 질의는 의존성 DAG의 wave 단위로 실행해 선행 결과를 후속 Agent의 입력으로 전달한다.

---

## 배경 — 왜 이 결정이 필요했나

이 서비스는 PostgreSQL의 수치·집계 사실과 Neo4j의 관계 탐색 read model을 함께 사용한다. 하나의 범용 Agent가 라우팅, 스키마 해석, SQL/Cypher 생성, 여러 결과의 join, 답변 생성까지 모두 맡으면 프롬프트가 비대해지고, 어떤 단계의 오류인지 구분하기 어려우며, DB별 안전 제약도 흐려진다.

반대로 SQL/Cypher마다 완전히 독립된 최상위 흐름을 만들면 사용자 질의의 엔티티 확인, 하위 질의 의존성, 결과 조합, 사용자 안전 실패 계약을 중복하게 된다. 따라서 공용 작업 흐름과 DB 특화 실행 책임을 분리할 필요가 있었다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. 단일 Orchestrator StateGraph가 요청 수명주기를 소유한다

`backend/orchestrator/graph.py`는 시작 시 SQL/그래프 YAML 스키마와 출력 catalog를 읽고, 다음 실행 그래프를 컴파일한다.

```text
START
  → guard_request
  → classify_topic
  → strict_query
  ├─ strict 성공 → generate_answer → END
  └─ 일반 경로 → resolve_entity → route_query → plan_outputs
                → execute_plan → compose_results → generate_answer → END
```

`OrchestratorState`는 원 질문, 엔티티, route draft, 하위 질의 DAG, SQL/그래프 결과, 안전한 `query_failure`, 조합 결과, 최종 답변을 보관한다. 이 상태를 경계로 삼아 각 노드는 필요한 입력과 결과만 갱신한다.

### 2. Orchestrator는 “무엇을 조회하고 어떻게 합칠지”를 결정한다

- `guard_request`: 데이터 변경 의도가 드러난 사용자 요청을 초기에 차단한다.
- `classify_topic`, `strict_query`, `resolve_entity`, `route_query`, `plan_outputs`: 질의 범위, 엔티티, SQL/GRAPH 선택, 필요한 출력 alias, 의존성 및 join 규칙을 정한다.
- `execute_plan`: 검증된 `subqueries`를 실행한다.
- `compose_results`: SQL/그래프 결과를 계획의 join 계약과 transform에 따라 합친다.
- `generate_answer`: 조합 결과 또는 안전한 실패 상태를 사용자 답변으로 렌더링한다.

Orchestrator는 DB 문법을 직접 생성하거나 신뢰되지 않은 쿼리를 직접 실행하지 않는다.

### 3. Specialist Agent는 “한 데이터 소스에 대한 안전한 조회”를 소유한다

`make_sql_agent_subgraph()`와 `make_cypher_agent_subgraph()`는 각자의 스키마, 의미 catalog, 생성기, executor, 가드를 주입받는다. Specialist가 수행하는 범위는 다음과 같다.

1. 원 질문·하위 질의 범위·엔티티·입력 binding·필수 출력·업무 규칙을 프롬프트에 넣어 쿼리 생성
2. DB별 쿼리 가드와 결과 계약 검증
3. 읽기 전용 executor 호출
4. 오류 또는 결과 불변식 위반 시 ADR 0013의 제한된 자기수정

Cypher Specialist에는 가변 경로 관계 list 오용, 독립 BOM 경로 결합, `depth`·경로 배열 길이 같은 그래프 특화 계약을 추가한다. SQL Specialist는 PostgreSQL 예외와 SQL 스키마 가드를 사용한다.

### 4. 혼합 질의는 의존성 DAG의 wave 단위로 실행한다

`execute_plan`은 `dependsOn`이 모두 완료된 하위 질의를 한 wave로 묶어 병렬 실행한다. 선행 결과가 필요한 후속 질의에는 `collect_input_bindings()`로 값 목록을 전달한다. 선행 질의가 실패하면 후속 질의는 실행하지 않고 `DEPENDENCY_FAILED`를, 선행 결과가 비어 있으면 `NO_DATA` 또는 `INCONCLUSIVE`를 보존한 채 건너뛴다.

이 방식은 “그래프에서 대상 식별 → SQL에서 수치 조회” 같은 순차 질의에는 올바른 데이터 의존성을 보장하면서, 서로 독립인 SQL/GRAPH 하위 질의는 불필요하게 직렬화하지 않는다.

### 5. 실패는 가장 심각한 원인을 보존한다

계획 안에 여러 하위 질의가 있을 때 `execute_plan`은 인프라 > 내부 > 사용자 수정 가능 순으로 실제 실패를 선택하고, 의존성 때문에 생긴 후속 실패는 원인보다 낮은 우선순위로 둔다. 사용자는 선행 실패의 안전한 이유를 보며, 내부적으로는 각 subquery의 시도 기록과 진단을 유지한다.

## 검토했으나 채택하지 않은 대안

**하나의 범용 Agent가 모든 DB와 결과 조합을 처리.** 구현 파일 수는 줄지만 SQL/Cypher 문법·스키마·권한·계약을 하나의 프롬프트와 오류 흐름에 섞게 된다. DB별 가드와 자기수정 피드백을 정밀하게 유지하기 어려워 기각했다.

**고정된 사전 작성 쿼리 템플릿만 실행.** 재현성과 안전성은 높지만, 제조 데이터 탐색 질문의 조합과 조건 변형을 충분히 수용하기 어렵다. 현재는 LLM 생성 쿼리를 허용하되 스키마·계약·가드·DB 권한으로 실행 범위를 제한한다.

**모든 하위 질의를 직렬 실행.** 의존성 구현은 단순하지만 독립 SQL/GRAPH 조회까지 대기하게 된다. DAG 검증 후 ready wave를 병렬 실행하는 방식이 현재 요구에 더 맞는다.

**모든 하위 질의를 무조건 병렬 실행.** 선행 조회의 식별자나 집계값을 후속 입력으로 쓰는 혼합 질의의 정확성을 보장할 수 없어 기각했다.

## 결과 및 트레이드오프

- 데이터 소스별 전문성, 가드, 읽기 executor, 오류 분류를 독립적으로 발전시킬 수 있다.
- Orchestrator가 질문 정책·계획·조합을 단일 상태로 관리해 API 응답 계약이 일관된다.
- 의존성 없는 하위 질의를 병렬화할 수 있다.
- 트레이드오프: StateGraph와 `subqueries` 계약의 구조가 단순한 단일 함수보다 복잡하다. 새 도구를 추가할 때는 tool 이름, 스키마, 가드, executor, 결과 조합, 관측 지표를 모두 연결해야 한다.
- TODO: 새 데이터 소스를 추가하기 전에는 `Subquery` 계약과 `compose_results`가 해당 도구의 결과 grain 및 join key를 충분히 표현하는지 먼저 검토한다.
