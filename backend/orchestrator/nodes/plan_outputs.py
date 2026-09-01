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

규칙:
- requiredOutputs에는 질문에 표시해야 하는 identity, name, physical scalar,
  aggregate, derived value, path output을 완전하게 선택합니다.
- displayEntities에는 사람이 결과에서 식별해야 하는 엔티티 역할만 넣습니다.
  특정 엔티티로 필터했더라도 결과가 그 엔티티의 속성이나 측정값을 설명하면
  포함합니다. 단지 필터 범위만 제한하는 엔티티는 넣지 않습니다.
- 전체 결과가 하나의 scalar 집계이면 displayEntities를 비울 수 있습니다.
- displayEntities는 requiredOutputs를 대체하지 않습니다. compiler는 catalog에
  선언된 key와 label만 requiredOutputs 뒤에 보완하며 계산값, 계산 입력, path
  묶음을 대신 추론하지 않습니다.
- 필터나 정렬에만 사용하는 필드는 결과로 요청된 경우에만 선택합니다.
- alias의 source ownership을 바꾸거나 새 alias를 만들지 않습니다.
- 같은 role이나 alias를 중복하지 않고 결과에 적합한 순서로 반환합니다.
- join key와 downstream binding source는 구조 finalizer가 추가합니다.
- entity role catalog와 output catalog의 kind, canonical meaning, terms, paths,
  grain, operation, inputs를 근거로 판단합니다.
- 불확실할 때는 requiredOutputs의 완전성과 기존 직접 선택을 우선합니다.
- 평가 ID, fixture 또는 정답 query를 추론하거나 언급하지 않습니다.
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
    return {
        "type": "object",
        "properties": {
            "requiredOutputs": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(catalog.allowed_aliases(tool)),
                },
                "minItems": 1,
            },
            "displayEntities": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(catalog.allowed_entity_roles(tool)),
                },
            },
        },
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
        raise ValueError(
            "output planner response must contain only requiredOutputs and "
            "displayEntities"
        )
    return SemanticOutputPlan(
        required_outputs=_parse_alias_array(
            raw["requiredOutputs"],
            field_name="requiredOutputs",
            allowed=catalog.allowed_aliases(tool),
            allow_empty=False,
        ),
        display_entities=_parse_alias_array(
            raw["displayEntities"],
            field_name="displayEntities",
            allowed=catalog.allowed_entity_roles(tool),
            allow_empty=True,
        ),
    )


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
    """Add catalog identity projections without replacing model-selected aliases."""
    identity_outputs = [
        alias
        for role in plan.display_entities
        for alias in catalog.identity_projection(role, tool).display_aliases
    ]
    return _ordered_union(plan.required_outputs, identity_outputs)


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
    entity: object | None,
    catalog: OutputCatalog,
    reasoning_effort: ReasoningEffort,
) -> SemanticOutputPlan:
    tool = route_subquery["tool"]
    user_content = (
        f"source: {tool}\n"
        f"original question: {original_question}\n"
        f"subquery: {route_subquery['question']}\n"
        f"resolved entity: {json.dumps(entity, ensure_ascii=False)}\n"
        f"join keys: {json.dumps(route_subquery['joinKeys'])}\n"
        "entity role catalog:\n"
        f"{catalog.describe_entity_roles(tool)}\n"
        "output catalog:\n"
        f"{catalog.describe(tool)}\n"
        "JSON:"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
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
        outgoing = _outgoing_binding_outputs(route_subqueries)
        transform = state.get("resultTransform")

        async def plan_one(route_subquery: RouteSubquery) -> Subquery:
            tool = cast(ToolName, route_subquery["tool"])
            if isinstance(transform, dict):
                transform_type = transform.get("type")
                if not isinstance(transform_type, str):
                    raise ValueError("resultTransform type is required")
                spec = catalog.transform(transform_type)
                selected = list(spec.required_outputs[tool])
                generator_rules = list(spec.generator_rules[tool])
            else:
                generator_rules = []
                semantic_plan = await _select_output_plan(
                    openai_client=openai_client,
                    route_subquery=route_subquery,
                    original_question=state["query"],
                    entity=state.get("entity"),
                    catalog=catalog,
                    reasoning_effort=reasoning_effort,
                )
                selected = compile_semantic_output_plan(
                    semantic_plan,
                    catalog,
                    tool=tool,
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
    "compile_semantic_output_plan",
    "finalize_required_outputs",
    "make_plan_outputs_node",
    "output_plan_json_schema",
]
