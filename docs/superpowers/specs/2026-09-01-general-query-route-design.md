# 일반질의(general) 라우트 설계

## 상태

브레인스토밍 확정, 사용자 최종 승인 후 계획 수립으로 진행.

## 배경

지금 `route_query`는 `sql`/`graph` 두 Tool 중에서만 고르도록 강제돼 있고, `orchestrator/planning.py`의 `parse_execution_plan`은 빈 `tool_plan`을 아예 `ValueError`로 막아둔다. 그래서 제조 데이터와 무관한 질문("안녕하세요" 등)이 들어오면:

1. LLM이 억지로 `sql`/`graph` 중 하나를 골라 이상한 계획을 만들거나,
2. 빈/무효 계획을 반환해 `route_query`가 한 번 재시도한 뒤에도 실패하면 `RoutePlanError`(`ValueError` 서브클래스)를 raise한다.

`RoutePlanError`는 `AppError`가 아니라서 `main.py`에 등록된 예외 핸들러에 안 걸리고, `POST /chat`(`api/chat.py`)에는 `graph.ainvoke(...)` 주변에 try/except가 없어 결국 **처리되지 않은 500 에러**로 끝난다. 사용자에게 "관련 없는 질문입니다" 같은 안내가 전혀 나가지 않는다.

## 목표

- 도메인과 무관한 질문에 500 대신 고정된 안내 메시지로 정상 응답(200)한다.
- "합법적으로 general로 분류된 경우"와 "LLM 응답 파싱/검증 실패"를 구조적으로 분리한다 — 실패를 general로 뭉뚱그리면 진짜 버그가 조용히 숨는다.
- 라우팅 자체가 실패하는 경우(`RoutePlanError`)도 더 이상 처리되지 않은 500으로 새지 않고, 다른 도메인 예외(`EntityNotFoundError` 등)와 같은 방식으로 422 + 한글 메시지를 반환한다.
- 기존 `sql`/`graph`/`HYBRID` 경로의 검증 규칙과 동작은 전혀 건드리지 않는다.

## 비목표

- `execute_plan`/`compose_results`/`generate_answer`의 SQL·Cypher 실행 로직 변경 — general 경로는 이 노드들을 아예 거치지 않는다.
- 프론트엔드 변경 — `ChatResponse`의 모든 필드가 이미 optional이라 `final_answer`만 채워진 응답을 프론트가 그대로 렌더링할 수 있다.
- 답변 문구의 다국어화/설정화 — 지금은 고정 한글 문자열 하나만 둔다.

## 설계

### 1. `orchestrator/planning.py`

`SUPPORTED_TOOLS = {"sql", "graph"}`는 그대로 두고 `GENERAL_ROUTE = "general"`을 별도 상수로 추가한다. `parse_execution_plan`(`planning.py:192-235`)에서 `tool_plan`이 정확히 `["general"]`이면(dict 형식의 `{"tool_plan": ["general"]}`, legacy list 형식 `["general"]` 최상위 배열 둘 다 `tool_plan` 파싱 후 값은 동일하므로 자연히 같이 처리된다) `subqueries` 검증을 건너뛰고 `{"tool_plan": ["general"], "subqueries": []}`를 바로 반환한다.

**삽입 위치가 중요하다**: 이 early-return은 반드시 중복 검사(`planning.py:210-211`, `tool_plan에는 같은 도구를 중복 지정할 수 없습니다`) 직후, `unsupported_tools = set(tool_plan) - SUPPORTED_TOOLS`(`planning.py:212`) 검사 **이전**에 와야 한다 — `"general"`은 `SUPPORTED_TOOLS`에 없으므로 그 검사를 먼저 통과시키면 "지원하지 않는 tool_plan 값: general"로 막혀버린다. `general`이 `sql`/`graph`와 섞여 있으면(예: `["sql","general"]`) 이 early-return 조건(`tool_plan == ["general"]`, 정확히 이 값과 같아야 함)에 안 걸리고 그대로 흘러가 기존 `unsupported_tools` 검사에서 "지원하지 않는 tool_plan 값: general"로 막힌다(의도한 동작 — general은 단독으로만 허용).

### 2. `orchestrator/nodes/route_query.py`

시스템 프롬프트의 Tool 목록에 3번째 항목으로 `general`을 추가하고, 서로 다른 유형(인사·날씨·감정·잡담 추천·정체성 질문)을 대표하는 few-shot 예시를 5개 넣는다:

```
Q: "안녕하세요"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "오늘 날씨가 어때요?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "기분이 어때요?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "요즘 재밌는 영화 뭐 있어?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}

Q: "당신은 누구인가요?"
entity: null
A: {"tool_plan":["general"],"subqueries":[]}
```

**경계 오분류 방지(negative few-shot)**: 인사·잡담처럼 보여도 실제로는 도메인 질문인 경우를 general로 잘못 보내지 않도록, 반대 방향 예시도 2개 추가한다:

```
Q: "안녕하세요, 재고가 부족한 제품 좀 알려주세요."
entity: null
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_low_stock","tool":"sql","question":"재고가 부족한 제품을 조회한다.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}

Q: "LL Road Frame 어때요?"
entity: {"productId": 680}
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_product_info","tool":"sql","question":"LL Road Frame의 정보를 조회한다.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}
```

규칙 섹션에 다음 두 문장을 추가한다:
1. "제조 데이터 조회·분석과 무관한 질문(인사, 날씨·감정 등 잡담, 시스템 정체성 질문, 시스템 능력 밖의 요청 등)이면 다른 Tool 없이 general만 반환하고 subqueries는 빈 배열로 둔다."
2. "인사말이나 잡담으로 시작해도 질문에 도메인 키워드(제품·재고·부품·공급업체·생산·작업지시·폐기·가격·수량 등)나 확인된 entity가 포함돼 있으면 general이 아니라 해당 sql/graph로 라우팅한다."

코드 로직(재시도 루프, `RoutePlanError` 처리)은 그대로 둔다 — `parse_execution_plan`이 `general`을 유효한 성공 케이스로 반환하므로 별도 분기가 필요 없다.

### 3. `orchestrator/nodes/answer_general.py` (신규)

LLM 호출 없이 고정 문자열을 `final_answer`로 반환하는 얇은 노드. `generate_answer.py`와 같은 패턴(factory 함수가 노드 콜러블을 반환).

```python
_GENERAL_ANSWER = (
    "제조 데이터와 관련된 질문을 입력해 주세요.\n"
    "제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다."
)


def make_answer_general_node() -> Callable[[OrchestratorState], Any]:
    async def answer_general(state: OrchestratorState) -> dict:
        return {"final_answer": _GENERAL_ANSWER}

    return answer_general
```

### 4. `orchestrator/graph.py`

`route_query` 다음에 조건부 엣지를 추가한다:

```python
def _route_after_route_query(state: OrchestratorState) -> str:
    return "answer_general" if state.get("tool_plan") == ["general"] else "execute_plan"

graph.add_node("answer_general", cast(Any, make_answer_general_node()))
graph.add_conditional_edges(
    "route_query",
    _route_after_route_query,
    {"answer_general": "answer_general", "execute_plan": "execute_plan"},
)
graph.add_edge("answer_general", END)
```

기존 `graph.add_edge("route_query", "execute_plan")` 한 줄을 이 조건부 엣지로 교체한다. `execute_plan → compose_results → generate_answer → END`는 변경 없음.

### 5. `execute_plan.py` / `compose_results.py` / `generate_answer.py`

변경 없음. general 경로는 그래프 구조상 이 노드들을 아예 거치지 않는다.

### 6. `/chat` 응답 · 대화기록 저장

변경 없음. `entity`/`tool_plan`/`sql_query`/`cypher_query`/`sql_result`/`graph_result`는 자연히 `None`(`tool_plan`만 `["general"]`), `final_answer`만 고정 문구. 기존 `ChatResponse`/`save_conversation` 그대로 통과한다.

### 7. `RoutePlanError`를 `AppError` 계층으로 이동 (방법 A)

지금 `RoutePlanError`(`orchestrator/nodes/route_query.py:35-48`)는 `ValueError`만 상속해서 `main.py:101`의 `@app.exception_handler(AppError)`에 안 걸리고, `POST /chat`(`api/chat.py`의 `graph.ainvoke(...)` 주변엔 try/except가 없음)에서 처리되지 않은 500으로 샌다. 기존 `EntityNotFoundError`/`RetryExceededError`(`orchestrator/errors.py`)와 같은 패턴으로 맞춘다.

**이동 대상**: `RoutePlanError` 클래스와 그 생성자가 폴백으로 쓰는 `_recover_tool_plan` 헬퍼(`route_query.py:13-32`)를 `orchestrator/nodes/route_query.py`에서 `orchestrator/errors.py`로 옮긴다. `_recover_tool_plan`이 `SUPPORTED_TOOLS`를 쓰므로 `errors.py`가 `from orchestrator.planning import SUPPORTED_TOOLS`를 새로 import한다(순환 참조 없음 — `planning.py`는 아무것도 import하지 않는 leaf 모듈, 실측 확인).

```python
# orchestrator/errors.py에 추가
import json

from orchestrator.planning import SUPPORTED_TOOLS


def _recover_tool_plan(raw_response: str) -> list[str] | None:
    """전체 계획이 잘못돼도 독립적으로 유효한 route 선택은 보존한다."""
    try:
        raw = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return None
    value = (
        raw
        if isinstance(raw, list)
        else raw.get("tool_plan") if isinstance(raw, dict) else None
    )
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(tool, str) for tool in value)
        or len(value) != len(set(value))
        or bool(set(value) - SUPPORTED_TOOLS)
    ):
        return None
    return list(value)


# 라우팅 계획을 세우지 못했을 때(빈/무효 tool_plan, 검증 실패 재시도 소진 등) 발생
class RoutePlanError(AppError):
    """검증 실패 정보와 모델 응답 원문을 함께 보존한다."""

    def __init__(
        self,
        message: str,
        raw_response: str,
        tool_plan: list[str] | None = None,
    ) -> None:
        super().__init__(
            422,
            "ROUTE_PLAN_ERROR",
            "질문을 처리할 계획을 세우지 못했습니다. 질문을 더 구체적으로 입력해 주세요.",
        )
        self.raw_response = raw_response
        self.tool_plan = (
            tool_plan if tool_plan is not None else _recover_tool_plan(raw_response)
        )
```

`orchestrator/nodes/route_query.py`에서 이 클래스/헬퍼 정의를 지우고 `from orchestrator.errors import RoutePlanError`로 바꾼다. `route_query()` 함수 로직(재시도 루프, raise 지점)은 그대로 — import 경로만 바뀐다.

**영향받는 import 3곳** (모두 `from orchestrator.nodes.route_query import RoutePlanError` → `from orchestrator.errors import RoutePlanError`로 변경):
- `backend/tests/orchestrator/test_route_query.py:5`
- `backend/tests/evaluation/test_runner_outcomes.py:13`
- `backend/evaluation/runner.py:41`

`evaluation/runner.py:513`의 `except RoutePlanError as exc: ... except ValueError as exc: ...` 순서는 그대로 둔다 — `RoutePlanError`가 더 이상 `ValueError`가 아니게 되지만, 특정 타입을 먼저 잡는 `except RoutePlanError`가 여전히 정확히 매칭되므로 동작 변화 없음(실측 확인 대상 — 계획 실행 시 재확인).

**`main.py`는 변경 없음**: 기존 `@app.exception_handler(AppError)`가 `RoutePlanError`도 `AppError`의 서브클래스가 됐으니 그대로 처리한다. 응답은 `{"code": "ROUTE_PLAN_ERROR", "message": "질문을 처리할 계획을 세우지 못했습니다..."}`, status 422.

## 오류 처리

- `route_query`가 여전히 진짜 파싱/검증 실패(JSON 깨짐, 지원 안 하는 tool 값, `general`과 다른 tool을 섞음 등)를 내면 기존과 동일하게 재시도 후 `RoutePlanError`로 raise한다 — 재시도 루프 자체는 이번 설계로 전혀 바뀌지 않는다.
- 다만 그 `RoutePlanError`가 `/chat`까지 올라왔을 때의 결과는 바뀐다: 위 7번 설계대로 `AppError` 서브클래스가 되면서 처리되지 않은 500 대신 422 + 한글 메시지로 응답한다.

## 테스트

- `tests/orchestrator/test_planning.py`: `tool_plan=["general"]`이면 `subqueries` 빈 배열이어도 통과, `general`+`sql` 혼합은 에러, 기존 sql/graph/HYBRID 케이스는 회귀 없음.
- `tests/orchestrator/test_route_query.py`:
  - LLM이 `{"tool_plan":["general"],"subqueries":[]}`를 반환하면 `RoutePlanError` 없이 그대로 반환.
  - **경계 케이스 회귀 테스트**: LLM이 "안녕하세요, 재고가 부족한 제품 좀 알려주세요." 같은 혼합 질문에 대해 `{"tool_plan":["sql"],...}`(도메인 라우트)를 반환하면 그대로 통과하고 general로 강제 변환되지 않는지 확인 — 코드가 LLM 판단을 임의로 덮어쓰지 않는다는 걸 못박는 테스트. 반대로 confirmed entity가 있는데도 LLM이 general을 반환하는 극단 케이스도 `parse_execution_plan`이 있는 그대로(LLM 판단 존중) 통과시키는지 확인.
- `tests/orchestrator/test_answer_general.py` (신규): 노드가 항상 같은 고정 문자열을 `final_answer`로 반환.
- `tests/orchestrator/test_graph*.py` 또는 `test_execute_plan.py`류에 조건부 엣지 통합 테스트 추가: `route_query`가 general을 반환하면 `execute_plan`/`compose_results`가 전혀 호출되지 않고 그래프가 `answer_general` 결과로 끝난다(compiled graph를 직접 돌리거나, 조건부 엣지 함수 `_route_after_route_query`를 단위 테스트).
- `tests/api/test_chat.py`: general 질문에 대해 `/chat`이 200과 고정 `final_answer`를 반환하고 `sql_query`/`cypher_query`/`sql_result`/`graph_result`가 모두 `None`인 통합 테스트 추가.
- `tests/orchestrator/test_errors.py`: `RoutePlanError`가 `AppError` 서브클래스이고 `status_code=422`, `code="ROUTE_PLAN_ERROR"`인지 확인. `raw_response`가 유효한 tool_plan을 담고 있으면 `tool_plan`이 채워지고, 아니면 `None`인지(`_recover_tool_plan` 폴백) 확인.
- `tests/api/test_chat.py`: `route_query`가 (재시도 소진 후) `RoutePlanError`를 raise하면 `/chat`이 처리되지 않은 500이 아니라 422 + `{"code": "ROUTE_PLAN_ERROR", ...}`를 반환하는 통합 테스트 추가(`main.py`의 `app_error_handler`가 실제로 잡는지까지 확인하려면 `TestClient(app)`으로 앱 전체를 띄우는 기존 `test_chat_endpoint_accepts_request_with_valid_cookie` 스타일 사용).
- `tests/evaluation/test_runner_outcomes.py`: import 경로만 바뀐 뒤에도 기존 `planningError` 기록 동작이 회귀 없는지 재확인(이미 있는 테스트, 새 테스트 불필요 — import 수정 후 그대로 통과해야 함).

## `general` 분류 정확도 (최소 구현으로 미루지 않고 이번 범위에 포함)

LLM의 실제 판단 정확도(애매한 질문을 잘못 general로 보내거나, 반대로 진짜 잡담을 도메인 질문으로 오인)는 유닛 테스트만으로 100% 보장할 수 없다 — 이건 LLM 호출 자체의 성질이지 우리 코드의 결정론적 버그가 아니다. 그래도 아래 세 가지로 이번 범위에서 실질적으로 다룬다:

1. **양방향 few-shot** — general 예시 5개 + "인사/잡담처럼 보이지만 실제 도메인 질문"인 negative 예시 2개(위 2번 섹션)를 프롬프트에 함께 넣어 경계를 명시적으로 가르친다.
2. **명시적 규칙 문장** — "도메인 키워드나 확인된 entity가 있으면 general이 아니다"를 규칙으로 못박아, 인사말로 시작하는 진짜 질문이 general로 밀리지 않게 한다.
3. **경계 회귀 테스트** — 위 테스트 섹션의 "경계 케이스 회귀 테스트"로, 적어도 코드 레벨(파싱·검증·라우팅)에서 LLM이 뭘 반환하든 우리가 그 판단을 왜곡 없이 그대로 실행에 반영하는지 고정해 둔다. LLM 판단 자체의 품질은 이 테스트로 보장되지 않지만, "LLM이 옳게 판단했는데 코드가 망가뜨리는" 회귀는 여기서 잡힌다.

향후 실사용 데이터로 프롬프트를 더 조정할 여지는 항상 남아있지만(모든 LLM 프롬프트가 그렇듯), 위 세 가지가 이번 구현에 포함되므로 "최소 구현이라 나중에" 상태로 남겨두지 않는다.

## 확실하지 않은 점 / 향후 과제

없음 — 브레인스토밍 과정에서 나온 미결 사항(general 분류 정확도, RoutePlanError의 500 누수)을 모두 이번 스코프에 포함시켰다.
