"""Lossless schema-aware output planning between routing and execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, cast

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
라우팅은 이미 확정됐습니다. 각 subquery의 결과 엔티티, grain과 답변값을 제공된
catalog에서 선택하세요. 최종 required output alias는 compiler가 만듭니다.

규칙:
- resultEntities에는 답변 행에 반환되는 엔티티만 넣고 filter-only 엔티티는
  넣지 않습니다.
- 엔티티의 속성이나 측정값을 설명하는 행이면 그 엔티티도 resultEntities입니다.
- representation=display는 사람이 식별할 결과로 key와 label을 함께 반환하고,
  representation=reference는 관계·문맥 식별용 key만 반환합니다.
- inGrain은 그 엔티티가 결과 행의 grain을 구성하는지 나타냅니다.
- row-level 속성의 주 결과 엔티티는 representation=display, inGrain=true로
  선언합니다. 명시적 grain이나 identity가 있는 경우에는 그 선언을 따릅니다.
- grainFields에는 엔티티 key 외에 행의 grain을 구성하는 physical/path field를
  넣습니다. answerValues에는 질문에 표시할 physical scalar, aggregate, derived,
  path output을 넣습니다.
- 전체 집계처럼 한 scalar만 반환하면 resultEntities와 grainFields는 비울 수
  있습니다.
- 필터나 정렬에만 사용하는 필드는 결과로 요청된 경우에만 선택합니다.
- alias의 source ownership을 바꾸거나 새 alias를 만들지 않습니다.
- 같은 role이나 alias를 중복하지 않고 결과에 적합한 순서로 반환합니다.
- join key와 downstream binding source는 구조 finalizer가 추가합니다.
- entity role catalog와 output catalog의 kind, canonical meaning, terms, paths,
  grain, operation, inputs를 근거로 판단합니다.
- 평가 ID, fixture 또는 정답 query를 추론하거나 언급하지 않습니다.
"""


class OutputPlanningError(ValueError):
    """Keep the failed model response for diagnostics."""

    def __init__(self, message: str, raw_response: str) -> None:
        super().__init__(message)
        self.raw_response = raw_response


EntityRepresentation = Literal["display", "reference"]


@dataclass(frozen=True)
class PlannedResultEntity:
    role: str
    representation: EntityRepresentation
    in_grain: bool


@dataclass(frozen=True)
class SemanticOutputPlan:
    result_entities: tuple[PlannedResultEntity, ...]
    grain_fields: tuple[str, ...]
    answer_values: tuple[str, ...]


def output_plan_json_schema(catalog: OutputCatalog, tool: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "resultEntities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": list(catalog.allowed_entity_roles(tool)),
                        },
                        "representation": {
                            "type": "string",
                            "enum": ["display", "reference"],
                        },
                        "inGrain": {"type": "boolean"},
                    },
                    "required": ["role", "representation", "inGrain"],
                    "additionalProperties": False,
                },
            },
            "grainFields": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(catalog.allowed_aliases(tool)),
                },
            },
            "answerValues": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(catalog.allowed_aliases(tool)),
                },
            },
        },
        "required": ["resultEntities", "grainFields", "answerValues"],
        "additionalProperties": False,
    }


def _parse_alias_array(
    value: object,
    *,
    field_name: str,
    tool: str,
    catalog: OutputCatalog,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(alias, str) or not alias.strip() for alias in value
    ):
        raise ValueError(f"{field_name} must be a string array")
    aliases = cast(list[str], value)
    if len(aliases) != len(set(aliases)):
        raise ValueError(f"{field_name} contains duplicate aliases")
    unknown = set(aliases) - set(catalog.allowed_aliases(tool))
    if unknown:
        raise ValueError(
            f"{tool} output source ownership violation in {field_name}: "
            + ", ".join(sorted(unknown))
        )
    return tuple(aliases)


def _parse_output_plan(
    content: str, *, tool: str, catalog: OutputCatalog
) -> SemanticOutputPlan:
    raw = json.loads(content)
    expected_fields = {"resultEntities", "grainFields", "answerValues"}
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise ValueError(
            "output planner response must contain only resultEntities, "
            "grainFields and answerValues"
        )
    raw_entities = raw["resultEntities"]
    if not isinstance(raw_entities, list):
        raise ValueError("resultEntities must be an array")
    entities: list[PlannedResultEntity] = []
    allowed_roles = set(catalog.allowed_entity_roles(tool))
    for index, item in enumerate(raw_entities):
        if not isinstance(item, dict) or set(item) != {
            "role",
            "representation",
            "inGrain",
        }:
            raise ValueError(f"resultEntities[{index}] has an invalid shape")
        role = item["role"]
        representation = item["representation"]
        in_grain = item["inGrain"]
        if not isinstance(role, str) or role not in allowed_roles:
            raise ValueError(f"resultEntities[{index}] role is unavailable from {tool}")
        if representation not in {"display", "reference"}:
            raise ValueError(f"resultEntities[{index}] representation is unsupported")
        if not isinstance(in_grain, bool):
            raise ValueError(f"resultEntities[{index}] inGrain must be boolean")
        entities.append(
            PlannedResultEntity(
                role=role,
                representation=cast(EntityRepresentation, representation),
                in_grain=in_grain,
            )
        )
    roles = [entity.role for entity in entities]
    if len(roles) != len(set(roles)):
        raise ValueError("resultEntities contains duplicate roles")
    return SemanticOutputPlan(
        result_entities=tuple(entities),
        grain_fields=_parse_alias_array(
            raw["grainFields"],
            field_name="grainFields",
            tool=tool,
            catalog=catalog,
        ),
        answer_values=_parse_alias_array(
            raw["answerValues"],
            field_name="answerValues",
            tool=tool,
            catalog=catalog,
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
    """Compile model-selected semantics into aliases without reading question text."""
    identity_outputs = [
        alias
        for entity in plan.result_entities
        for alias in catalog.identity_projection(entity.role, tool).aliases(
            entity.representation
        )
    ]
    outputs = _ordered_union(
        identity_outputs,
        plan.grain_fields,
        plan.answer_values,
    )
    return outputs


def _validate_semantic_output_plan(
    plan: SemanticOutputPlan,
    catalog: OutputCatalog,
    *,
    tool: str,
    structural_outputs: Iterable[str],
) -> None:
    """Reject internally incomplete semantics without consulting a question oracle.

    A source step used only to produce a join or binding may legitimately have no
    answer semantics.  A row-level attribute selected for the user, however, needs
    an explicit result context: a displayed entity, a declared grain, an explicitly
    requested identity, or an execution join identity.  Aggregate/derived/path
    values may be scalar and therefore do not require an entity projection.
    """
    source = cast(ToolName, tool)
    structural = tuple(structural_outputs)
    compiled = compile_semantic_output_plan(plan, catalog, tool=source)
    if not compiled and not structural:
        raise ValueError(
            "semantic output plan has neither answer semantics nor structural outputs"
        )

    row_level_values = [
        alias
        for alias in plan.answer_values
        if catalog.by_tool[source][alias].kind in {"physical", "role"}
        and catalog.by_tool[source][alias].value_type != "identity"
    ]
    if not row_level_values:
        return

    has_display_grain_entity = any(
        entity.representation == "display" and entity.in_grain
        for entity in plan.result_entities
    )
    has_explicit_grain = bool(plan.grain_fields)
    has_requested_identity = any(
        catalog.by_tool[source][alias].value_type == "identity"
        for alias in plan.answer_values
    )
    has_structural_identity = any(
        alias in catalog.by_tool[source]
        and catalog.by_tool[source][alias].value_type == "identity"
        for alias in structural
    )
    if not (
        has_display_grain_entity
        or has_explicit_grain
        or has_requested_identity
        or has_structural_identity
    ):
        raise ValueError(
            "row-level answer values require a display result entity, an explicit "
            "grain field, or an identity output: " + ", ".join(row_level_values)
        )


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
    structural_outputs: Iterable[str],
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
            "name": f"{tool}_semantic_output_plan",
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
            plan = _parse_output_plan(content, tool=tool, catalog=catalog)
            _validate_semantic_output_plan(
                plan,
                catalog,
                tool=tool,
                structural_outputs=structural_outputs,
            )
            return plan
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
                        f"{exc}\nReturn the complete semantic output plan using only "
                        "catalog roles and aliases."
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
                    structural_outputs=(
                        *route_subquery["joinKeys"],
                        *outgoing[route_subquery["id"]],
                    ),
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
    "PlannedResultEntity",
    "SemanticOutputPlan",
    "compile_semantic_output_plan",
    "finalize_required_outputs",
    "make_plan_outputs_node",
    "output_plan_json_schema",
]
