"""SQL/GRAPH/HYBRID 중 실행할 Tool을 결정하는 route_query 노드를 정의한다."""

import json
import os
from collections.abc import Callable
from typing import Any

from orchestrator.state import OrchestratorState

_SYSTEM_PROMPT = """당신은 제조 데이터 질의 라우터입니다.
사용자 질문과 확인된 entity를 보고 어떤 Tool을 실행해야 하는지 결정합니다.
반드시 아래 Tool 중에서만 선택하고, JSON 배열로 반환합니다.

Tool 목록:
- sql: 수치 조회, 집계, 재고 계산, 가격, 수량, 날짜 비교가 필요한 질의
- graph: 제품-부품-공급업체-공정 간 다단계 관계 탐색이 필요한 질의

예시:
Q: "LL Road Frame의 정가와 표준원가를 알려줘."
entity: {"productId": 680}
A: ["sql"]

Q: "부품 Blade를 사용하는 완제품을 최대 4단계까지 알려줘."
entity: {"productId": 316}
A: ["graph"]

Q: "공급업체 Cycling Master가 공급을 중단하면 영향받는 완제품과 현재 부품 재고를 알려줘."
entity: {"supplierId": 52}
A: ["sql", "graph"]

반환값은 Tool 이름의 JSON 배열만 포함한다. 설명이나 이유는 포함하지 않는다."""


def make_route_query_node(openai_client: Any) -> Callable[[OrchestratorState], dict]:
    """OpenAI 클라이언트를 주입받은 route_query 노드 함수를 만든다."""

    def route_query(state: OrchestratorState) -> dict:
        entity_json = json.dumps(state.get("entity"), ensure_ascii=False)
        user_content = f"Q: {state['query']}\nentity: {entity_json}\nA:"

        response = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        tool_plan = json.loads(response.choices[0].message.content)
        return {"tool_plan": tool_plan}

    return route_query
