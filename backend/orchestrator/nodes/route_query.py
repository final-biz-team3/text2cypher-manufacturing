import json
import logging
import os
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from orchestrator.numeric_literals import normalized_numeric_literals
from orchestrator.planning import (
    DEFAULT_SHARED_JOIN_ALIASES,
    SUPPORTED_TOOLS,
    parse_route_draft,
    route_draft_json_schema,
)
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)


def _numeric_literal_counts(value: str) -> Counter[str]:
    return Counter(normalized_numeric_literals(value))


def _entity_numbers_mentioned_in_query(
    query: str, entity: object | None
) -> Counter[str]:
    """질문에 실제 이름 문자열로 등장한 entity 숫자만 한 번 제외한다."""
    values: list[object]
    if isinstance(entity, list):
        values = entity
    else:
        values = [entity]
    counts: Counter[str] = Counter()
    for value in values:
        if not isinstance(value, dict):
            continue
        for field_value in value.values():
            if isinstance(field_value, str) and field_value and field_value in query:
                counts.update(_numeric_literal_counts(field_value))
    return counts


def _validate_numeric_condition_preservation(
    query: str, entity: object | None, plan: Mapping[str, Any]
) -> None:
    """entity 이름을 제외한 원문 숫자가 하위 질문에서 사라지면 fail-closed한다."""
    required = _numeric_literal_counts(query)
    required.subtract(_entity_numbers_mentioned_in_query(query, entity))
    required = +required
    planned_text = " ".join(
        str(subquery.get("question", "")) for subquery in plan.get("subqueries", [])
    )
    missing = required - _numeric_literal_counts(planned_text)
    if missing:
        raise ValueError("원본 질문의 숫자 조건이 하위 질의에서 누락되었습니다.")


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


class RoutePlanError(ValueError):
    """검증 실패 정보와 모델 응답 원문을 함께 보존한다."""

    def __init__(
        self,
        message: str,
        raw_response: str,
        tool_plan: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.tool_plan = (
            tool_plan if tool_plan is not None else _recover_tool_plan(raw_response)
        )


_SYSTEM_PROMPT = """당신은 제조 데이터 질의 라우터입니다.
사용자 질문과 확인된 entity를 보고 데이터 소스별 책임과 실행 순서를 결정합니다.

Tool 목록:
- sql: 재고, 가격, 비용, 수량, 폐기량, scalar 상태와 집계
- graph: BOM 경로, 영향 관계, 공통 부품, 공급 관계와 공정 경로

canonical source ownership:
- 관계 데이터가 SQL에 중복돼도 BOM·공급·공정 관계 탐색은 graph가 소유한다.
- scalar가 graph에 복제돼도 재고·비용·폐기량과 집계는 sql이 소유한다.
- BOM 경로 edge의 quantityPerAssembly는 graph가 소유한다.
- 양쪽 사실이 모두 필요하면 HYBRID 계획을 만든다.
- 공급업체별 서로 다른 공급 제품 수와 작업장별 서로 다른 작업지시 수처럼
  관계를 펼치지 않는 순위·집계는 sql이 소유한다.
- 공급업체 쌍·공동 공급 부품은 공급 관계 집계이므로 graph만 사용한다.
- 특정 작업장을 거친 제품은 graph에서 DISTINCT 제품을 찾고, 그 제품의 모든 작업지시 재고·폐기 집계가 필요하면 productId를 SQL로 전달한다. 이 경우 workOrderId를 전달하지 않는다.
- 작업지시의 폐기 scalar와 공정·작업장이 함께 필요하면 workOrderId로 결합하는
  독립 SQL + GRAPH 계획을 만든다.
- 공급 중단 영향 부품의 재고는 componentId를 SQL componentIds로 전달한다.
- 제품명에 포함된 숫자는 크기·모델명의 일부이며 생산 수량이 아니다. 생산 수량은
  "개", "대" 같은 단위와 연결된 숫자 또는 수사에서만 읽는다. 예를 들어
  "..., 58 열 개"의 생산 수량은 58이 아니라 10이다.
- 최하위 BOM 부품 subquery에는 부품별 최소 깊이 조회 의미를 보존한다.

예시:
1. 단일 SQL:
Q: "완제품 Aurora Frame의 현재 재고와 표준원가를 알려줘."
entity: {"productId": 7001, "productName": "Aurora Frame"}
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_inventory_cost","tool":"sql","question":"완제품 Aurora Frame의 현재 재고와 표준원가를 조회한다.","dependsOn":[],"joinKeys":[],"inputBindings":{}}],"resultTransform":null}

2. 독립 SQL + GRAPH:
Q: "활성 공급업체 수와 완제품 Nova Bike의 BOM 경로를 함께 알려줘."
entity: {"productId": 7002, "productName": "Nova Bike"}
A: {"tool_plan":["sql","graph"],"subqueries":[{"id":"sql_active_supplier_count","tool":"sql","question":"현재 활성 공급업체 수를 집계한다.","dependsOn":[],"joinKeys":[],"inputBindings":{}},{"id":"graph_bom_paths","tool":"graph","question":"완제품 Nova Bike에서 부품까지의 BOM 경로를 조회한다.","dependsOn":[],"joinKeys":[],"inputBindings":{}}],"resultTransform":null}

3. GRAPH 결과를 SQL filter로 전달하는 의존 HYBRID:
Q: "완제품 Summit Bike를 12개 만들 때 부족한 외부 구매 부품과 공급업체를 알려줘."
entity: {"productId": 7003, "productName": "Summit Bike"}
A: {"tool_plan":["graph","sql"],"subqueries":[{"id":"graph_bom_supply","tool":"graph","question":"완제품 Summit Bike의 유효 BOM 경로별 수량 계수와 활성 공급업체를 조회한다.","dependsOn":[],"joinKeys":["componentId"],"inputBindings":{}},{"id":"sql_component_stock","tool":"sql","question":"앞 단계의 componentId별 makeFlag와 현재 재고를 조회한다.","dependsOn":["graph_bom_supply"],"joinKeys":["componentId"],"inputBindings":{"componentIds":"graph_bom_supply.componentId"}}],"resultTransform":{"type":"bom_shortage_v1","productionQty":12}}

4. 공급 중단 영향과 재고:
Q: "Cobalt Works 공급 중단 시 영향 부품·완제품과 재고를 알려줘."
A: {"tool_plan":["graph","sql"],"subqueries":[{"id":"graph_supplier_impact","tool":"graph","question":"Cobalt Works 공급 부품과 그 부품이 쓰이는 완제품 경로를 조회한다.","dependsOn":[],"joinKeys":["componentId"],"inputBindings":{}},{"id":"sql_component_stock","tool":"sql","question":"앞 단계 componentId별 현재 재고를 조회한다.","dependsOn":["graph_supplier_impact"],"joinKeys":["componentId"],"inputBindings":{"componentIds":"graph_supplier_impact.componentId"}}],"resultTransform":null}

규칙:
- 단일 SQL/GRAPH 질문도 subquery를 정확히 1개 만들고 question에 원래 질문의 의미를 보존한다.
- 복합 질문은 데이터 소스의 책임별로 나누고 dependsOn, inputBindings, joinKeys를 명시한다.
- 원문에 명시된 최대 깊이, 상위 개수, 생산 수량 같은 수치 제약은 담당 subquery의
  question 또는 resultTransform에 빠짐없이 보존한다. 원문에 없는 수치 제약은 만들지 않는다.
- tool_plan에 포함된 도구마다 subquery를 정확히 하나만 만들고 같은 도구를 나누지 않는다.
- requiredOutputs는 이후 schema-aware planner가 결정하므로 생성하거나 추측하지 않는다.
- joinKeys와 inputBindings source는 제공된 schema identity alias만 사용한다.
- 선행 결과가 필요하지 않은 두 단계는 dependsOn을 빈 배열로 둔다.
- 단일 source의 joinKeys는 빈 배열이다. 질문의 filter ID를 join key로 쓰지 않는다.
- 독립 HYBRID도 실제로 두 결과의 같은 entity 행을 결합할 때만 양쪽 joinKeys를
  지정한다.
- 작업지시별 폐기량은 작업지시 행의 scalar 조회이므로 여러 작업지시를 합치는
  집계로 표현하지 않는다.
- id는 sql_stock, graph_impact처럼 책임을 나타내며 질문에 없는 RQ 번호를 사용하지 않는다.
- inputBindings 값은 반드시 "선행단계ID.출력필드" 형식이다.
- inputBindings에서 참조한 출력필드는 producer와 consumer 양쪽 joinKeys에 모두
  같은 alias로 명시한다. JSON을 반환하기 전에 양쪽 배열을 확인한다.
- 한 binding 문자열에 여러 source를 합치지 않는다. 공급 중단 재고 조회는 영향
  완제품이 아니라 직접 공급 부품의 componentId 하나만 전달한다.
- tool_plan은 실제 의존 실행 순서로 쓰고 각 도구는 한 번만 포함한다.
- resultTransform은 위 부족량 계산 계약에 정확히 해당할 때만 bom_shortage_v1을 사용하고, 나머지는 null이다.
- 원본 질문의 필터, 비교 조건, 수량, 기간, 상위 N 같은 숫자 조건을 하위 질문에 빠짐없이 그대로 보존한다.
"""


def make_route_query_node(
    openai_client: Any,
    *,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
    shared_join_aliases: frozenset[str] = DEFAULT_SHARED_JOIN_ALIASES,
) -> Callable[[OrchestratorState], Any]:
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "manufacturing_route_draft",
            "strict": True,
            "schema": route_draft_json_schema(shared_join_aliases),
        },
    }

    async def route_query(state: OrchestratorState) -> dict:
        entity_json = json.dumps(state.get("entity"), ensure_ascii=False)
        user_content = f"Q: {state['query']}\nentity: {entity_json}\nA:"

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        last_content = ""
        last_error: ValueError | None = None
        raw_route_draft: dict[str, Any] | None = None
        for attempt in range(2):
            response = await openai_client.chat.completions.create(
                model=os.environ["OPENAI_MODEL"],
                messages=messages,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
            )
            content = response.choices[0].message.content
            last_content = content if isinstance(content, str) else ""
            try:
                if not isinstance(content, str):
                    raise ValueError("route_query가 빈 응답을 반환했습니다.")
                raw_document = json.loads(content)
                plan = parse_route_draft(
                    content,
                    state["query"],
                    shared_join_aliases=shared_join_aliases,
                    # 첫 응답은 원본 계약 자체가 완전해야 한다. 재시도에서도
                    # 누락되면 production 가용성을 위해 기존 deterministic
                    # recovery를 적용하되 rawRouteDraft에는 원문을 남긴다.
                    recover_missing_binding_join_keys=attempt > 0,
                )
                _validate_numeric_condition_preservation(
                    state["query"], state.get("entity"), plan
                )
                raw_route_draft = (
                    raw_document if isinstance(raw_document, dict) else None
                )
                break
            except ValueError as exc:
                last_error = exc
                if attempt == 1:
                    raise RoutePlanError(str(exc), last_content) from exc
                messages = [
                    *messages,
                    {"role": "assistant", "content": last_content},
                    {
                        "role": "user",
                        "content": (
                            "위 실행 계획이 다음 검증을 통과하지 못했습니다: "
                            f"{exc}\n규칙에 맞는 JSON 객체 전체를 다시 생성하세요."
                        ),
                    },
                ]
        else:  # pragma: no cover - 두 번의 반복은 성공 또는 예외로만 끝난다.
            assert last_error is not None
            raise RoutePlanError(str(last_error), last_content)
        logger.info(
            "route_query: query=%r -> tool_plan=%s subqueries=%s",
            state["query"],
            plan["tool_plan"],
            [item["id"] for item in plan["subqueries"]],
        )
        result: dict[str, Any] = {
            "tool_plan": plan["tool_plan"],
            "routeDraft": dict(plan),
            "resultTransform": plan.get("resultTransform"),
        }
        if raw_route_draft is not None:
            result["rawRouteDraft"] = raw_route_draft
        return result

    return route_query
