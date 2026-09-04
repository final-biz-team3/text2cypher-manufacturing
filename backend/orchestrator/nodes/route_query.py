"""질문 하나를 현재 표현 가능한 SQL/Graph 기능에 맞게 라우팅한다."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from orchestrator.planning import (
    DEFAULT_SHARED_JOIN_ALIASES,
    parse_route_draft,
    route_draft_json_schema,
)
from orchestrator.semantic_catalog import QuerySemanticCatalog
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)


class RoutePlanError(ValueError):
    """통제된 진단을 위해 실패한 원본 응답만 보존한다."""

    def __init__(self, message: str, raw_response: str) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.tool_plan: list[str] | None = None


_SYSTEM_PROMPT = """당신은 제조 데이터 질의 라우터입니다.
사용자 질문과 확인된 entity를 보고 source ownership, dependency와 result
composition 구조를 한 번 결정합니다.

규칙:
- physical schema와 semantic catalog가 명시한 source에서만 사실을 조회합니다.
- 한 source당 subquery는 최대 하나이며 전체 subquery는 최대 두 개입니다.
- requiredOutputs와 tool_plan은 만들지 않습니다. tool_plan은 dependency DAG에서
  파생되고 requiredOutputs는 다음 planner가 선택합니다.
- 단일 source 질문은 subquery 하나를 만듭니다.
- 두 source가 필요하면 각 source 책임으로 질문을 나눕니다. 선행 결과 없이 실행할
  수 있으면 두 dependsOn과 inputBindings를 비웁니다.
- 후속 source가 선행 row field를 입력으로 사용할 때 dependsOn을 선언하고 각 배열을
  inputBindings 항목으로 만듭니다. 같은 dependency에서 온 여러 binding은 row index,
  중복과 NULL이 정렬된 별도 배열입니다.
- binding target은 안전한 영문 identifier입니다. sourceOutput은 producer source의
  catalog alias여야 하며 identity alias로 제한되지 않습니다.
- input binding은 데이터 전달이고 joinKeys는 최종 결과 조합입니다. binding 때문에
  joinKeys를 만들지 않습니다.
- 실제로 두 결과를 같은 identity 행으로 합칠 때만 양쪽 joinKeys에 같은 ordered
  shared identity alias를 둡니다. 그 외에는 양쪽 모두 비웁니다.
- subquery question은 원문의 filter, limit, date, quantity와 업무 의미를 보존하며
  source 책임만 좁혀 표현합니다. 원문에 없는 출력 필드, 결과 grain, 집계 차원,
  계산 방식, filter 또는 relationship 속성을 추가하지 않으며 query syntax나
  required output recipe를 쓰지 않습니다.
- resultTransform은 선언된 formal transform이 정확히 필요한 경우에만 사용합니다.
- bom_shortage_v1은 생산 수량을 입력으로 받아 Graph BOM path quantity와 SQL stock을
  검증·계산하는 formal transform입니다. 이 transform은 Graph -> SQL dependency,
  componentId join, componentIds binding 계약을 사용합니다.
- JSON 외의 설명을 반환하지 않습니다.

최소 shape 예시:
{"subqueries":[{"id":"graph_relationships","tool":"graph","question":"관계 범위를 조회한다.","dependsOn":[],"joinKeys":[],"inputBindings":[]},{"id":"sql_measures","tool":"sql","question":"scalar 측정값을 조회한다.","dependsOn":[],"joinKeys":[],"inputBindings":[]}],"resultTransform":null}
"""


def _routing_context(
    *,
    catalog: QuerySemanticCatalog | None,
    sql_schema_text: str,
    graph_schema_text: str,
) -> str:
    sections: list[str] = []
    if sql_schema_text.strip():
        sections.append("SQL physical capabilities:\n" + sql_schema_text.strip())
    if graph_schema_text.strip():
        sections.append("Graph physical capabilities:\n" + graph_schema_text.strip())
    if catalog is not None:
        sections.extend(
            (
                "SQL semantic capabilities:\n" + catalog.describe("sql"),
                "Graph semantic capabilities:\n" + catalog.describe("graph"),
            )
        )
    return "\n\n".join(sections)


def make_route_query_node(
    openai_client: Any,
    *,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
    shared_join_aliases: frozenset[str] = DEFAULT_SHARED_JOIN_ALIASES,
    catalog: QuerySemanticCatalog | None = None,
    sql_schema_text: str = "",
    graph_schema_text: str = "",
) -> Callable[[OrchestratorState], Any]:
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "manufacturing_route_draft",
            "strict": True,
            "schema": route_draft_json_schema(
                shared_join_aliases,
                catalog=catalog,
            ),
        },
    }
    context = _routing_context(
        catalog=catalog,
        sql_schema_text=sql_schema_text,
        graph_schema_text=graph_schema_text,
    )
    system_content = _SYSTEM_PROMPT + (f"\n\n{context}" if context else "")

    async def route_query(state: OrchestratorState) -> dict[str, Any]:
        user_content = json.dumps(
            {"query": state["query"], "entity": state.get("entity")},
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        last_content = ""
        plan = None
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
                    raise ValueError("route_query returned an empty response")
                raw_document = json.loads(content)
                plan = parse_route_draft(
                    content,
                    state["query"],
                    shared_join_aliases=shared_join_aliases,
                    catalog=catalog,
                )
                raw_route_draft = (
                    raw_document if isinstance(raw_document, dict) else None
                )
                if len(plan["subqueries"]) == 1:
                    # 단일 source에는 분해할 source 책임이 없다. 모델 paraphrase가
                    # 원문에 없는 출력·grain을 보태지 못하도록 실행 질문을 원문으로
                    # 결정적으로 복원하고 raw 응답은 진단용으로 별도 보존한다.
                    plan["subqueries"][0]["question"] = state["query"]
                break
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt == 1:
                    raise RoutePlanError(str(exc), last_content) from exc
                messages = [
                    *messages,
                    {"role": "assistant", "content": last_content},
                    {
                        "role": "user",
                        "content": (
                            "The route failed structural validation: "
                            f"{exc}\nReturn the complete corrected JSON object."
                        ),
                    },
                ]
        if plan is None:
            raise AssertionError("route retry loop did not terminate")
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
            "routeRepairCount": attempt,
        }
        if raw_route_draft is not None:
            result["rawRouteDraft"] = raw_route_draft
        return result

    return route_query


__all__ = ["RoutePlanError", "make_route_query_node"]
