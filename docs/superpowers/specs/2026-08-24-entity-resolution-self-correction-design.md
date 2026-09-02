# Entity Resolution 일반화 + Self-Correction 뼈대 설계

## 상태

브레인스토밍 확정 (2026-08-24), git 커밋은 사용자 최종 승인 후 진행

## 배경

현재 dev 브랜치는 `START → resolve_entity → route_query → generate_queries → END`로 이어지는 파이프라인을 갖고 있다([backend/orchestrator/graph.py](../../../backend/orchestrator/graph.py)). 이 설계는 세 가지 문제를 다룬다.

1. **엔티티 해석이 product 하나로 고정돼 있고 정확 일치만 지원한다.** 실제 사용자(공장장/생산관리자, 데이터 전문가가 아님)는 제품명·업체명을 정확히 모르고 두루뭉술하게 묻는다.
2. **Self-correction이 전혀 없다.** `generate_queries`가 SQL/Cypher 문자열을 생성만 하고 끝나며, 실행·검증·재시도 코드가 아예 없다. `backend/orchestrator/errors.py`의 `RetryExceededError`는 정의만 돼 있고 어디서도 쓰이지 않는다.
3. **세션/이력이 없다.** `/chat`은 매 요청이 독립적인 1턴짜리이고, 대화 이력을 저장할 곳이 없다.

팀 설계 문서("목차" 문서, 섹션 3~9)가 이 세 문제에 대한 상세 설계를 이미 갖고 있어, 이번 스펙은 그 문서와 기존 코드베이스를 대조해 실제로 구현할 범위를 확정한 것이다. 문서와 다르게 결정한 지점은 각 섹션에 명시했다.

## 목표

- `resolve_entity`가 product 외 supplier/location/scrapReason까지 이름으로 찾을 수 있게 일반화
- 오타·부분 이름 같은 두루뭉술한 질의에 대해 후보를 제시하고 사용자 확인을 받는 흐름
- self-correction을 구현할 사람이 바로 이어받아 작업할 수 있는 SubGraph 뼈대(상태, 노드 시그니처, 조건부 엣지)
- 세션 단위 대화 이력의 영속 저장

## 비목표 (이번 스코프 제외)

- self-correction의 실제 검증/실행 로직(SQL/Cypher 파싱, 화이트리스트 검사, DB 실행) — 다른 담당자가 뼈대 위에서 구현
- READ 전용 가드(`sqlparse`/`CyVer` 화이트리스트) — self-correction 실제 구현과 함께 나중에
- `generate_answer`의 실제 자연어 생성·완결성 검증(6-3의 `check_missing_fields`) — 얇은 스텁만 이번 범위
- 로그인/인증 시스템, `employee_id` 식별 — `session_id`만 사용
- `production.productcategory`(category 엔티티 타입) 스키마 추가 — 스키마 파일에 없는 건 이번에 채우지 않음

## 1. 오케스트레이터 구조

`resolve_entity`와 `route_query`는 **분리 유지**한다(ADR 0009가 제안한 병합은 채택하지 않음).

**판단 근거**: 두 노드는 서로 다른 실패 모드를 가진다 — `resolve_entity`는 추출+DB 검증(모호함/미발견 가능), `route_query`는 순수 분류(거의 실패 없음). 합치면 self-correction이 "무엇 때문에 재시도하는지" 구분하기 어려워지고, 재시도 시 엔티티 확정까지 흔들릴 위험이 생긴다. 실제로 self-correction 설계(3번)는 재시도 시 `resolve_entity`가 확정한 entity ID를 그대로 재사용하는 것을 전제로 하므로, 두 노드가 분리돼 있어야 이 전제가 깨끗하게 성립한다.

## 2. Entity Resolution 일반화

### 2-1. 엔티티 타입 소스 (스키마 기반, 하드코딩 없음)

`schema/graph_schema.yaml`의 `nodes` 중 `name` 속성을 가진 노드를 전부 대상으로 한다. 현재 기준: `Product`, `Supplier`, `Location`, `ScrapReason`. `WorkOrder`/`RoutingOperation`은 이름이 없어(ID/합성키로만 식별) 제외된다. `production.productcategory`(category)는 두 스키마 파일 어디에도 없어 이번 범위에서 제외한다(비목표 참고).

타입 목록을 코드에 나열하지 않고 스키마 로더가 반환하는 노드 정보에서 매 요청 시 동적으로 구성한다 — 나중에 이름 있는 노드가 스키마에 추가되면 `resolve_entity.py` 코드 변경 없이 자동으로 포함된다.

### 2-2. 추출 (Function Calling)

기존 `_EXTRACT_PRODUCT_NAME_TOOL`(product 전용, [backend/orchestrator/nodes/resolve_entity.py](../../../backend/orchestrator/nodes/resolve_entity.py))을 대체하는 단일 동적 도구를 만든다. `entityType`(2-1의 타입 enum) + `entityName`을 한 번의 Function Calling으로 추출한다. 질의가 특정 대상을 가리키지 않으면 호출하지 않는다(기존 동작과 동일).

### 2-3. DB 조회 (스키마 기반 동적 조립)

타입별 SQL을 하드코딩하지 않는다. `graph_schema.yaml` 각 노드가 이미 갖고 있는 `source.schema`/`source.table`, `uniqueKey`(및 그 `sourceColumn`), `name.sourceColumn` 정보로 조회 쿼리를 그때그때 조립한다.

```
SELECT {idSourceColumn}, {nameSourceColumn}
FROM {source.schema}.{source.table}
WHERE {nameSourceColumn} = %s
```

### 2-4. 매칭 순서와 퍼지 폴백

1. **정확 일치 우선** — 기존 `_find_product_by_name`과 동일한 방식(회귀 없음). 찾으면 즉시 확정.
2. **실패 시에만 퍼지 폴백** — PostgreSQL `pg_trgm` 확장(`CREATE EXTENSION pg_trgm` 필요, 아직 미설치)의 `similarity()`로 유사 이름 검색.
   - 포함 기준: 유사도 ≥ 0.3
   - 최대 개수: 5개, 유사도 내림차순 정렬
   - 후보가 1개 이상이면 — **1등이 압도적이어도 자동 확정하지 않고** 항상 `EntityAmbiguousError(candidates)`를 발생시켜 사용자 확인을 요구한다.
   - 후보가 0개면 기존과 동일하게 `EntityNotFoundError`.

**판단 근거**: DB 실행 결과에 없는 걸 만들어내지 않는다는 프로젝트 원칙(`query_contracts.json`의 `globalBusinessRules`)에 따라, 퍼지 매칭으로 "추측"한 엔티티를 조용히 자동 확정하는 것보다 확인을 요구하는 편이 안전하다. 최대 개수를 팀 문서의 평가 지표(상위 3개 이내 포함 여부, 9-1)보다 넉넉한 5개로 잡은 이유는, 평가 지표는 랭킹 품질을 재는 것이지 반환 목록 크기의 상한이 아니기 때문이다 — 5개를 보여주고 평가 시에만 "상위 3개 안에 정답이 있는가"를 측정하면 랭킹이 다소 부정확해도 진단이 가능하고, 사용자가 첫 시도에 원하는 항목을 찾을 확률도 높아진다.

### 2-5. 후보 응답 형태

```json
{
  "code": "ENTITY_AMBIGUOUS",
  "message": "비슷한 이름이 여러 개 있습니다. 아래 후보 중 하나를 선택해 주세요.",
  "candidates": [
    { "id": 680, "name": "Touring-1000 Yellow, 54", "entityType": "product", "score": 0.62 }
  ]
}
```

`backend/main.py`의 `app_error_handler`가 이미 `AppError`의 `candidates` 속성을 JSON에 실어 보내므로, `EntityAmbiguousError`를 실제로 발생시키기만 하면 새 배관이 필요 없다.

### 2-6. 무상태 + 재진입 (세션과 통합)

오케스트레이터 자체는 대화 맥락을 기억하지 않는 무상태 함수로 유지한다. `/chat` 요청에 `confirmed_entity` 필드를 추가해, 값이 있으면 `resolve_entity`가 매칭 과정을 건너뛰고 해당 ID를 바로 확정한다. 사용자가 후보 목록을 받으면, 프론트가 새 요청에 `confirmed_entity`를 실어 재진입하는 방식으로 "다시 묻기"를 구현한다(진짜 멀티턴 LLM 대화 상태는 아님).

## 3. Self-Correction 뼈대

### 3-1. 아키텍처

SQL Agent와 Cypher Agent를 각각 독립된 LangGraph SubGraph로 분리한다(단일 노드에서 SQL/Cypher 재생성을 모두 처리하는 방식은 채택하지 않음). 각 SubGraph는 `agent ↔ tools` ReAct 구조를 가지며, `retry_count`를 서로 독립적으로 관리해 SQL 재시도와 Cypher 재시도가 서로 간섭하지 않게 한다.

### 3-2. SubGraph 상태

```python
class SQLAgentState(TypedDict):
    query: str
    entity: dict | None
    schema: str
    messages: list
    result: Any | None
    error: str | None
    iteration_count: int

class CypherAgentState(TypedDict):
    query: str
    entity: dict | None
    schema: str
    messages: list
    result: Any | None
    error: str | None
    iteration_count: int
```

### 3-3. 노드 뼈대 (시그니처만, 실제 검증·실행 로직은 self-correction 담당자 몫)

- `agent`: LLM이 SQL/Cypher 생성 + tool 호출 여부 판단 — 기존 `agents/generator.py`의 `generate_query`, `agents/sql|cypher/prompt.py`의 프롬프트 빌더를 재사용/확장
- `tools`: 검증(화이트리스트 등, 이번 범위 제외) + DB 실행을 처리하는 자리 — 이번엔 시그니처와 반환 계약(`result`/`error`)만 정의, 내부 구현은 스텁

### 3-4. 재시도 트리거와 상한

- **트리거**: DB 실행 에러만. **결과 0건 자체는 트리거가 아니다** — `query_contracts.json`의 `emptyResultPolicy`가 여러 질의에서 빈 결과를 정상 답으로 명시하고 있어, 0건을 자동으로 "틀림"으로 보면 정상 케이스까지 재시도하게 된다.
- Cypher SubGraph는 "빈 결과가 진짜 데이터 없음인지, 스키마 참조 오류인지"를 구분할 수 있는 상태 여지(스키마 검증 결과 필드)를 열어둔다 — 실제 검증 로직(CyVer 등)은 담당자가 채운다.
- **상한**: `iteration_count >= 2`를 조건부 엣지가 코드로 강제한다(LLM이 스스로 판단하게 두지 않음). 초과 시 `RetryExceededError`(이미 `backend/orchestrator/errors.py`에 정의돼 있음, 재사용).

### 3-5. entity/route와의 분리

재시도 시 `resolve_entity`가 확정한 entity ID를 그대로 파라미터로 재사용한다 — 재시도 도중 엔티티를 다시 추출하거나 라우팅을 다시 판단하지 않는다.

### 3-6. `generate_answer` 스텁

LLM 호출 없이 `sql_result`/`graph_result`를 `final_answer`에 조합해 넣는 얇은 pass-through 노드를 추가한다. 목적은 두 가지다: (1) self-correction 담당자가 자신의 SubGraph 출력이 전체 파이프라인에서 올바르게 흘러가는지 끝까지 실행해서 확인할 수 있게 하고, (2) `/chat` 응답이 `answer`/`sql`/`cypher`/`error` 필드를 채울 수 있는 그래프 종착점을 만든다. 실제 자연어 생성·완결성 검증은 비목표.

## 4. 세션/이력

- `/chat` 요청에 `session_id`, `confirmed_entity` 필드 추가
- `GET /history/{session_id}` 신규 엔드포인트
- PostgreSQL에 이력 테이블 신설 — 컬럼: `session_id`, `query`, `entity`, `tool_plan`, `sql_query`, `cypher_query`, `final_answer`, `error`, `created_at`. `OrchestratorState`/API 계약과 그대로 맞물리는 구조.
- 식별은 `session_id`만 사용한다(로그인 시스템 없음). "직원이 게스트가 아니므로 영속 저장이 맞다"는 취지는 유지하되, 지금은 `employee_id`를 넣을 인증 체계가 없으므로 `session_id`만으로 시작하고, 나중에 인증이 생기면 컬럼을 추가하는 방식으로 확장한다.

## 5. 기존 코드 재사용 지점

새로 만들기 전에 반드시 확인하고 이어받을 것들:

| 기존 코드 | 재사용 방법 |
| --- | --- |
| `backend/orchestrator/errors.py`의 `EntityAmbiguousError`/`EntityNotFoundError`/`RetryExceededError` | 이미 완성된 예외 클래스. 새로 만들지 말고 그대로 raise |
| `backend/main.py`의 `app_error_handler` | `candidates` 속성을 이미 JSON에 실어 보냄. 손댈 필요 없음 |
| `backend/agents/cypher/schema/loader.py`, `models.py`(`GraphQueryPolicy` 등) | `graph_schema.yaml`의 노드/속성/`source` 정보를 이미 파싱함 — entityType 동적 목록과 DB 조회 조립에 그대로 재사용 |
| `backend/agents/generator.py`의 `generate_query`, `backend/agents/prompt.py`의 `build_prompt_messages` | self-correction의 `agent` 노드가 SQL/Cypher를 생성할 때 재사용(빈 응답/비정상 종료 검증 로직 포함) |
| `backend/orchestrator/state.py`의 `OrchestratorState` | `sql_query`/`cypher_query`/`sql_result`/`graph_result`/`final_answer`/`error` 필드가 이미 정의돼 있음 — `session_id` 등 필요한 필드만 추가 |
| `backend/orchestrator/nodes/resolve_entity.py`, `route_query.py` | 전면 재작성이 아니라 확장 — 정확 일치 로직과 Function Calling 패턴은 유지하고 타입 동적화·퍼지 폴백만 추가 |
| `backend/core/postgres.py`의 `get_connection`, `backend/core/neo4j.py`의 드라이버 접근 | 새 실행 함수·이력 테이블 저장 모두 기존 커넥션 관리 방식 재사용 |

## 6. 작업 진행 방식

기능 단위로 독립적으로 나눌 수 있는 작업(엔티티 해석 일반화 / self-correction 뼈대 / 세션·이력)은 각각 **별도 브랜치로 분리해 순차 개발**한다 — 하나를 완료하고 나서 다음 브랜치로 넘어간다. `dev`에서 매번 새 브랜치를 딴다.

## 확실하지 않은 부분 / 향후 과제

- `pg_trgm` 확장 설치가 실제 배포 환경(Docker Compose, 원격 공유 서버)에서 별도 승인이 필요한지 확인 필요
- Cypher SubGraph의 "빈 결과 vs 스키마 오류" 구분 로직은 뼈대에 여지만 두고 실제 판단 기준(CyVer 등)은 self-correction 담당자가 정함
- category 엔티티 타입, READ 가드, `generate_answer`의 실제 자연어 생성은 이후 별도 스펙으로 다룸
