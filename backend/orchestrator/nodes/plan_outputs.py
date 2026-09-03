"""Lossless schema-aware output planning between routing and execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, cast

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from orchestrator.output_catalog import OutputCatalog
from orchestrator.planning import (
    RouteSubquery,
    Subquery,
    validate_result_transform,
    validate_subqueries,
)
from orchestrator.semantic_catalog import ToolName
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """당신은 제조 데이터 질의의 schema-aware output planner입니다.
라우팅은 이미 확정됐습니다. 각 subquery가 답으로 반환해야 하는 alias를 제공된
catalog에서 선택하세요.

공통 규칙:
- original question만 출력 의미, 결과 grain과 표시 필드의 권위 있는 근거입니다.
  clarified interpretation은 짧거나 생략된 표현에서 원문에 이미 함의된 세부 필드를
  풀어 쓴 참고 문맥일 뿐 새 요구사항이 아닙니다. source responsibility는 original
  question이 요구한 출력 중 이 source가 생산할 부분만 좁힙니다.
- 원질문이 요구한 graph 관계의 provenance와 cardinality를 보존하는 데 필요한
  source-local path/depth 구조는 source responsibility와 catalog를 근거로 직접
  선택할 수 있습니다. 그 외에는 두 보조 문맥만으로 original question에 없는 표시
  필드, filter, grain, 계산 또는 관계 속성을 추가하지 않습니다.
- displayEntities에는 결과 행의 값·측정·집계·관계를 식별하는 엔티티 역할을
  넣습니다. 특정 엔티티의 상세, 측정값 또는 그 엔티티별 행을 묻는다면 질문에서
  고정된 anchor라도 결과 문맥과 grain을 식별하므로 포함합니다. 결과 행의 문맥이나
  grain에 참여하지 않고 검색 범위만 제한하는 filter entity만 제외합니다.
- 필터나 정렬에만 사용하는 필드는 결과로 요청된 경우에만 선택합니다.
- alias의 source ownership을 바꾸거나 새 alias를 만들지 않습니다.
- 같은 role이나 alias를 중복하지 않고 결과에 적합한 순서로 반환합니다.
- join key와 downstream binding source는 구조 finalizer가 추가합니다.
- entity role catalog와 output catalog의 kind, canonical meaning, terms, paths,
  operation, inputs를 근거로 판단합니다. 특정 질의 유형의 고정 출력 묶음을
  추측하지 않습니다.
- 불확실할 때는 requiredOutputs의 완전성과 기존 직접 선택을 우선합니다.
- 평가 ID, fixture 또는 정답 query를 추론하거나 언급하지 않습니다.
"""

_SQL_SYSTEM_RULES = """
SQL 규칙:
- requiredOutputs에는 질문 결과에 표시해야 하는 identity, name, physical scalar,
  aggregate, derived value를 완전하게 직접 선택합니다. displayEntities가 같은
  key와 label을 보완하더라도 requiredOutputs의 직접 선택을 생략하지 않습니다.
- 특정 엔티티나 분류의 속성, 측정값, 집계를 설명하는 결과라면 그 엔티티가
  필터에서 고정됐더라도 결과 문맥을 식별하는 key와 label을 포함합니다.
- 전체 결과가 특정 엔티티 문맥이 없는 하나의 scalar 집계일 때만
  displayEntities를 비울 수 있습니다.
- 파생값이 여러 값의 비교나 차이이고 그 구성값들이 질문의 비교 대상을 이루면,
  catalog의 inputs를 근거로 파생값과 표시할 구성값을 함께 선택합니다. 단지
  inputs에 있다는 이유만으로 질문과 무관한 계산 입력을 모두 노출하지 않습니다.
- 원본 행 grain의 물리값은 physical alias를 유지합니다. 여러 원본 행을 결과
  grain으로 실제 집계할 때만 aggregate alias를 선택하며, 정렬이나 top-N만으로
  물리값을 aggregate alias로 바꾸지 않습니다.
- displayEntities는 requiredOutputs를 대체하지 않습니다. compiler는 catalog에
  선언된 key와 label만 보완하며 계산값이나 계산 입력을 대신 추론하지 않습니다.
- 위치별 재고처럼 물리적 보관 위치가 결과 grain이면 location identity만으로는
  위치가 완전하지 않습니다. catalog에 있는 shelf와 bin을 포함해 실제 보관 좌표와
  그 위치의 quantity를 requiredOutputs에서 직접 선택합니다.
"""

_GRAPH_SYSTEM_RULES = """
GRAPH 규칙:
- requiredOutputs에는 질문이 직접 요구하는 physical scalar, aggregate, derived
  value와 구조 증거(path, depth)를 직접 선택합니다. displayEntities가 보완하는
  entity key와 label은 중복 선택하지 않습니다.
- displayEntities의 순서는 결과에서 엔티티를 표시할 순서일 뿐이며 ordered path
  projection의 방향 계약이 아닙니다. 경로 방향은 original question의 탐색 의미와
  graph schema의 관계 방향을 함께 보고 Cypher 생성 단계에서 결정합니다.
- 전체 결과가 하나의 scalar 집계이면 displayEntities를 비울 수 있습니다.
- requiredOutputs는 직접 값이 없으면 비울 수 있지만 requiredOutputs와
  displayEntities를 동시에 비우지는 않습니다.
- compiler는 displayEntities에 catalog가 선언한 key와 label만 보완합니다.
- 가변 길이 관계로 연결된 anchor와 destination을 함께 반환하면, 원문이 서로 다른
  endpoint만으로 축약하라고 명시하지 않는 한 서로 다른 전체 경로가 결과 행을
  구분하는 path-preserving traversal입니다. 짧은 endpoint 목록 표현이어도 개별
  경로의 provenance와 cardinality를 보존하도록 depth와 ordered ID path를
  requiredOutputs에 직접 선택합니다. 개별 경로의 노드 순서나 계층·트리의
  조상-자손 구조가 사람이 읽는 결과 표현의 일부이면 ordered name path도
  선택합니다. endpoint 목록만 요구하면 name path는 추가하지 않습니다. 최대 탐색
  깊이는 filter 범위이므로 그 자체를 path 출력 요청으로 해석하지 않습니다.
- 둘 이상의 anchor가 같은 destination을 공유하는 교집합·비교 결과이고 결과 행이
  공통 destination별로 축약되면, 각 anchor에서 destination까지의 minimum path
  length를 각각 requiredOutputs에 직접 선택합니다. 질문이 개별 경로 자체를
  요구하지 않으면 ordered path projection은 추가하지 않습니다.
- 관계 경로를 결과 행으로 반환하면 고정 여부와 무관하게 그 경로 결과를
  식별해야 하는 endpoint 역할을 displayEntities에 포함합니다.
- 원문이 순서값 자체를 결과로 요구하면 ORDER BY 의미로만 처리하지 말고 catalog의
  대응 physical scalar를 requiredOutputs에 포함합니다.
- role alias는 물리 MATCH 방향이 아니라 catalog에 선언된 질문의 의미 역할을
  따릅니다. 물리 관계와 방향은 Cypher 생성 단계가 graph schema에서 결정합니다.
"""


class OutputPlanningError(ValueError):
    """Keep the failed model response for diagnostics."""

    def __init__(self, message: str, raw_response: str) -> None:
        super().__init__(message)
        self.raw_response = raw_response


@dataclass(frozen=True)
class SemanticOutputPlan:
    required_outputs: tuple[str, ...]
    display_entities: tuple[str, ...]


def output_plan_json_schema(catalog: OutputCatalog, tool: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "requiredOutputs": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(catalog.allowed_aliases(tool)),
            },
        },
        "displayEntities": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(catalog.allowed_entity_roles(tool)),
            },
        },
    }
    if tool != "graph":
        properties["requiredOutputs"]["minItems"] = 1
    return {
        "type": "object",
        "properties": properties,
        "required": ["requiredOutputs", "displayEntities"],
        "additionalProperties": False,
    }


def _parse_alias_array(
    value: object,
    *,
    field_name: str,
    allowed: Iterable[str],
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(alias, str) or not alias.strip() for alias in value
    ):
        raise ValueError(f"{field_name} must be a string array")
    outputs = cast(list[str], value)
    if not allow_empty and not outputs:
        raise ValueError(f"{field_name} must be a non-empty string array")
    if len(outputs) != len(set(outputs)):
        raise ValueError(f"{field_name} contains duplicate values")
    unknown = set(outputs) - set(allowed)
    if unknown:
        raise ValueError(
            f"{field_name} contains unavailable values: " + ", ".join(sorted(unknown))
        )
    return tuple(outputs)


def _parse_output_plan(
    content: str, *, tool: str, catalog: OutputCatalog
) -> SemanticOutputPlan:
    raw = json.loads(content)
    if not isinstance(raw, dict) or set(raw) != {
        "requiredOutputs",
        "displayEntities",
    }:
        raise ValueError("output planner response has invalid fields for " + tool)
    plan = SemanticOutputPlan(
        required_outputs=_parse_alias_array(
            raw["requiredOutputs"],
            field_name="requiredOutputs",
            allowed=catalog.allowed_aliases(tool),
            allow_empty=tool == "graph",
        ),
        display_entities=_parse_alias_array(
            raw["displayEntities"],
            field_name="displayEntities",
            allowed=catalog.allowed_entity_roles(tool),
            allow_empty=True,
        ),
    )
    if tool == "graph" and not plan.required_outputs and not plan.display_entities:
        raise ValueError("requiredOutputs and displayEntities must not both be empty")
    return plan


def _ordered_union(*groups: Iterable[str]) -> list[str]:
    result: list[str] = []
    for group in groups:
        for item in group:
            if item not in result:
                result.append(item)
    return result


def compile_semantic_output_plan(
    plan: SemanticOutputPlan,
    catalog: OutputCatalog,
    *,
    tool: str,
) -> list[str]:
    """Compile direct values and explicitly selected entity identity only."""
    identity_outputs = [
        alias
        for role in plan.display_entities
        for alias in catalog.identity_projection(role, tool).display_aliases
    ]
    return _ordered_union(plan.required_outputs, identity_outputs)


def compile_graph_generator_rules(
    _plan: SemanticOutputPlan,
    catalog: OutputCatalog,
    selected_outputs: Iterable[str],
) -> list[str]:
    """Tell the generator how to resolve ordered paths without encoding role order."""
    selected_specs = [catalog.by_tool["graph"][alias] for alias in selected_outputs]
    if not any(spec.operation == "orderedPathProjection" for spec in selected_specs):
        return []
    return [
        "Determine orderedPathProjection direction from the path origin and destination "
        "expressed by the original question and source responsibility. displayEntities "
        "order, required output order, and physical MATCH direction do not define this "
        "semantic direction."
    ]


def finalize_required_outputs(
    selected_outputs: list[str],
    join_keys: list[str],
    outgoing_binding_sources: list[str],
    catalog: OutputCatalog,
    *,
    tool: str,
) -> list[str]:
    """Apply only execution-structural additions to a model's ordered selection.

    This function deliberately receives no question or entity text. It cannot infer
    business meaning, replace an alias, or manufacture an answer shape.
    """
    allowed = set(catalog.allowed_aliases(tool))
    for field_name, aliases in (
        ("selectedOutputs", selected_outputs),
        ("joinKeys", join_keys),
        ("outgoingBindingSources", outgoing_binding_sources),
    ):
        if len(aliases) != len(set(aliases)):
            raise ValueError(f"{field_name} contains duplicate aliases")
        unknown = set(aliases) - allowed
        if unknown:
            raise ValueError(
                f"{tool} source does not own {field_name}: "
                + ", ".join(sorted(unknown))
            )
    return _ordered_union(selected_outputs, join_keys, outgoing_binding_sources)


def _outgoing_binding_outputs(
    route_subqueries: list[RouteSubquery],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {item["id"]: [] for item in route_subqueries}
    for item in route_subqueries:
        for source in item.get("inputBindings", {}).values():
            source_id, alias = source.split(".", 1)
            if source_id not in result:
                raise ValueError(
                    f"binding references unknown producer subquery {source_id!r}"
                )
            if alias not in result[source_id]:
                result[source_id].append(alias)
    return result


def _with_required_outputs(
    route_subquery: RouteSubquery,
    required_outputs: list[str],
    generator_rules: list[str] | None = None,
) -> Subquery:
    item: Subquery = {
        "id": route_subquery["id"],
        "tool": route_subquery["tool"],
        "question": route_subquery["question"],
        "dependsOn": route_subquery["dependsOn"],
        "requiredOutputs": required_outputs,
        "joinKeys": route_subquery["joinKeys"],
    }
    if route_subquery.get("inputBindings"):
        item["inputBindings"] = dict(route_subquery["inputBindings"])
    if generator_rules:
        item["generatorRules"] = list(generator_rules)
    return item


async def _select_output_plan(
    *,
    openai_client: Any,
    route_subquery: RouteSubquery,
    original_question: str,
    planning_context: tuple[str, str] | None,
    entity: object | None,
    catalog: OutputCatalog,
    reasoning_effort: ReasoningEffort,
) -> SemanticOutputPlan:
    tool = route_subquery["tool"]
    user_content = f"source: {tool}\noriginal question: {original_question}\n"
    if planning_context is not None:
        context_label, context_text = planning_context
        user_content += f"{context_label}: {context_text}\n"
    user_content += (
        f"resolved entity: {json.dumps(entity, ensure_ascii=False)}\n"
        f"join keys: {json.dumps(route_subquery['joinKeys'])}\n"
        "entity role catalog:\n"
        f"{catalog.describe_entity_roles(tool)}\n"
    )
    user_content += f"output catalog:\n{catalog.describe(tool)}\nJSON:"
    source_rules = _GRAPH_SYSTEM_RULES if tool == "graph" else _SQL_SYSTEM_RULES
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT + source_rules},
        {"role": "user", "content": user_content},
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": f"{tool}_required_outputs",
            "strict": True,
            "schema": output_plan_json_schema(catalog, tool),
        },
    }
    last_content = ""
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
                raise ValueError("output planner returned an empty response")
            return _parse_output_plan(content, tool=tool, catalog=catalog)
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt == 1:
                raise OutputPlanningError(str(exc), last_content) from exc
            messages = [
                *messages,
                {"role": "assistant", "content": last_content},
                {
                    "role": "user",
                    "content": (
                        "The output plan failed structural validation: "
                        f"{exc}\nReturn the complete JSON object using only catalog "
                        "roles and aliases. Keep requiredOutputs complete."
                    ),
                },
            ]
    raise AssertionError("output planner retry loop did not terminate")


def make_plan_outputs_node(
    openai_client: Any,
    catalog: OutputCatalog,
    *,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
) -> Callable[[OrchestratorState], Any]:
    """Create an output planner with at most one corrective retry per subquery."""

    async def plan_outputs(state: OrchestratorState) -> dict[str, Any]:
        route_draft = state.get("routeDraft")
        if not isinstance(route_draft, dict):
            raise ValueError("routeDraft is required for output planning")
        raw_subqueries = route_draft.get("subqueries")
        if not isinstance(raw_subqueries, list):
            raise ValueError("routeDraft.subqueries must be an array")
        route_subqueries: list[RouteSubquery] = raw_subqueries
        raw_route_draft = state.get("rawRouteDraft")
        raw_questions: dict[str, str] = {}
        if isinstance(raw_route_draft, dict):
            raw_items = raw_route_draft.get("subqueries")
            if isinstance(raw_items, list):
                for raw_item in raw_items:
                    if not isinstance(raw_item, dict):
                        continue
                    raw_id = raw_item.get("id")
                    raw_question = raw_item.get("question")
                    if isinstance(raw_id, str) and isinstance(raw_question, str):
                        raw_questions[raw_id] = raw_question
        outgoing = _outgoing_binding_outputs(route_subqueries)
        transform = state.get("resultTransform")

        async def plan_one(route_subquery: RouteSubquery) -> Subquery:
            tool = cast(ToolName, route_subquery["tool"])
            planning_context: tuple[str, str] | None
            if len(route_subqueries) > 1:
                planning_context = (
                    "source responsibility",
                    route_subquery["question"],
                )
            else:
                raw_question = raw_questions.get(route_subquery["id"])
                planning_context = (
                    ("clarified interpretation", raw_question)
                    if raw_question is not None and raw_question != state["query"]
                    else None
                )
            if isinstance(transform, dict):
                transform_type = transform.get("type")
                if not isinstance(transform_type, str):
                    raise ValueError("resultTransform type is required")
                spec = catalog.transform(transform_type)
                selected = list(spec.required_outputs[tool])
                generator_rules = list(spec.generator_rules[tool])
            else:
                semantic_plan = await _select_output_plan(
                    openai_client=openai_client,
                    route_subquery=route_subquery,
                    original_question=state["query"],
                    planning_context=planning_context,
                    entity=state.get("entity"),
                    catalog=catalog,
                    reasoning_effort=reasoning_effort,
                )
                selected = compile_semantic_output_plan(
                    semantic_plan,
                    catalog,
                    tool=tool,
                )
                generator_rules = (
                    compile_graph_generator_rules(
                        semantic_plan,
                        catalog,
                        selected,
                    )
                    if tool == "graph"
                    else []
                )
            outputs = finalize_required_outputs(
                selected,
                route_subquery["joinKeys"],
                outgoing[route_subquery["id"]],
                catalog,
                tool=tool,
            )
            return _with_required_outputs(
                route_subquery,
                outputs,
                generator_rules,
            )

        if len(route_subqueries) > 1 and all(
            not subquery.get("dependsOn") for subquery in route_subqueries
        ):
            planned = list(
                await asyncio.gather(*(plan_one(s) for s in route_subqueries))
            )
        else:
            planned = [await plan_one(subquery) for subquery in route_subqueries]

        validated = validate_subqueries(planned)
        validated_transform = validate_result_transform(
            transform,
            validated,
            catalog=catalog,
        )
        logger.info(
            "plan_outputs: subqueries=%s outputs=%s",
            [item["id"] for item in validated],
            [item["requiredOutputs"] for item in validated],
        )
        return {
            "subqueries": validated,
            "resultTransform": validated_transform,
        }

    return plan_outputs


__all__ = [
    "OutputPlanningError",
    "SemanticOutputPlan",
    "compile_graph_generator_rules",
    "compile_semantic_output_plan",
    "finalize_required_outputs",
    "make_plan_outputs_node",
    "output_plan_json_schema",
]
