import json
import logging
import os
from collections.abc import Callable
from typing import Any

from orchestrator.planning import (
    EXECUTION_PLAN_JSON_SCHEMA,
    SUPPORTED_TOOLS,
    parse_execution_plan,
)
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)


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

예시:
1. 단일 SQL:
Q: "완제품 Aurora Frame의 현재 재고와 표준원가를 알려줘."
entity: {"productId": 7001, "productName": "Aurora Frame"}
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_inventory_cost","tool":"sql","question":"완제품 Aurora Frame의 현재 재고와 표준원가를 조회한다.","dependsOn":[],"requiredOutputs":["productId","productName","actualStock","standardCost"],"joinKeys":[],"inputBindings":{}}]}

2. 독립 SQL + GRAPH:
Q: "활성 공급업체 수와 완제품 Nova Bike의 BOM 경로를 함께 알려줘."
entity: {"productId": 7002, "productName": "Nova Bike"}
A: {"tool_plan":["sql","graph"],"subqueries":[{"id":"sql_active_supplier_count","tool":"sql","question":"현재 활성 공급업체 수를 집계한다.","dependsOn":[],"requiredOutputs":["activeSupplierCount"],"joinKeys":[],"inputBindings":{}},{"id":"graph_bom_paths","tool":"graph","question":"완제품 Nova Bike에서 부품까지의 BOM 경로를 조회한다.","dependsOn":[],"requiredOutputs":["finishedProductId","finishedProductName","componentId","componentName","depth","pathProductIds","quantityPerAssembly"],"joinKeys":[],"inputBindings":{}}]}

3. GRAPH 결과를 SQL filter로 전달하는 의존 HYBRID:
Q: "완제품 Summit Bike의 BOM 부품별 현재 재고를 알려줘."
entity: {"productId": 7003, "productName": "Summit Bike"}
A: {"tool_plan":["graph","sql"],"subqueries":[{"id":"graph_components","tool":"graph","question":"완제품 Summit Bike의 BOM 부품과 경로를 조회한다.","dependsOn":[],"requiredOutputs":["finishedProductId","finishedProductName","componentId","componentName","depth","pathProductIds","quantityPerAssembly"],"joinKeys":["componentId"],"inputBindings":{}},{"id":"sql_stock","tool":"sql","question":"앞 단계의 componentId별 현재 재고를 조회한다.","dependsOn":["graph_components"],"requiredOutputs":["componentId","actualStock"],"joinKeys":["componentId"],"inputBindings":{"componentIds":"graph_components.componentId"}}]}

규칙:
- 단일 SQL/GRAPH 질문도 subquery를 정확히 1개 만들고 question에 원래 질문의 의미를 보존한다.
- 복합 질문은 데이터 소스의 책임별로 나누고 dependsOn, inputBindings, requiredOutputs, joinKeys를 명시한다.
- requiredOutputs는 해당 subquery가 반환해야 하는 전체 canonical output alias이며 절대 비워 두지 않는다.
- HYBRID의 전달 필드와 최종 결합 키는 해당 단계의 requiredOutputs와 joinKeys 둘 다에 넣는다.
- 선행 결과가 필요하지 않은 두 단계는 dependsOn을 빈 배열로 둔다.
- id는 sql_stock, graph_impact처럼 책임을 나타내며 질문에 없는 RQ 번호를 사용하지 않는다.
- inputBindings 값은 반드시 "선행단계ID.출력필드" 형식이다.
- tool_plan은 실제 의존 실행 순서로 쓰고 각 도구는 한 번만 포함한다.
"""

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "manufacturing_execution_plan",
        "strict": True,
        "schema": EXECUTION_PLAN_JSON_SCHEMA,
    },
}


# OpenAI 클라이언트를 주입받은 route_query 노드 함수를 생성
def make_route_query_node(openai_client: Any) -> Callable[[OrchestratorState], Any]:
    async def route_query(state: OrchestratorState) -> dict:
        # 질의 원문 + 확정된 entity를 few-shot 프롬프트의 입력 형식으로 구성
        entity_json = json.dumps(state.get("entity"), ensure_ascii=False)
        user_content = f"Q: {state['query']}\nentity: {entity_json}\nA:"

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        last_content = ""
        last_error: ValueError | None = None
        for attempt in range(2):
            response = await openai_client.chat.completions.create(
                model=os.environ["OPENAI_MODEL"],
                messages=messages,
                response_format=_RESPONSE_FORMAT,
            )
            content = response.choices[0].message.content
            last_content = content if isinstance(content, str) else ""
            try:
                if not isinstance(content, str):
                    raise ValueError("route_query가 빈 응답을 반환했습니다.")
                plan = parse_execution_plan(content, state["query"])
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
        return dict(plan)

    return route_query
