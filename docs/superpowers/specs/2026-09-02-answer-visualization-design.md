# 챗봇 답변 데이터 시각화 설계

상태: 승인됨 — 구현 대기

## 1. 배경 및 목표

현재 `/chat`의 "AI 정리 답변"(`NaturalLanguageAnswerBox`)은 LLM이 생성한 자유 형식 Markdown을 그대로 렌더링한다. 여러 행을 비교·순위화하는 질문(예: "판매량 TOP5", "반려 수량 많은 공급업체 TOP5")에서 LLM이 Markdown 표를 만들어 답하는 경우가 많아, 사용자가 한눈에 경향을 파악하기 어렵다.

목표: `composed_result`(이미 검증된 구조화 조회 결과)를 기준으로 **결정론적 규칙**이 시각화 타입(KPI 카드 / 막대그래프)을 선택하고, 프론트가 해당 타입에 맞는 차트를 렌더링한다. LLM은 시각화 판단에 관여하지 않는다 — 이 프로젝트가 지금까지 자연어 답변 grounding에 들인 노력([docs/plans/answer-generation-improvement-proposal.md](../../plans/answer-generation-improvement-proposal.md) 참고)과 같은 이유로, 차트 타입/데이터도 LLM 환각 없이 구조화 데이터에서 직접 도출한다.

그래프 관계형 질의(BOM 경로, 계층 구조, 공급망 영향 등)는 이미 `PathGraphCanvas`가 시각화하고 있으므로 이번 범위에서 제외한다.

## 2. 범위

**대상**: "AI 정리 답변" 박스(`NaturalLanguageAnswerBox`) 안에서 LLM이 표 형태로 답하는 케이스.

**제외**:
- 접혀있는 "조회 근거 데이터" 패널의 `ResultsTable`(원본 표 그대로 유지)
- `PathGraphCanvas`가 다루는 그래프/경로/계층형 결과
- 시계열(라인 차트), 파이 차트 — 현재 스키마(`queries/evaluation/manifest.json` 골드셋 RQ01~20/HQ01~10)에 날짜·시간 축이나 부분-전체 비율을 다루는 질의가 없어 v1 범위에서 제외. 필요해지면 별도로 확장한다.

## 3. 백엔드: 시각화 규칙 엔진

새 모듈 `backend/orchestrator/nodes/build_visualization.py`에 순수 함수를 둔다.

```python
def build_visualization_spec(composed_result: ComposedResult) -> VisualizationSpec | None:
    ...
```

### 3.1 컬럼 전처리

- `composed_result["rows"]`의 첫 행 키를 기준으로 컬럼을 본다. `mode == "separate"`이거나 rows가 비어 있으면 즉시 `None`.
- 컬럼명이 `Id`로 끝나는 필드(`productId`, `supplierId`, `scrapReasonId` 등)는 식별자로 간주해 라벨/수치 판정에서 제외한다.
- 남은 컬럼 중 모든 행에서 값이 숫자(또는 숫자 문자열, null 허용)로 파싱되면 "수치 컬럼", 아니면 "텍스트 컬럼"으로 분류한다.

### 3.2 KPI 규칙

- 행 수 == 1, 수치 컬럼 1~4개 → `{ type: "kpi", items: [{label, value}, ...] }`
- 수치 컬럼이 0개면 시각화 없음(`None`) — 텍스트만 있는 단건 결과(예: 제품 속성 조회)는 지금처럼 텍스트로.
- 텍스트 컬럼이 있으면 카드 제목/부제로 사용(예: 제품명).
- 예: RQ01(정가+표준원가 2개 지표), RQ08(안전재고/실재고/부족수량 3개 지표).

### 3.3 막대그래프 규칙

- 행 수 2~20, 텍스트 컬럼 정확히 1개(카테고리 라벨) + 수치 컬럼 정확히 1개(단일 시리즈) → `{ type: "bar", categoryLabel, series: [...], data: [...] }`
- 텍스트 컬럼이 2개 이상이거나 수치 컬럼이 2개 이상이면 시각화 없음(`None`) — 단위가 다른 값을 한 막대에 섞어 왜곡하지 않기 위해 v1은 단일 시리즈만 지원.
- 행 수가 20 초과면 시각화 없음(`None`) — 막대가 너무 많아지면 오히려 가독성이 떨어진다.
- 예: RQ09(판매량 TOP5), RQ10(반려수량 TOP5), HQ03(공급 부품 종류 수 TOP5), HQ05(작업지시 처리 건수 TOP5).

### 3.4 그 외

경로/깊이(`pathProductIds`, `depth` 등)가 섞인 결과, 텍스트 컬럼이 여러 개인 결과(예: RQ11 — 제품명+폐기사유명 2개 텍스트 컬럼)는 모두 `None`을 반환해 지금처럼 텍스트/표로만 보여준다.

### 3.5 컬럼 라벨 매핑

컬럼 키(`totalOrderQty`, `safetyStockLevel` 등) → 한글 라벨을 위한 소규모 매핑 사전을 같은 모듈에 둔다. `queries/evaluation/manifest.json`의 `fieldAliases`에 나오는 필드명 기준으로 채운다(예: `totalOrderQty` → "판매량", `safetyStockLevel` → "안전재고", `actualStock` → "실제재고"). 매핑에 없는 키는 원래 키를 그대로 라벨로 쓴다(안전한 폴백, 유지보수 부담 최소화).

## 4. API 스키마 & 대화기록 저장

### 4.1 응답 모델

`backend/api/chat.py`의 `ChatResponse`에 필드 추가:

```python
class VisualizationSpec(BaseModel):
    type: Literal["kpi", "bar"]
    title: str | None = None
    items: list[KpiItem] | None = None       # type == "kpi"
    categoryLabel: str | None = None         # type == "bar"
    series: list[SeriesMeta] | None = None   # type == "bar"
    data: list[dict[str, Any]] | None = None # type == "bar"

class ChatResponse(BaseModel):
    ...
    visualization: VisualizationSpec | None = None
```

`generate_answer` 노드가 답변 생성 전에 `build_visualization_spec(composed_result)`를 호출해 `state["visualization"]`에 저장하고, `/chat` 핸들러가 이를 `ChatResponse.visualization`으로 노출한다.

### 4.2 대화기록 저장

`backend/core/history.py`:
- `save_conversation`에 `visualization: dict | None` 인자 추가, `app.conversation_history.visualization` JSONB 컬럼에 저장.
- `list_history`가 `visualization` 컬럼도 함께 SELECT해서 반환.

`app.conversation_history`는 서버 코드가 자동 생성하지 않는 수동 관리 테이블([ADR 0011](../../adr/0011-conversation-history-schema.md))이므로, 공유 Postgres에 아래 DDL을 직접 실행해야 한다:

```sql
ALTER TABLE app.conversation_history ADD COLUMN visualization JSONB;
```

**이 ALTER TABLE은 구현 완료 후 실제 실행 직전에 별도로 확인을 구한다** — 공유 인프라 변경이라 임의로 실행하지 않는다. ADR 0011도 이 컬럼 추가로 갱신한다.

### 4.3 프론트 스키마

`frontend/src/lib/schemas.ts`의 `ChatResponseSchema`/`HistoryEntrySchema`에 동일한 `visualization` 필드(옵셔널, nullable) 추가. `frontend/src/lib/displayResult.ts`의 `toDisplayResult`가 `DisplayResult.visualization`으로 통과시켜, 실시간 응답과 기록 재조회가 같은 경로를 타게 한다.

## 5. 프론트엔드 렌더링

- 신규 의존성: `recharts` (`frontend/package.json`) — React 네이티브 SVG 차트, 번들 작음, 기존 CSS 변수 테마와 쉽게 연동.
- 신규 컴포넌트 `frontend/src/components/query/AnswerVisualization.tsx`:
  - `type: "kpi"` → 통계 카드 row(라벨 + 큰 숫자), 카드 2~4개를 가로로 배치.
  - `type: "bar"` → Recharts `BarChart`(가로 방향) + `Bar`. 색상은 기존 CSS 변수(`--info`, `--text`, `--border`, `--panel`) 재사용해 라이트/다크 테마 자동 대응.
- `NaturalLanguageAnswerBox`에 `visualization?: VisualizationSpec | null` prop 추가 — 있으면 마크다운 텍스트 **위**에 `AnswerVisualization`을 렌더링.
- 접혀있는 "조회 근거 데이터" 패널(`ResultEvidencePanel`/`ResultsTable`)은 그대로 유지.

## 6. LLM 프롬프트 조정

`backend/orchestrator/nodes/generate_answer.py`의 `_ANSWER_INSTRUCTIONS`에 조건부 규칙 추가: `build_visualization_spec`이 `None`이 아니면(즉 차트가 이미 표시되면) 프롬프트 컨텍스트에 `has_visualization: true`를 포함시키고, "구조화된 차트가 이미 표시되므로 답변에서는 표를 다시 만들지 말고 핵심 결론을 한두 문장으로 설명하라"는 지시를 추가한다. 차트가 없으면 지금 동작(필요시 Markdown 표 사용)을 그대로 유지한다.

기존 grounding 검증(`_validate_and_sanitize_answer` 등)은 변경하지 않는다 — 시각화 규칙은 별도 경로에서 이미 검증된 `composed_result`로부터만 값을 가져오므로 새로운 환각 경로가 생기지 않는다.

## 7. 테스트 계획

- `backend/tests/orchestrator/nodes/`에 `build_visualization_spec` 유닛 테스트 신설: KPI 케이스, 막대 케이스, 각 폴백 케이스(행 0개/21개 이상, 텍스트 컬럼 0/2개 이상, 수치 컬럼 0/2개 이상, `separate` 모드)를 골드셋 실제 형태(RQ09/RQ10/RQ01/RQ08/RQ11 형태)로 검증.
- `backend/tests/api/test_chat.py`, `backend/tests/core/test_history.py`에 `visualization` 필드 왕복(저장→조회) 케이스 추가.
- 프론트: `AnswerVisualization`에 대한 컴포넌트 테스트(KPI/바/차트 없음 3가지 렌더링), `displayResult.test.ts`에 `visualization` passthrough 케이스 추가.
- UI 수동 확인: dev 서버에서 RQ09류 질문("판매량이 가장 많은 완제품 상위 5개")과 RQ01류 질문("정가와 표준원가")을 실제로 실행해 막대/KPI가 기대대로 나오는지, 라이트/다크 테마 모두 확인.

## 8. 범위 밖 (v1에서 하지 않음)

- 라인 차트, 파이 차트, Sankey, 트리/네트워크 그래프(이미 `PathGraphCanvas`가 담당).
- LLM이 시각화 타입을 직접 결정하는 방식(구조화 출력 기반) — 규칙 기반으로 충분히 커버되는 동안은 도입하지 않는다.
- 다중 시리즈 막대그래프(단위가 다른 수치 컬럼을 함께 그리는 것) — 오해를 부를 수 있어 제외.
- `ResultsTable`(조회 근거 데이터 패널) 자체의 시각화 — 원본 데이터 확인용으로 표 형태를 유지.
