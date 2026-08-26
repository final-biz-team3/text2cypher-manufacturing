import json
import logging
import os
from collections.abc import Callable
from typing import Any

from orchestrator.planning import parse_execution_plan
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)


class RoutePlanError(ValueError):
    """검증에 실패한 모델 원문을 진단 artifact까지 전달한다."""

    def __init__(self, message: str, raw_response: str) -> None:
        super().__init__(message)
        self.raw_response = raw_response


_SYSTEM_PROMPT = """당신은 제조 데이터 질의 라우터입니다.
사용자 질문과 확인된 entity를 보고 어떤 Tool을 실행해야 하는지 결정합니다.
반드시 아래 Tool 중에서만 선택하고 JSON 객체로 반환합니다.

Tool 목록:
- sql: 수치 조회, 집계, 재고 계산, 가격, 수량, 날짜 비교가 필요한 질의
- graph: 제품-부품-공급업체-공정 간 다단계 관계 탐색이 필요한 질의

예시:
Q: "LL Road Frame의 정가와 표준원가를 알려줘."
entity: {"productId": 680}
A: {"tool_plan":["sql"],"subqueries":[{"id":"sql_product_cost","tool":"sql","question":"LL Road Frame의 정가와 표준원가를 알려줘.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}

Q: "부품 Blade를 사용하는 완제품을 최대 4단계까지 알려줘."
entity: {"productId": 316}
A: {"tool_plan":["graph"],"subqueries":[{"id":"graph_impact","tool":"graph","question":"부품 Blade를 사용하는 완제품 경로를 최대 4단계까지 조회한다.","dependsOn":[],"requiredOutputs":[],"joinKeys":[]}]}

Q: "공급업체 Cycling Master가 공급을 중단하면 영향받는 완제품과 현재 부품 재고를 알려줘."
entity: {"supplierId": 52}
A: {"tool_plan":["graph","sql"],"subqueries":[{"id":"graph_impact","tool":"graph","question":"활성 공급업체 Cycling Master의 공급 부품과 영향 완제품 경로를 조회한다.","dependsOn":[],"requiredOutputs":["componentId"],"joinKeys":["componentId"]},{"id":"sql_stock","tool":"sql","question":"앞 단계에서 확인한 부품들의 현재 재고를 조회한다.","dependsOn":["graph_impact"],"inputBindings":{"componentIds":"graph_impact.componentId"},"requiredOutputs":["componentId"],"joinKeys":["componentId"]}]}

규칙:
- 단일 SQL/GRAPH 질문도 subquery를 정확히 1개 만들고 question에 원래 질문의 의미를 보존한다.
- 복합 질문은 데이터 소스의 책임별로 나누고 dependsOn, inputBindings, requiredOutputs, joinKeys를 명시한다.
- requiredOutputs에는 다른 단계로 전달하거나 최종 결합에 실제로 필요한 필드만 쓴다. 단일 질의처럼 전달·결합이 없으면 빈 배열로 둔다.
- HYBRID의 전달 필드와 최종 결합 키는 해당 단계의 requiredOutputs와 joinKeys 둘 다에 넣는다.
- 선행 결과가 필요하지 않은 두 단계는 dependsOn을 빈 배열로 둔다.
- id는 sql_stock, graph_impact처럼 책임을 나타내며 질문에 없는 RQ 번호를 사용하지 않는다.
- inputBindings 값은 반드시 "선행단계ID.출력필드" 형식이다.
- tool_plan은 실제 의존 실행 순서로 쓰고 각 도구는 한 번만 포함한다.

설명이나 Markdown 없이 JSON 객체만 반환한다."""


def make_route_query_node(openai_client: Any) -> Callable[[OrchestratorState], dict]:
    def route_query(state: OrchestratorState) -> dict:
        entity_json = json.dumps(state.get("entity"), ensure_ascii=False)
        user_content = f"Q: {state['query']}\nentity: {entity_json}\nA:"

        response = openai_client.chat.completions.create(
            model=os.environ["OPENAI_MODEL"],
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not isinstance(content, str):
            raise ValueError("route_query가 빈 응답을 반환했습니다.")
        try:
            plan = parse_execution_plan(content, state["query"])
        except ValueError as exc:
            raise RoutePlanError(str(exc), content) from exc
        logger.info(
            "route_query: query=%r -> tool_plan=%s subqueries=%s",
            state["query"],
            plan["tool_plan"],
            [item["id"] for item in plan["subqueries"]],
        )
        return dict(plan)

    return route_query
