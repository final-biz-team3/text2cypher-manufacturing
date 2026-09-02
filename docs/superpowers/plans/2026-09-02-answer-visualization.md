# 챗봇 답변 데이터 시각화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/chat`의 "AI 정리 답변"이 표로만 나오는 문제를 해결하기 위해, `composed_result`를 결정론적 규칙으로 분석해 KPI 카드 또는 막대그래프 시각화 스펙을 만들고, 프론트가 이를 렌더링하게 한다.

**Architecture:** 백엔드에 순수 규칙 함수(`build_visualization_spec`)를 추가해 `composed_result` 모양(행 수, 컬럼 타입)만으로 시각화 타입을 결정한다(LLM 비관여). 이 스펙은 `/chat` 응답과 대화기록에 `visualization` 필드로 실려 나가고, 프론트는 새 `AnswerVisualization` 컴포넌트(Recharts)로 렌더링해 `NaturalLanguageAnswerBox` 안 마크다운 텍스트 위에 배치한다.

**Tech Stack:** Python(FastAPI/LangGraph, 기존 TypedDict/Pydantic 패턴), TypeScript/React 19 + Zod + Recharts(신규), Vitest, pytest.

**Spec:** [docs/superpowers/specs/2026-09-02-answer-visualization-design.md](../specs/2026-09-02-answer-visualization-design.md)

## Global Constraints

- 시각화 타입 결정은 항상 `composed_result` 기준 규칙 엔진이 하며, LLM이 타입을 고르지 않는다(스펙 §1, §3).
- v1 지원 타입은 `kpi`, `bar` 뿐이다 — 라인/파이/Sankey/트리는 범위 밖(스펙 §2, §8).
- 막대그래프는 단일 시리즈만 지원한다(텍스트 컬럼 정확히 1개 + 수치 컬럼 정확히 1개) — 다중 시리즈는 범위 밖(스펙 §3.3, §8).
- 차트 색상은 기존 CSS 변수(`--info`, `--text`, `--text-muted`, `--border`, `--panel`)만 재사용한다 — 새 팔레트를 만들지 않는다(스펙 §5).
- `app.conversation_history`에 대한 `ALTER TABLE ... ADD COLUMN visualization JSONB;`는 공유 Postgres에 대한 변경이라, 실행 직전 사용자에게 별도로 확인을 구한 뒤에만 실행한다(스펙 §4.2). 코드 작업 자체는 이 확인과 무관하게 먼저 진행할 수 있다.
- 각 태스크의 "Commit" 스텝은 사용자의 그때그때 명시적 승인을 받은 뒤에만 실행한다 — 자동으로 커밋하지 않는다.

---

## Task 1: 시각화 규칙 엔진 (백엔드 순수 함수)

**Files:**
- Modify: `backend/orchestrator/state.py` (타입 추가)
- Create: `backend/orchestrator/nodes/build_visualization.py`
- Test: `backend/tests/orchestrator/test_build_visualization.py`

**Interfaces:**
- Consumes: `orchestrator.state.ComposedResult` (기존 타입, 변경 없음)
- Produces: `orchestrator.state.VisualizationSpec` (TypedDict), `build_visualization.build_visualization_spec(composed_result: ComposedResult) -> VisualizationSpec | None` — Task 2가 이 함수와 타입을 그대로 가져다 쓴다.

- [ ] **Step 1: `state.py`에 시각화 타입 추가**

`backend/orchestrator/state.py`의 `ComposedResult` 클래스 바로 위에 추가:

```python
class VisualizationKpiItem(TypedDict):
    label: str
    value: float | int


class VisualizationSeries(TypedDict):
    key: str
    label: str


class VisualizationSpec(TypedDict):
    type: Literal["kpi", "bar"]
    title: str | None
    items: NotRequired[list[VisualizationKpiItem]]
    categoryLabel: NotRequired[str]
    series: NotRequired[list[VisualizationSeries]]
    data: NotRequired[list[dict[str, Any]]]
```

그리고 `OrchestratorState`의 `final_answer` 필드 바로 아래에 추가:

```python
    # composed_result에서 규칙 기반으로 도출한 시각화 스펙(없으면 None)
    visualization: NotRequired[VisualizationSpec | None]
```

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/orchestrator/test_build_visualization.py` 새로 작성:

```python
"""build_visualization_spec의 규칙 기반 시각화 판정을 테스트한다."""

from typing import Any, cast

from orchestrator.nodes.build_visualization import build_visualization_spec
from orchestrator.state import ComposedResult


def _composed_result(rows: list[dict[str, Any]], **overrides: Any) -> ComposedResult:
    result: ComposedResult = {
        "mode": "joined",
        "rows": rows,
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": len(rows),
        "truncated": False,
    }
    return cast(ComposedResult, {**result, **overrides})


def test_single_row_with_two_numeric_fields_becomes_kpi() -> None:
    composed = _composed_result(
        [
            {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
                "listPrice": 2384.07,
                "standardCost": 1912.42,
            }
        ]
    )

    spec = build_visualization_spec(composed)

    assert spec == {
        "type": "kpi",
        "title": "Touring-1000 Yellow, 54",
        "items": [
            {"label": "정가", "value": 2384.07},
            {"label": "표준원가", "value": 1912.42},
        ],
    }


def test_single_row_with_three_numeric_fields_becomes_kpi() -> None:
    composed = _composed_result(
        [
            {
                "productId": 492,
                "productName": "Paint - Black",
                "safetyStockLevel": 100,
                "actualStock": 40,
                "shortageQty": 60,
            }
        ]
    )

    spec = build_visualization_spec(composed)

    assert spec is not None
    assert spec["type"] == "kpi"
    assert spec["items"] == [
        {"label": "안전재고", "value": 100},
        {"label": "실제재고", "value": 40},
        {"label": "부족 수량", "value": 60},
    ]


def test_single_row_without_numeric_fields_has_no_visualization() -> None:
    composed = _composed_result(
        [
            {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
                "productNumber": "BK-M18B-58",
                "color": "Black",
                "size": "58",
            }
        ]
    )

    assert build_visualization_spec(composed) is None


def test_single_row_with_too_many_numeric_fields_has_no_visualization() -> None:
    row = {"productId": 1, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}

    assert build_visualization_spec(_composed_result([row])) is None


def test_ranked_rows_with_one_label_and_one_numeric_column_becomes_bar() -> None:
    rows = [
        {"productId": 1, "productName": "Product A", "totalOrderQty": 8420},
        {"productId": 2, "productName": "Product B", "totalOrderQty": 6830},
        {"productId": 3, "productName": "Product C", "totalOrderQty": 5210},
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec == {
        "type": "bar",
        "title": None,
        "categoryLabel": "제품명",
        "series": [{"key": "value", "label": "판매량"}],
        "data": [
            {"category": "Product A", "value": 8420},
            {"category": "Product B", "value": 6830},
            {"category": "Product C", "value": 5210},
        ],
    }


def test_bar_rows_with_two_text_columns_has_no_visualization() -> None:
    rows = [
        {
            "workOrderId": 1,
            "productId": 10,
            "productName": "Product A",
            "scrappedQty": 54,
            "scrapReasonId": 13,
            "scrapReasonName": "Thermoform temperature too low",
        },
        {
            "workOrderId": 2,
            "productId": 20,
            "productName": "Product B",
            "scrappedQty": 30,
            "scrapReasonId": 13,
            "scrapReasonName": "Thermoform temperature too low",
        },
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_bar_rows_with_two_numeric_columns_has_no_visualization() -> None:
    rows = [
        {"categoryId": 1, "categoryName": "Components", "productCount": 12, "averageListPrice": 45.5},
        {"categoryId": 2, "categoryName": "Bikes", "productCount": 8, "averageListPrice": 1200.0},
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_more_than_twenty_rows_has_no_visualization() -> None:
    rows = [
        {"productId": i, "productName": f"Product {i}", "totalOrderQty": i}
        for i in range(21)
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_separate_mode_has_no_visualization() -> None:
    composed = _composed_result([], mode="separate")

    assert build_visualization_spec(composed) is None


def test_empty_rows_has_no_visualization() -> None:
    assert build_visualization_spec(_composed_result([])) is None
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/orchestrator/test_build_visualization.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'orchestrator.nodes.build_visualization'`)

- [ ] **Step 4: 규칙 엔진 구현**

`backend/orchestrator/nodes/build_visualization.py` 새로 작성:

```python
"""composed_result 모양만으로 시각화 타입(KPI/막대)을 결정하는 규칙 엔진.

LLM은 이 판정에 관여하지 않는다 - composed_result는 이미 검증된 구조화
데이터이므로, 행 수·컬럼 타입만 보고 결정론적으로 차트 타입을 고른다.
"""

from decimal import Decimal
from typing import Any

from orchestrator.state import ComposedResult, VisualizationSpec

_MIN_BAR_ROWS = 2
_MAX_BAR_ROWS = 20
_MAX_KPI_ITEMS = 4

# composed_result 컬럼 키(camelCase) -> 한글 라벨. 매핑에 없는 키는 그대로
# 쓴다(폴백) - queries/evaluation/manifest.json의 fieldAliases에 나오는
# 필드명 기준으로 채운다.
_FIELD_LABELS: dict[str, str] = {
    "listPrice": "정가",
    "standardCost": "표준원가",
    "priceCostGap": "정가-원가 차액",
    "activeSupplierCount": "활성 공급업체 수",
    "purchasedProductCount": "외부 구매 부품 수",
    "productCount": "제품 수",
    "safetyStockLevel": "안전재고",
    "actualStock": "실제재고",
    "shortageQty": "부족 수량",
    "totalOrderQty": "판매량",
    "totalRejectedQty": "반려 수량",
    "scrappedQty": "폐기 수량",
    "totalScrappedQty": "총 폐기 수량",
    "suppliedProductCount": "공급 부품 종류 수",
    "averageListPrice": "평균 정가",
    "workOrderCount": "작업지시 수",
    "sharedComponentCount": "공통 부품 수",
    "quantityPerAssembly": "조립당 필요 수량",
    "productName": "제품명",
    "supplierName": "공급업체명",
    "categoryName": "분류명",
    "locationName": "작업장명",
    "scrapReasonName": "폐기사유",
    "componentName": "부품명",
    "finishedProductName": "완제품명",
    "rootProductName": "완제품명",
}


def _label_for(key: str) -> str:
    return _FIELD_LABELS.get(key, key)


def _is_id_column(key: str) -> bool:
    return key == "id" or key.endswith("Id")


def _is_numeric(value: Any) -> bool:
    # bool은 int의 서브클래스라 명시적으로 제외한다.
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _to_number(value: Any) -> float | int:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _classify_columns(
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[str]] | None:
    """id류를 제외한 컬럼을 (텍스트 컬럼, 수치 컬럼)으로 나눈다."""
    if not rows:
        return None
    keys = [key for key in rows[0] if not _is_id_column(key)]
    text_columns: list[str] = []
    numeric_columns: list[str] = []
    for key in keys:
        non_null = [row.get(key) for row in rows if row.get(key) is not None]
        if non_null and all(_is_numeric(value) for value in non_null):
            numeric_columns.append(key)
        else:
            text_columns.append(key)
    return text_columns, numeric_columns


def _build_kpi(
    row: dict[str, Any], text_columns: list[str], numeric_columns: list[str]
) -> VisualizationSpec | None:
    if not numeric_columns or len(numeric_columns) > _MAX_KPI_ITEMS:
        return None
    items = [
        {"label": _label_for(key), "value": _to_number(row[key])}
        for key in numeric_columns
        if row.get(key) is not None
    ]
    if not items:
        return None
    title_key = text_columns[0] if text_columns else None
    title = str(row[title_key]) if title_key and row.get(title_key) is not None else None
    return {"type": "kpi", "title": title, "items": items}


def _build_bar(
    rows: list[dict[str, Any]], text_columns: list[str], numeric_columns: list[str]
) -> VisualizationSpec | None:
    if len(text_columns) != 1 or len(numeric_columns) != 1:
        return None
    category_key = text_columns[0]
    value_key = numeric_columns[0]
    data = [
        {"category": str(row[category_key]), "value": _to_number(row[value_key])}
        for row in rows
        if row.get(category_key) is not None and row.get(value_key) is not None
    ]
    if len(data) < _MIN_BAR_ROWS:
        return None
    return {
        "type": "bar",
        "title": None,
        "categoryLabel": _label_for(category_key),
        "series": [{"key": "value", "label": _label_for(value_key)}],
        "data": data,
    }


def build_visualization_spec(composed_result: ComposedResult) -> VisualizationSpec | None:
    """composed_result 모양을 보고 KPI/막대 시각화 스펙을 만들거나, 적합하지
    않으면 None을 반환한다(이 경우 지금처럼 텍스트/표로만 보여준다)."""
    if composed_result["mode"] == "separate":
        return None
    rows = composed_result["rows"]
    classified = _classify_columns(rows)
    if classified is None:
        return None
    text_columns, numeric_columns = classified

    if len(rows) == 1:
        return _build_kpi(rows[0], text_columns, numeric_columns)
    if _MIN_BAR_ROWS <= len(rows) <= _MAX_BAR_ROWS:
        return _build_bar(rows, text_columns, numeric_columns)
    return None
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/orchestrator/test_build_visualization.py -v`
Expected: PASS (전체 10개 테스트)

- [ ] **Step 6: 타입 체크**

Run: `backend/venv/Scripts/python.exe -m mypy backend/orchestrator/nodes/build_visualization.py backend/orchestrator/state.py`
Expected: `Success: no issues found`

- [ ] **Step 7: Commit**

```bash
git add backend/orchestrator/state.py backend/orchestrator/nodes/build_visualization.py backend/tests/orchestrator/test_build_visualization.py
git commit -m "Feat: composed_result 기반 시각화 규칙 엔진 추가"
```

---

## Task 2: `generate_answer` 노드에 시각화 배선

**Files:**
- Modify: `backend/orchestrator/nodes/generate_answer.py`
- Test: `backend/tests/orchestrator/test_generate_answer.py`

**Interfaces:**
- Consumes: `orchestrator.nodes.build_visualization.build_visualization_spec` (Task 1), `orchestrator.state.VisualizationSpec` (Task 1)
- Produces: `make_generate_answer_node(...)`가 반환하는 노드가 LLM 답변 성공 시 `{"final_answer": str, "visualization": VisualizationSpec | None}`을 반환(기존에는 `{"final_answer": str}`만 반환했음). Task 3이 이 `state["visualization"]`을 그대로 API 응답에 옮긴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/orchestrator/test_generate_answer.py`에 다음 테스트를 추가(기존 `import` 블록의 `from orchestrator.state import ComposedResult, QueryFailure`는 그대로 두고, 파일 하단에 새 테스트 함수 추가):

```python
async def test_generate_answer_attaches_visualization_spec_for_ranked_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "query-model")
    client = MockOpenAIClient(make_content_response("판매량 상위 3개 제품입니다."))
    node = make_generate_answer_node(client)

    result = await node(
        {
            "query": "판매량이 가장 많은 완제품 상위 3개를 알려줘.",
            "composed_result": _composed_result(
                rows=[
                    {"productId": 1, "productName": "Product A", "totalOrderQty": 8420},
                    {"productId": 2, "productName": "Product B", "totalOrderQty": 6830},
                    {"productId": 3, "productName": "Product C", "totalOrderQty": 5210},
                ]
            ),
        }
    )

    assert result["final_answer"] == "판매량 상위 3개 제품입니다."
    assert result["visualization"] == {
        "type": "bar",
        "title": None,
        "categoryLabel": "제품명",
        "series": [{"key": "value", "label": "판매량"}],
        "data": [
            {"category": "Product A", "value": 8420},
            {"category": "Product B", "value": 6830},
            {"category": "Product C", "value": 5210},
        ],
    }


async def test_generate_answer_visualization_note_added_to_prompt_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "query-model")
    client = MockOpenAIClient(make_content_response("요약 문장입니다."))
    node = make_generate_answer_node(client)

    await node(
        {
            "query": "판매량이 가장 많은 완제품 상위 3개를 알려줘.",
            "composed_result": _composed_result(
                rows=[
                    {"productId": 1, "productName": "Product A", "totalOrderQty": 8420},
                    {"productId": 2, "productName": "Product B", "totalOrderQty": 6830},
                    {"productId": 3, "productName": "Product C", "totalOrderQty": 5210},
                ]
            ),
        }
    )

    developer_message = client.calls[0]["messages"][0]["content"]
    assert "이미 별도 차트로 표시" in developer_message


async def test_generate_answer_has_no_visualization_for_freeform_single_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "query-model")
    client = MockOpenAIClient(make_content_response("색상은 Black, 크기는 58입니다."))
    node = make_generate_answer_node(client)

    result = await node(
        {
            "query": "제품 속성을 알려줘.",
            "composed_result": _composed_result(
                rows=[{"productId": 1, "productName": "A", "color": "Black", "size": "58"}]
            ),
        }
    )

    assert result["visualization"] is None
    developer_message = client.calls[0]["messages"][0]["content"]
    assert "이미 별도 차트로 표시" not in developer_message
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/orchestrator/test_generate_answer.py -v -k visualization`
Expected: FAIL (`KeyError: 'visualization'` 또는 assertion 실패 — 아직 배선 전)

- [ ] **Step 3: `generate_answer.py` 수정**

`backend/orchestrator/nodes/generate_answer.py` 상단 import에 추가:

```python
from orchestrator.nodes.build_visualization import build_visualization_spec
from orchestrator.state import ComposedResult, OrchestratorState, QueryFailure, VisualizationSpec
```

(기존 `from orchestrator.state import ComposedResult, OrchestratorState, QueryFailure` 줄을 위처럼 `VisualizationSpec` 추가한 형태로 교체.)

`_ANSWER_INSTRUCTIONS` 상수 바로 아래에 추가:

```python
_VISUALIZATION_NOTE = (
    "\n- 이 결과는 이미 별도 차트로 표시되므로 답변에서는 표를 다시 만들지 말고 "
    "핵심 결론을 한두 문장으로 설명하세요."
)
```

`_build_messages` 함수를 다음으로 교체(시그니처에 `has_visualization` 추가):

```python
def _build_messages(
    query: str, context: Mapping[str, Any], *, has_visualization: bool
) -> list[dict[str, str]]:
    context_json = json.dumps(
        context,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    instructions = _ANSWER_INSTRUCTIONS + (
        _VISUALIZATION_NOTE if has_visualization else ""
    )
    return [
        {"role": "developer", "content": instructions},
        {
            "role": "user",
            "content": (
                "사용자 질문:\n"
                f"{query}\n\n"
                "검증된 답변 데이터(JSON):\n"
                f"{context_json}"
            ),
        },
    ]
```

`_generate_markdown_answer` 함수 시그니처와 본문을 수정 — `composed_result` 매개변수 다음에 `visualization` 추가, `_build_messages` 호출부를 갱신:

```python
async def _generate_markdown_answer(
    openai_client: Any,
    *,
    query: str,
    composed_result: ComposedResult,
    visualization: VisualizationSpec | None,
) -> str:
    try:
        context = build_answer_context(composed_result)
        if context["included_count"] == 0:
            logger.warning("답변 생성 실패(포함할 행 없음)")
            raise AnswerGenerationError()
        model = os.getenv("ANSWER_MODEL", "").strip() or os.environ["OPENAI_MODEL"]
        max_output_tokens = int(
            os.getenv("ANSWER_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))
        )
        if max_output_tokens <= 0:
            raise ValueError("ANSWER_MAX_OUTPUT_TOKENS must be positive.")
        response = await openai_client.chat.completions.create(
            model=model,
            messages=_build_messages(
                query, context, has_visualization=visualization is not None
            ),
            max_completion_tokens=max_output_tokens,
        )
```

(이 아래 나머지 본문 — `if not response.choices:` 부터 함수 끝까지는 지금 그대로 둔다. `_build_messages(query, context)` 호출부 한 줄만 위처럼 바뀐다.)

`make_generate_answer_node` 안의 `generate_answer` 함수에서, `_generate_markdown_answer` 호출부를 찾아 다음으로 교체:

```python
        visualization = build_visualization_spec(composed_result)
        final_answer = await _generate_markdown_answer(
            openai_client,
            query=state["query"],
            composed_result=composed_result,
            visualization=visualization,
        )
        return {"final_answer": final_answer, "visualization": visualization}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/orchestrator/test_generate_answer.py -v`
Expected: PASS (기존 테스트 포함 전체 통과 — 기존 `test_generate_answer_uses_only_query_and_composed_result`의 `assert result == {"final_answer": ...}`를 다음으로 갱신해야 한다: `{"id": 1, "stock": 10}` 한 행짜리 결과는 `id`가 제외되고 `stock` 하나만 수치 컬럼으로 남아 KPI가 되므로,

```python
    assert result == {
        "final_answer": "**재고는 10개입니다.**",
        "visualization": {
            "type": "kpi",
            "title": None,
            "items": [{"label": "stock", "value": 10}],
        },
    }
```

로 교체한다.)

- [ ] **Step 5: 타입 체크**

Run: `backend/venv/Scripts/python.exe -m mypy backend/orchestrator/nodes/generate_answer.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/nodes/generate_answer.py backend/tests/orchestrator/test_generate_answer.py
git commit -m "Feat: generate_answer가 시각화 스펙을 계산해 상태에 싣는다"
```

---

## Task 3: `/chat` 응답에 `visualization` 노출

**Files:**
- Modify: `backend/api/chat.py`
- Test: `backend/tests/api/test_chat.py`

**Interfaces:**
- Consumes: `result.get("visualization")` (Task 2가 orchestrator state에 채운 값)
- Produces: `ChatResponse.visualization: VisualizationSpec | None` — 프론트 `ChatResponseSchema`(Task 5)가 그대로 파싱한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/api/test_chat.py`의 기존 테스트들을 참고해, 파일 끝에 추가(기존 헬퍼 `_answering_client`, `_fake_request` 재사용):

```python
async def test_chat_response_includes_visualization_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_sql이 판매량 순위 행을 반환하면 응답에 bar 시각화가 실린다."""
    openai_client = _answering_client(
        make_no_tool_call_response(),
        make_content_response('["sql"]'),
        make_content_response('{"requiredOutputs":["productName","totalOrderQty"]}'),
        make_content_response(
            'SELECT name AS "productName", SUM(orderqty) AS "totalOrderQty" '
            "FROM sales.salesorderdetail GROUP BY name ORDER BY 2 DESC LIMIT 3"
        ),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(chat_module, "get_pool", lambda: MockAsyncPostgresPool())
    monkeypatch.setattr(chat_module, "get_write_pool", lambda: MockAsyncWritePool())

    async def fake_execute_sql(sql: str) -> list[dict]:
        return [
            {"productName": "Product A", "totalOrderQty": 8420},
            {"productName": "Product B", "totalOrderQty": 6830},
            {"productName": "Product C", "totalOrderQty": 5210},
        ]

    monkeypatch.setattr(graph_module, "execute_sql", fake_execute_sql)

    result = await chat(
        ChatRequest(query="판매량이 가장 많은 완제품 상위 3개를 알려줘."),
        request=_fake_request(),
        user=CurrentUser(username="kim.quality", role="user"),
    )

    assert result.visualization is not None
    assert result.visualization.type == "bar"
    assert result.visualization.data == [
        {"category": "Product A", "value": 8420},
        {"category": "Product B", "value": 6830},
        {"category": "Product C", "value": 5210},
    ]
```

이 테스트의 질의("판매량이 가장 많은 완제품 상위 3개를 알려줘.")에는 고유 이름이 없으므로 `resolve_entity`는 `confirmed_entity` 유무와 무관하게 `extract_entity`를 호출하지 않는 방향으로 LLM을 한 번 호출한다(`make_no_tool_call_response()`가 이를 목업함) — 이후 `route_query`(`["sql"]`), `plan_outputs`(JSON), SQL 생성까지 기존 `test_chat_passes_confirmed_entity_and_runs_sql_agent_once`와 동일한 4단계 목업 순서를 그대로 따른다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_chat.py -v -k visualization`
Expected: FAIL (`AttributeError: 'ChatResponse' object has no attribute 'visualization'`)

- [ ] **Step 3: `ChatResponse`에 필드 추가**

`backend/api/chat.py`의 `QueryOutcome` 클래스 바로 아래, `ChatResponse` 클래스 바로 위에 시각화 모델 추가:

```python
class VisualizationKpiItem(BaseModel):
    label: str
    value: float | int


class VisualizationSeries(BaseModel):
    key: str
    label: str


class VisualizationSpec(BaseModel):
    type: str
    title: str | None = None
    items: list[VisualizationKpiItem] | None = None
    categoryLabel: str | None = None
    series: list[VisualizationSeries] | None = None
    data: list[dict[str, Any]] | None = None
```

`ChatResponse` 클래스에 필드 추가:

```python
class ChatResponse(BaseModel):
    """POST /chat 응답 계약. 필드 목록이 이 모델 하나로 고정돼, orchestrator
    내부 전용 필드(composed_result 등)가 실수로 새어나가는 걸 막는다."""

    query: str
    entity: dict | list[dict] | None = None
    tool_plan: list[str] | None = None
    sql_query: str | None = None
    cypher_query: str | None = None
    sql_result: QueryOutcome | None = None
    graph_result: QueryOutcome | None = None
    final_answer: str | None = None
    visualization: VisualizationSpec | None = None
```

`chat()` 함수 안, `response = ChatResponse(**_to_json_safe({...}))` 딕셔너리에 키 추가:

```python
                "final_answer": result.get("final_answer"),
                "visualization": result.get("visualization"),
```

- [ ] **Step 4: 대화기록 저장 호출부 갱신(컴파일 유지용 임시 처리)**

`save_conversation` 호출부(Task 4에서 실제로 인자를 받도록 바꾸기 전까지는) 아직 `visualization`을 넘기지 않는다 — 이 태스크에서는 API 응답 노출만 다룬다. 다음 태스크(Task 4)에서 `save_conversation` 시그니처를 바꾸면서 이 호출부도 함께 갱신하므로, 지금은 그대로 둔다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_chat.py -v`
Expected: PASS (전체)

- [ ] **Step 6: 타입 체크**

Run: `backend/venv/Scripts/python.exe -m mypy backend/api/chat.py`
Expected: `Success: no issues found`

- [ ] **Step 7: Commit**

```bash
git add backend/api/chat.py backend/tests/api/test_chat.py
git commit -m "Feat: /chat 응답에 visualization 필드 노출"
```

---

## Task 4: 대화기록에 `visualization` 저장·조회

**Files:**
- Modify: `backend/core/history.py`
- Modify: `backend/api/chat.py` (save_conversation 호출부)
- Modify: `docs/adr/0011-conversation-history-schema.md`
- Test: `backend/tests/core/test_history.py`
- Test: `backend/tests/api/test_history.py`

**Interfaces:**
- Consumes: `ChatResponse.visualization`(Task 3), `VisualizationSpec` TypedDict(Task 1)
- Produces: `save_conversation(pool, username, query, final_answer, sql_query, cypher_query, sql_result, graph_result, visualization)` (인자 추가), `list_history(...)`가 반환하는 각 dict에 `"visualization"` 키 포함 — Task 5의 프론트 `HistoryEntrySchema`가 이 키를 그대로 받는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/core/test_history.py`의 `test_save_conversation_inserts_and_commits`를 다음으로 교체:

```python
async def test_save_conversation_inserts_and_commits() -> None:
    pool = _FakePool()

    await save_conversation(
        pool,
        "kim.quality",
        "정가 알려줘",
        "1200원입니다",
        "SELECT listprice FROM production.product",
        None,
        {
            "result": [{"listprice": 1200}],
            "error": None,
            "attempts": [],
            "empty_reason": None,
        },
        None,
        {"type": "kpi", "title": None, "items": [{"label": "정가", "value": 1200}]},
    )

    query, params = pool.statements[0]
    assert "INSERT INTO app.conversation_history" in query
    assert params[0] == "kim.quality"
    assert params[1] == "정가 알려줘"
    assert params[2] == "1200원입니다"
    assert '"listprice": 1200' in params[5]
    assert params[6] is None
    assert '"type": "kpi"' in params[7]
    assert pool.committed is True
```

`test_list_history_scopes_to_own_rows_for_non_admin`을 다음으로 교체(9-튜플 → 10-튜플, `visualization`이 `created_at` 앞에 옴):

```python
async def test_list_history_scopes_to_own_rows_for_non_admin() -> None:
    pool = _FakePool(
        rows=[
            (
                1,
                "kim.quality",
                "q",
                "a",
                None,
                None,
                None,
                None,
                None,
                datetime(2026, 1, 1),
            )
        ]
    )

    result = await list_history(pool, CurrentUser(username="kim.quality", role="user"))

    query, params = pool.statements[0]
    assert "WHERE username = %s" in query
    assert params == ("kim.quality",)
    assert result == [
        {
            "id": 1,
            "username": "kim.quality",
            "query": "q",
            "final_answer": "a",
            "sql_query": None,
            "cypher_query": None,
            "sql_result": None,
            "graph_result": None,
            "visualization": None,
            "created_at": "2026-01-01T00:00:00",
        }
    ]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_history.py -v`
Expected: FAIL (`TypeError: save_conversation() takes ... positional arguments but 9 were given` 및 dict 키 불일치)

- [ ] **Step 3: `history.py` 수정**

`backend/core/history.py`의 `save_conversation` 함수를 다음으로 교체:

```python
async def save_conversation(
    pool: Pool,
    username: str,
    query: str,
    final_answer: str | None,
    sql_query: str | None,
    cypher_query: str | None,
    sql_result: dict | None,
    graph_result: dict | None,
    visualization: dict | None,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO app.conversation_history "
            "(username, query, final_answer, sql_query, cypher_query, sql_result, graph_result, visualization) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                username,
                query,
                final_answer,
                sql_query,
                cypher_query,
                json.dumps(sql_result) if sql_result is not None else None,
                json.dumps(graph_result) if graph_result is not None else None,
                json.dumps(visualization) if visualization is not None else None,
            ),
        )
        await conn.commit()
```

`list_history` 함수를 다음으로 교체:

```python
async def list_history(pool: Pool, user: CurrentUser) -> list[dict]:
    """admin이면 전체, 아니면 본인 기록만 최신순으로 반환한다."""
    base_query = (
        "SELECT id, username, query, final_answer, sql_query, cypher_query, "
        "sql_result, graph_result, visualization, created_at FROM app.conversation_history"
    )
    async with pool.connection() as conn:
        if user.role == "admin":
            cursor = await conn.execute(base_query + " ORDER BY created_at DESC")
        else:
            cursor = await conn.execute(
                base_query + " WHERE username = %s ORDER BY created_at DESC",
                (user.username,),
            )
        rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "username": row[1],
            "query": row[2],
            "final_answer": row[3],
            "sql_query": row[4],
            "cypher_query": row[5],
            "sql_result": row[6],
            "graph_result": row[7],
            "visualization": row[8],
            "created_at": row[9].isoformat(),
        }
        for row in rows
    ]
```

- [ ] **Step 4: `api/chat.py`의 `save_conversation` 호출부 갱신**

`backend/api/chat.py`의 `await save_conversation(...)` 호출에 인자 추가:

```python
        await save_conversation(
            get_write_pool(),  # type: ignore[arg-type]
            user.username,
            response.query,
            response.final_answer,
            response.sql_query,
            response.cypher_query,
            response.sql_result.model_dump() if response.sql_result else None,
            response.graph_result.model_dump() if response.graph_result else None,
            response.visualization.model_dump() if response.visualization else None,
        )
```

- [ ] **Step 5: `backend/tests/api/test_history.py`의 튜플 픽스처 갱신**

`test_get_history_returns_own_rows_for_user`의 `rows` 정의를 10-튜플로 교체:

```python
    rows = [
        (
            1,
            "kim.quality",
            "q",
            "a",
            None,
            None,
            None,
            None,
            None,
            datetime(2026, 1, 1),
        )
    ]
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_history.py backend/tests/api/test_history.py backend/tests/api/test_chat.py -v`
Expected: PASS (전체)

- [ ] **Step 7: 타입 체크**

Run: `backend/venv/Scripts/python.exe -m mypy backend/core/history.py backend/api/chat.py`
Expected: `Success: no issues found`

- [ ] **Step 8: ADR 0011 갱신**

`docs/adr/0011-conversation-history-schema.md`의 DDL 블록(라인 22~34)을 다음으로 교체:

```sql
CREATE TABLE IF NOT EXISTS app.conversation_history (
    id SERIAL PRIMARY KEY,
    username VARCHAR NOT NULL REFERENCES app.users(username),
    query TEXT NOT NULL,
    final_answer TEXT,
    sql_query TEXT,
    cypher_query TEXT,
    sql_result JSONB,
    graph_result JSONB,
    visualization JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

그리고 "### 3. 테이블 생성" 절 바로 아래에 문단 추가:

```markdown
### 4. 컬럼 추가: `visualization` (2026-09-02)

챗봇 답변 시각화 기능([spec](../superpowers/specs/2026-09-02-answer-visualization-design.md))이 대화기록에도 시각화 스펙을 남기도록, 기존 테이블에 컬럼을 추가했다:

```sql
ALTER TABLE app.conversation_history ADD COLUMN visualization JSONB;
```

`sql_result`/`graph_result`와 같은 이유로 JSONB를 쓴다 — psycopg3가 조회 시 자동으로 Python dict로 역직렬화한다.
```

- [ ] **Step 9: 공유 Postgres에 컬럼 추가 (실행 전 반드시 확인)**

이 스텝은 실제 공유 데이터베이스 스키마를 변경하는 작업이다. **실행하기 전에 사용자에게 명시적으로 확인을 구한다** — 다른 스텝들과 달리 로컬 코드 변경이 아니라 팀이 공유하는 인프라에 대한 변경이다. 확인을 받으면 아래 DDL을 대상 Postgres에 실행한다:

```sql
ALTER TABLE app.conversation_history ADD COLUMN visualization JSONB;
```

- [ ] **Step 10: Commit**

```bash
git add backend/core/history.py backend/api/chat.py backend/tests/core/test_history.py backend/tests/api/test_history.py docs/adr/0011-conversation-history-schema.md
git commit -m "Feat: 대화기록에 visualization 컬럼 저장·조회 추가"
```

---

## Task 5: 프론트 스키마 & DisplayResult 배선

**Files:**
- Modify: `frontend/src/lib/schemas.ts`
- Modify: `frontend/src/types/query.ts`
- Modify: `frontend/src/lib/displayResult.ts`
- Test: `frontend/src/lib/displayResult.test.ts`

**Interfaces:**
- Consumes: 백엔드 `ChatResponse.visualization`/`HistoryEntry.visualization`(Task 3, 4)과 같은 JSON 모양(`{type, title, items?, categoryLabel?, series?, data?}`)
- Produces: `VisualizationSpec` 타입(Zod 추론), `DisplayResult.visualization: VisualizationSpec | null` — Task 6/7의 `AnswerVisualization`/`NaturalLanguageAnswerBox`가 이 타입과 필드를 그대로 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/lib/displayResult.test.ts`의 `describe` 블록 안, 기존 테스트들 사이에 추가:

```ts
  it('passes visualization spec through from a chat response', () => {
    const result = toDisplayResult({
      query: '판매량이 가장 많은 완제품 상위 3개를 알려줘.',
      visualization: {
        type: 'bar',
        title: null,
        categoryLabel: '제품명',
        series: [{ key: 'value', label: '판매량' }],
        data: [{ category: 'Product A', value: 8420 }],
      },
    })

    expect(result.visualization).toEqual({
      type: 'bar',
      title: null,
      categoryLabel: '제품명',
      series: [{ key: 'value', label: '판매량' }],
      data: [{ category: 'Product A', value: 8420 }],
    })
  })

  it('defaults visualization to null when the response omits it', () => {
    const result = toDisplayResult({ query: '단순 질문' })

    expect(result.visualization).toBeNull()
  })
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npm run test -- displayResult`
Expected: FAIL (TypeScript 타입 에러 — `visualization`이 `ChatResponse`/`DisplayResult`에 없음, 또는 `result.visualization`이 `undefined`)

- [ ] **Step 3: Zod 스키마에 시각화 타입 추가**

`frontend/src/lib/schemas.ts`에서 `ChatResponseSchema` 바로 위에 추가:

```ts
export const VisualizationKpiItemSchema = z.object({
  label: z.string(),
  value: z.number(),
})

export const VisualizationSeriesSchema = z.object({
  key: z.string(),
  label: z.string(),
})

export const VisualizationSpecSchema = z.object({
  type: z.enum(['kpi', 'bar']),
  title: z.string().nullable().optional(),
  items: z.array(VisualizationKpiItemSchema).nullable().optional(),
  categoryLabel: z.string().nullable().optional(),
  series: z.array(VisualizationSeriesSchema).nullable().optional(),
  data: z.array(z.record(z.string(), z.union([z.string(), z.number()]))).nullable().optional(),
})
export type VisualizationSpec = z.infer<typeof VisualizationSpecSchema>
```

`ChatResponseSchema`에 필드 추가:

```ts
export const ChatResponseSchema = z.object({
  query: z.string(),
  sql_query: z.string().nullable().optional(),
  cypher_query: z.string().nullable().optional(),
  sql_result: QueryOutcomeSchema.optional(),
  graph_result: QueryOutcomeSchema.optional(),
  final_answer: z.string().nullable().optional(),
  visualization: VisualizationSpecSchema.nullable().optional(),
})
```

`HistoryEntrySchema`에도 필드 추가:

```ts
export const HistoryEntrySchema = z.object({
  id: z.number(),
  username: z.string(),
  query: z.string(),
  final_answer: z.string().nullable(),
  sql_query: z.string().nullable(),
  cypher_query: z.string().nullable(),
  sql_result: QueryOutcomeSchema,
  graph_result: QueryOutcomeSchema,
  visualization: VisualizationSpecSchema.nullable(),
  created_at: z.string(),
})
```

- [ ] **Step 4: `DisplayResult`에 필드 추가**

`frontend/src/types/query.ts`의 `DisplayResult` 인터페이스에 필드 추가:

```ts
export interface DisplayResult {
  query: string
  answer: string
  sql: string | null
  cypher: string | null
  columns: ResultColumn[]
  rows: Record<string, string>[]
  hasGraphResult: boolean
  graphRows: Record<string, unknown>[]
  graphError: string | null
  graphEmptyReason: string | null
  sqlAttempts: RetryAttempt[]
  cypherAttempts: RetryAttempt[]
  visualization: VisualizationSpec | null
}
```

파일 상단 import에 추가:

```ts
import type { VisualizationSpec } from '@/lib/schemas'
```

- [ ] **Step 5: `toDisplayResult` 갱신**

`frontend/src/lib/displayResult.ts`의 반환 객체에 필드 추가:

```ts
  return {
    query: response.query,
    answer: toDisplayAnswer(response.final_answer),
    sql: response.sql_query ?? null,
    cypher: response.cypher_query ?? null,
    columns,
    rows,
    hasGraphResult: response.graph_result != null,
    graphRows: response.graph_result?.result ?? [],
    graphError: response.graph_result?.error ?? null,
    graphEmptyReason: response.graph_result?.empty_reason ?? null,
    sqlAttempts: response.sql_result?.attempts ?? [],
    cypherAttempts: response.graph_result?.attempts ?? [],
    visualization: response.visualization ?? null,
  }
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd frontend && npm run test -- displayResult`
Expected: PASS (전체)

- [ ] **Step 7: 타입 체크**

Run: `cd frontend && npm run typecheck`
Expected: 에러 없음

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/schemas.ts frontend/src/types/query.ts frontend/src/lib/displayResult.ts frontend/src/lib/displayResult.test.ts
git commit -m "Feat: 프론트 스키마·DisplayResult에 visualization 배선"
```

---

## Task 6: `AnswerVisualization` 컴포넌트 (Recharts)

**Files:**
- Modify: `frontend/package.json` (recharts 추가)
- Create: `frontend/src/components/query/AnswerVisualization.tsx`
- Test: `frontend/src/components/query/AnswerVisualization.test.tsx`

**Interfaces:**
- Consumes: `VisualizationSpec`(Task 5)
- Produces: `AnswerVisualization({ visualization: VisualizationSpec }): JSX.Element | null` — Task 7의 `NaturalLanguageAnswerBox`가 이 컴포넌트를 그대로 렌더링한다.

- [ ] **Step 1: recharts 설치**

Run: `cd frontend && npm install recharts@^2.15.0`
Expected: `frontend/package.json`의 `dependencies`에 `"recharts": "^2.15.x"` 추가, `package-lock.json` 갱신.

- [ ] **Step 2: 실패하는 테스트 작성**

`frontend/src/components/query/AnswerVisualization.test.tsx` 새로 작성. 이 프로젝트의 기존 컴포넌트 테스트(`NaturalLanguageAnswerBox.test.tsx`)와 같은 방식으로 `react-dom/server`의 `renderToStaticMarkup`을 쓴다 — `@testing-library/react`는 이 프로젝트에 없고 새로 추가하지 않는다:

```tsx
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { AnswerVisualization } from './AnswerVisualization'

describe('AnswerVisualization', () => {
  it('renders KPI cards with labels and formatted values', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'kpi',
          title: 'Touring-1000 Yellow, 54',
          items: [
            { label: '정가', value: 2384.07 },
            { label: '표준원가', value: 1912.42 },
          ],
        }}
      />,
    )

    expect(html).toContain('정가')
    expect(html).toContain('2,384.07')
    expect(html).toContain('표준원가')
    expect(html).toContain('1,912.42')
  })

  it('renders a responsive bar chart wrapper for ranked rows', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'bar',
          title: null,
          categoryLabel: '제품명',
          series: [{ key: 'value', label: '판매량' }],
          data: [
            { category: 'Product A', value: 8420 },
            { category: 'Product B', value: 6830 },
          ],
        }}
      />,
    )

    // Recharts의 ResponsiveContainer는 실제 브라우저의 크기 측정에 의존해서
    // renderToStaticMarkup(SSR)로는 내부 SVG/막대까지 그려지지 않는다 -
    // 여기서는 컴포넌트가 올바른 래퍼를 렌더링하는지만 확인한다. 실제 막대
    // 렌더링 여부는 Task 8의 브라우저 수동 확인에서 검증한다.
    expect(html).toContain('recharts-responsive-container')
  })

  it('renders nothing when kpi items are empty', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization visualization={{ type: 'kpi', title: null, items: [] }} />,
    )

    expect(html).toBe('')
  })

  it('renders nothing when bar data is empty', () => {
    const html = renderToStaticMarkup(
      <AnswerVisualization
        visualization={{
          type: 'bar',
          title: null,
          categoryLabel: '제품명',
          series: [{ key: 'value', label: '판매량' }],
          data: [],
        }}
      />,
    )

    expect(html).toBe('')
  })
})
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd frontend && npm run test -- AnswerVisualization`
Expected: FAIL (`Failed to resolve import "./AnswerVisualization"`)

- [ ] **Step 4: 컴포넌트 구현**

`frontend/src/components/query/AnswerVisualization.tsx` 새로 작성:

```tsx
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { VisualizationSpec } from '@/lib/schemas'

interface AnswerVisualizationProps {
  visualization: VisualizationSpec
}

const numberFormatter = new Intl.NumberFormat('ko-KR')

function KpiCards({ items }: { items: NonNullable<VisualizationSpec['items']> }) {
  if (items.length === 0) return null
  return (
    <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="rounded-md border border-border bg-panel px-3 py-2">
          <p className="text-[10.5px] text-text-muted">{item.label}</p>
          <p className="mt-0.5 text-base font-semibold text-text">
            {numberFormatter.format(item.value)}
          </p>
        </div>
      ))}
    </div>
  )
}

function BarVisualization({
  data,
  series,
}: {
  data: NonNullable<VisualizationSpec['data']>
  series: NonNullable<VisualizationSpec['series']>[number]
}) {
  if (data.length === 0) return null
  return (
    <div className="mb-3 rounded-md border border-border bg-panel p-3">
      <ResponsiveContainer width="100%" height={Math.max(120, data.length * 32)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} />
          <XAxis type="number" tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }} />
          <YAxis
            type="category"
            dataKey="category"
            width={110}
            tick={{ fill: 'var(--color-text)', fontSize: 11 }}
          />
          <Tooltip
            formatter={(value: number) => numberFormatter.format(value)}
            contentStyle={{
              background: 'var(--color-panel)',
              border: '1px solid var(--color-border)',
              fontSize: 12,
            }}
          />
          <Bar dataKey="value" name={series.label} fill="var(--color-info)" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// 규칙 기반으로 결정된 시각화 스펙(KPI 카드 또는 막대그래프)을 렌더링한다.
export function AnswerVisualization({ visualization }: AnswerVisualizationProps) {
  if (visualization.type === 'kpi') {
    return <KpiCards items={visualization.items ?? []} />
  }
  const series = visualization.series?.[0]
  if (!series) return null
  return <BarVisualization data={visualization.data ?? []} series={series} />
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd frontend && npm run test -- AnswerVisualization`
Expected: PASS (전체 4개 테스트)

- [ ] **Step 6: 타입 체크 및 린트**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: 에러 없음

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/query/AnswerVisualization.tsx frontend/src/components/query/AnswerVisualization.test.tsx
git commit -m "Feat: KPI/막대 시각화 렌더링 컴포넌트 추가"
```

---

## Task 7: `NaturalLanguageAnswerBox`/`Dashboard`에 배선

**Files:**
- Modify: `frontend/src/components/query/NaturalLanguageAnswerBox.tsx`
- Modify: `frontend/src/screens/Dashboard.tsx`
- Test: `frontend/src/components/query/NaturalLanguageAnswerBox.test.tsx`

**Interfaces:**
- Consumes: `AnswerVisualization`(Task 6), `DisplayResult.visualization`(Task 5)
- Produces: `NaturalLanguageAnswerBox({ answer, visualization? }: ...)` — 최종 사용자에게 보이는 화면.

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/components/query/NaturalLanguageAnswerBox.test.tsx`에 기존 `describe` 블록 안, 기존 테스트들 사이에 추가:

```tsx
  it('renders the visualization above the markdown answer when present', () => {
    const html = renderToStaticMarkup(
      <NaturalLanguageAnswerBox
        answer="판매량 상위 3개 제품입니다."
        visualization={{
          type: 'bar',
          title: null,
          categoryLabel: '제품명',
          series: [{ key: 'value', label: '판매량' }],
          data: [{ category: 'Product A', value: 8420 }],
        }}
      />,
    )

    expect(html).toContain('recharts-responsive-container')
    const chartIndex = html.indexOf('recharts-responsive-container')
    const answerIndex = html.indexOf('판매량 상위 3개 제품입니다')
    expect(chartIndex).toBeGreaterThan(-1)
    expect(chartIndex).toBeLessThan(answerIndex)
  })

  it('renders no visualization block when visualization is absent', () => {
    const html = renderToStaticMarkup(<NaturalLanguageAnswerBox answer="일반 답변입니다." />)

    expect(html).not.toContain('recharts')
  })
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npm run test -- NaturalLanguageAnswerBox`
Expected: FAIL (TypeScript 에러 — `visualization` prop이 타입에 없음)

- [ ] **Step 3: `NaturalLanguageAnswerBox.tsx` 수정**

파일 상단 import에 추가:

```tsx
import { AnswerVisualization } from './AnswerVisualization'
import type { VisualizationSpec } from '@/lib/schemas'
```

`interface`와 컴포넌트 시그니처, 렌더링부를 다음으로 교체:

```tsx
interface NaturalLanguageAnswerBoxProps {
  answer: string
  visualization?: VisualizationSpec | null
}

// LLM이 생성한 Markdown 답변을 raw HTML 실행 없이 표시한다.
export function NaturalLanguageAnswerBox({
  answer,
  visualization,
}: NaturalLanguageAnswerBoxProps) {
  return (
    <section
      aria-labelledby="ai-answer-title"
      className="min-w-0 overflow-hidden rounded-md border border-info bg-accent-bg text-[13.5px] leading-relaxed text-text"
    >
      <div className="flex items-center gap-2 border-b border-info/25 px-4 py-2.5">
        <Sparkles className="size-4 text-info" aria-hidden="true" />
        <div>
          <h2 id="ai-answer-title" className="text-[12.5px] font-semibold text-text">
            AI 정리 답변
          </h2>
          <p className="text-[10.5px] text-text-muted">조회된 데이터만 근거로 정리했습니다.</p>
        </div>
      </div>
      <div className="p-4">
        {visualization ? <AnswerVisualization visualization={visualization} /> : null}
        <ReactMarkdown
```

(`<ReactMarkdown` 이후 `remarkPlugins`/`components`/닫는 태그는 지금 그대로 둔다 — `<div className="p-4">` 안에서 `AnswerVisualization`이 `ReactMarkdown` 바로 앞에 추가되는 것만 바뀐다.)

- [ ] **Step 4: `Dashboard.tsx` 호출부 갱신**

`frontend/src/screens/Dashboard.tsx`의 `<NaturalLanguageAnswerBox answer={result.answer} />`를 다음으로 교체:

```tsx
              <NaturalLanguageAnswerBox
                answer={result.answer}
                visualization={result.visualization}
              />
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd frontend && npm run test -- NaturalLanguageAnswerBox`
Expected: PASS (전체)

- [ ] **Step 6: 전체 프론트 테스트/타입체크/린트**

Run: `cd frontend && npm run test && npm run typecheck && npm run lint`
Expected: 에러 없음

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/query/NaturalLanguageAnswerBox.tsx frontend/src/components/query/NaturalLanguageAnswerBox.test.tsx frontend/src/screens/Dashboard.tsx
git commit -m "Feat: AI 정리 답변에 시각화 렌더링 배선"
```

---

## Task 8: 수동 확인 (dev 서버)

**Files:** 없음(코드 변경 없음, 수동 검증만)

- [ ] **Step 1: 백엔드 dev 서버 기동 확인**

Run: `cd backend && venv/Scripts/python.exe -m uvicorn main:app --reload`

- [ ] **Step 2: 프론트 dev 서버 기동**

Run: `cd frontend && npm run dev`

- [ ] **Step 3: 막대그래프 케이스 확인**

브라우저에서 로그인 후 "판매량이 가장 많은 완제품 상위 5개를 알려줘."를 질의. AI 정리 답변 박스 위쪽에 가로 막대그래프가 뜨는지, 마크다운 답변에 표가 중복으로 나오지 않는지 확인. 라이트/다크 테마 모두 전환해 색상이 자연스러운지 확인.

- [ ] **Step 4: KPI 케이스 확인**

"Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘."를 질의. KPI 카드 2개(정가/표준원가)가 뜨는지 확인.

- [ ] **Step 5: 시각화 없는 케이스 확인**

"HL Mountain Frame - Black, 38의 재고 위치와 위치별 수량을 알려줘." 같은, 텍스트 컬럼이 여러 개인 질의를 실행해 지금처럼 표/텍스트로만 나오고 차트 자리가 비지 않는지 확인.

- [ ] **Step 6: 대화기록 재조회 확인**

Task 4의 Step 9(공유 DB `ALTER TABLE`)가 이미 완료된 상태라면, 질의 후 대화기록에서 같은 항목을 다시 열어 차트가 동일하게 보이는지 확인. 아직 `ALTER TABLE`을 실행하지 않았다면 이 스텝은 건너뛰고, 완료 후 별도로 확인한다.
