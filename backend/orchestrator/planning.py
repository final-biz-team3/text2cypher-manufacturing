"""라우터가 반환하는 실행 계획의 공통 타입과 검증 규칙."""

import json
import math
import re
from typing import Any, Literal, NotRequired, TypedDict

SUPPORTED_TOOLS = {"sql", "graph"}

DEFAULT_SHARED_JOIN_ALIASES = frozenset(
    {
        "componentId",
        "finishedProductId",
        "finishedProductIdA",
        "finishedProductIdB",
        "locationId",
        "productId",
        "rootProductId",
        "scrapReasonId",
        "supplierId",
        "workOrderId",
    }
)

BOM_SHORTAGE_GRAPH_OUTPUTS = frozenset(
    {
        "finishedProductId",
        "finishedProductName",
        "componentId",
        "componentName",
        "depth",
        "pathProductIds",
        "quantityPerAssembly",
        "supplierId",
        "supplierName",
    }
)
BOM_SHORTAGE_SQL_OUTPUTS = frozenset({"componentId", "makeFlag", "actualStock"})

_BINDING_SCHEMA_VARIANTS = [
    {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    *[
        {
            "type": "object",
            "properties": {name: {"type": "string"}},
            "required": [name],
            "additionalProperties": False,
        }
        for name in ("componentIds", "productIds")
    ],
]


def route_draft_json_schema(
    shared_join_aliases: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Build the strict router schema from cross-source join aliases."""
    aliases = sorted(shared_join_aliases)
    if not aliases:
        raise ValueError("route draft identity alias enum은 비어 있을 수 없습니다.")
    binding_names = sorted(
        {f"{alias[:-2]}Ids" for alias in aliases if alias.endswith("Id")}
    )
    binding_variants: list[dict[str, Any]] = [
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    ]
    binding_variants.extend(
        {
            "type": "object",
            "properties": {
                name: {
                    "type": "string",
                    "pattern": rf"^[^.\s]+\.{name[:-1]}$",
                }
            },
            "required": [name],
            "additionalProperties": False,
        }
        for name in binding_names
    )
    return {
        "type": "object",
        "properties": {
            "tool_plan": {
                "type": "array",
                "items": {"type": "string", "enum": ["sql", "graph"]},
            },
            "subqueries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "tool": {
                            "type": "string",
                            "enum": ["sql", "graph"],
                        },
                        "question": {"type": "string"},
                        "dependsOn": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "joinKeys": {
                            "type": "array",
                            "items": {"type": "string", "enum": aliases},
                        },
                        "inputBindings": {"anyOf": binding_variants},
                    },
                    "required": [
                        "id",
                        "tool",
                        "question",
                        "dependsOn",
                        "joinKeys",
                        "inputBindings",
                    ],
                    "additionalProperties": False,
                },
            },
            "resultTransform": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["bom_shortage_v1"],
                            },
                            "productionQty": {"type": "number"},
                        },
                        "required": ["type", "productionQty"],
                        "additionalProperties": False,
                    },
                ]
            },
        },
        "required": ["tool_plan", "subqueries", "resultTransform"],
        "additionalProperties": False,
    }


ROUTE_DRAFT_JSON_SCHEMA = route_draft_json_schema(DEFAULT_SHARED_JOIN_ALIASES)

EXECUTION_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_plan": {
            "type": "array",
            "items": {"type": "string", "enum": ["sql", "graph"]},
        },
        "subqueries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "tool": {"type": "string", "enum": ["sql", "graph"]},
                    "question": {"type": "string"},
                    "dependsOn": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "requiredOutputs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "joinKeys": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "inputBindings": {"anyOf": _BINDING_SCHEMA_VARIANTS},
                },
                "required": [
                    "id",
                    "tool",
                    "question",
                    "dependsOn",
                    "requiredOutputs",
                    "joinKeys",
                    "inputBindings",
                ],
                "additionalProperties": False,
            },
        },
        "resultTransform": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["bom_shortage_v1"]},
                        "productionQty": {"type": "number"},
                    },
                    "required": ["type", "productionQty"],
                    "additionalProperties": False,
                },
            ]
        },
    },
    "required": ["tool_plan", "subqueries", "resultTransform"],
    "additionalProperties": False,
}


class Subquery(TypedDict):
    """하나의 데이터 소스가 담당하는 독립 실행 단위."""

    id: str
    tool: str
    question: str
    dependsOn: list[str]
    requiredOutputs: list[str]
    joinKeys: list[str]
    inputBindings: NotRequired[dict[str, str]]


class RouteSubquery(TypedDict):
    """Router-owned fields before schema-aware output planning."""

    id: str
    tool: str
    question: str
    dependsOn: list[str]
    joinKeys: list[str]
    inputBindings: NotRequired[dict[str, str]]


class BomShortageTransform(TypedDict):
    type: Literal["bom_shortage_v1"]
    productionQty: int | float


class ExecutionPlan(TypedDict):
    tool_plan: list[str]
    subqueries: list[Subquery]
    resultTransform: NotRequired[BomShortageTransform | None]


class RouteDraft(TypedDict):
    tool_plan: list[str]
    subqueries: list[RouteSubquery]
    resultTransform: NotRequired[BomShortageTransform | None]


def _string_list(value: Any, field: str, subquery_id: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(
            f"subquery {subquery_id!r}의 {field}는 문자열 배열이어야 합니다."
        )
    if len(value) != len(set(value)):
        raise ValueError(f"subquery {subquery_id!r}의 {field}에 중복 값이 있습니다.")
    return value


def _validate_transform_shape(raw_transform: Any) -> BomShortageTransform | None:
    if raw_transform is None:
        return None
    if not isinstance(raw_transform, dict) or set(raw_transform) != {
        "type",
        "productionQty",
    }:
        raise ValueError("resultTransform 형식이 잘못됐습니다.")
    if raw_transform.get("type") != "bom_shortage_v1":
        raise ValueError("지원하지 않는 resultTransform입니다.")
    production_qty = raw_transform.get("productionQty")
    if isinstance(production_qty, bool) or not isinstance(production_qty, int | float):
        raise ValueError("bom_shortage_v1 productionQty는 유한한 양수여야 합니다.")
    try:
        finite_quantity = math.isfinite(production_qty)
    except OverflowError:
        finite_quantity = False
    if not finite_quantity or production_qty <= 0:
        raise ValueError("bom_shortage_v1 productionQty는 유한한 양수여야 합니다.")
    return {"type": "bom_shortage_v1", "productionQty": production_qty}


_KOREAN_COUNTER_ONES = {
    "한": 1,
    "하나": 1,
    "두": 2,
    "둘": 2,
    "세": 3,
    "셋": 3,
    "네": 4,
    "넷": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
}
_KOREAN_COUNTER_TENS = {
    "열": 10,
    "스물": 20,
    "스무": 20,
    "서른": 30,
    "마흔": 40,
    "쉰": 50,
    "예순": 60,
    "일흔": 70,
    "여든": 80,
    "아흔": 90,
}
_COUNTER_QUANTITY = re.compile(
    r"(?P<quantity>\d+(?:\.\d+)?|[가-힣]+)\s*(?:개(?:분)?|대)(?=$|[\s을를이가의,.?!])"
)


def _korean_counter_quantity(value: str) -> int | None:
    direct = _KOREAN_COUNTER_ONES.get(value)
    if direct is not None:
        return direct
    for prefix, tens in _KOREAN_COUNTER_TENS.items():
        if not value.startswith(prefix):
            continue
        remainder = value[len(prefix) :]
        if not remainder:
            return tens
        ones = _KOREAN_COUNTER_ONES.get(remainder)
        if ones is not None:
            return tens + ones
    return None


def explicit_production_quantity(query: str) -> int | float | None:
    """Read one unambiguous quantity attached to a Korean production counter.

    Bare numbers are deliberately ignored because manufacturing product names commonly
    contain model and size numbers (for example ``..., 58``).
    """
    quantities: list[int | float] = []
    for match in _COUNTER_QUANTITY.finditer(query):
        raw = match.group("quantity")
        if raw[0].isdigit():
            value = float(raw) if "." in raw else int(raw)
        else:
            parsed = _korean_counter_quantity(raw)
            if parsed is None:
                continue
            value = parsed
        quantities.append(value)
    distinct = {float(value) for value in quantities}
    if len(distinct) != 1:
        return None
    return quantities[0]


def validate_route_subqueries(
    subqueries: Any,
    *,
    shared_join_aliases: set[str] | frozenset[str] = DEFAULT_SHARED_JOIN_ALIASES,
) -> list[RouteSubquery]:
    """Validate source ownership, dependency, join, and binding draft fields."""
    if not isinstance(subqueries, list) or not subqueries:
        raise ValueError("subqueries는 비어 있지 않은 배열이어야 합니다.")
    validated: list[RouteSubquery] = []
    for index, raw in enumerate(subqueries):
        if not isinstance(raw, dict):
            raise ValueError(f"subqueries[{index}]는 객체여야 합니다.")
        if "requiredOutputs" in raw:
            raise ValueError("route draft는 requiredOutputs를 생성할 수 없습니다.")
        subquery_id = raw.get("id")
        if not isinstance(subquery_id, str) or not subquery_id.strip():
            raise ValueError(
                f"subqueries[{index}].id는 비어 있지 않은 문자열이어야 합니다."
            )
        tool = raw.get("tool")
        if tool not in SUPPORTED_TOOLS:
            raise ValueError(
                f"subquery {subquery_id!r}의 tool이 지원되지 않습니다: {tool!r}"
            )
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"subquery {subquery_id!r}의 question이 비어 있습니다.")
        depends_on = _string_list(raw.get("dependsOn", []), "dependsOn", subquery_id)
        join_keys = _string_list(raw.get("joinKeys", []), "joinKeys", subquery_id)
        unknown_join_keys = set(join_keys) - set(shared_join_aliases)
        if unknown_join_keys:
            names = ", ".join(sorted(unknown_join_keys))
            raise ValueError(
                f"subquery {subquery_id!r}의 joinKeys가 schema identity alias가 "
                f"아닙니다: {names}"
            )
        raw_bindings = raw.get("inputBindings", {})
        if not isinstance(raw_bindings, dict) or len(raw_bindings) > 1:
            raise ValueError(
                f"subquery {subquery_id!r}의 inputBindings 형식이 잘못됐습니다."
            )
        item: RouteSubquery = {
            "id": subquery_id,
            "tool": tool,
            "question": question,
            "dependsOn": depends_on,
            "joinKeys": join_keys,
        }
        if raw_bindings:
            item["inputBindings"] = dict(raw_bindings)
        validated.append(item)

    by_id = {item["id"]: item for item in validated}
    if len(by_id) != len(validated):
        raise ValueError("subquery id는 중복될 수 없습니다.")
    for item in validated:
        binding_dependencies: set[str] = set()
        for binding_name, source in item.get("inputBindings", {}).items():
            if not isinstance(binding_name, str) or not binding_name.endswith("Ids"):
                raise ValueError(
                    f"subquery {item['id']!r}의 binding 이름이 잘못됐습니다."
                )
            if not isinstance(source, str) or "." not in source:
                raise ValueError(
                    f"subquery {item['id']!r}의 inputBindings 형식이 잘못됐습니다."
                )
            dependency_id, output_field = source.split(".", 1)
            binding_dependencies.add(dependency_id)
            if dependency_id not in item["dependsOn"] or dependency_id not in by_id:
                raise ValueError(
                    f"subquery {item['id']!r}의 binding {source!r}는 dependsOn에 "
                    "선언된 단계만 참조할 수 있습니다."
                )
            if output_field not in shared_join_aliases:
                raise ValueError(
                    f"subquery {item['id']!r}의 binding source가 schema identity "
                    f"alias가 아닙니다: {output_field}"
                )
            expected_binding = (
                f"{output_field[:-2]}Ids" if output_field.endswith("Id") else None
            )
            if expected_binding != binding_name:
                raise ValueError(
                    f"subquery {item['id']!r}의 binding 이름과 source alias가 "
                    "일치하지 않습니다."
                )
        unknown_dependencies = set(item["dependsOn"]) - set(by_id)
        if item["id"] in item["dependsOn"] or unknown_dependencies:
            raise ValueError(
                f"subquery {item['id']!r}가 존재하지 않는 의존성을 참조합니다."
            )
        if set(item["dependsOn"]) != binding_dependencies:
            raise ValueError(
                f"subquery {item['id']!r}의 dependsOn은 inputBindings에서 "
                "참조되어야 합니다."
            )

    pending = {item["id"]: set(item["dependsOn"]) for item in validated}
    ordered: list[RouteSubquery] = []
    while pending:
        ready = [
            item["id"]
            for item in validated
            if item["id"] in pending and not pending[item["id"]]
        ]
        if not ready:
            raise ValueError("subqueries에 순환 의존성이 있습니다.")
        for subquery_id in ready:
            ordered.append(by_id[subquery_id])
            del pending[subquery_id]
            for dependencies in pending.values():
                dependencies.discard(subquery_id)

    if len(ordered) == 2:
        first_keys, second_keys = ordered[0]["joinKeys"], ordered[1]["joinKeys"]
        if bool(first_keys) != bool(second_keys):
            raise ValueError(
                "HYBRID 계획은 양쪽 subquery에 joinKeys를 모두 지정하거나 "
                "모두 비워야 합니다."
            )
        if first_keys and set(first_keys) != set(second_keys):
            raise ValueError("HYBRID 계획의 양쪽 joinKeys 구성이 일치해야 합니다.")
        if any(item["dependsOn"] for item in ordered) and not first_keys:
            raise ValueError("의존 HYBRID 계획에는 양쪽의 공통 joinKeys가 필요합니다.")
    return ordered


def _merge_binding_join_keys(
    subqueries: Any,
    shared_join_aliases: set[str] | frozenset[str],
) -> Any:
    if not isinstance(subqueries, list):
        return subqueries
    normalized = [dict(item) if isinstance(item, dict) else item for item in subqueries]
    by_id = {
        item.get("id"): item
        for item in normalized
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for item in normalized:
        if not isinstance(item, dict):
            continue
        bindings = item.get("inputBindings")
        dependencies = item.get("dependsOn")
        if not isinstance(bindings, dict) or not isinstance(dependencies, list):
            continue
        for source in bindings.values():
            if not isinstance(source, str) or source.count(".") != 1:
                continue
            source_id, alias = source.split(".", 1)
            source_item = by_id.get(source_id)
            if (
                alias not in shared_join_aliases
                or source_id not in dependencies
                or not isinstance(source_item, dict)
            ):
                continue
            for target in (source_item, item):
                join_keys = target.get("joinKeys")
                if isinstance(join_keys, list) and alias not in join_keys:
                    target["joinKeys"] = [*join_keys, alias]
    return normalized


def parse_route_draft(
    content: str,
    query: str,
    *,
    shared_join_aliases: set[str] | frozenset[str] = DEFAULT_SHARED_JOIN_ALIASES,
    recover_missing_binding_join_keys: bool = True,
) -> RouteDraft:
    """Parse the router response without accepting output-contract guesses."""
    raw = json.loads(content)
    legacy = isinstance(raw, list)
    if legacy:
        raw_tool_plan: Any = raw
        raw_subqueries: Any = [
            {
                "id": f"{tool}_query",
                "tool": tool,
                "question": query,
                "dependsOn": [],
                "joinKeys": [],
                "inputBindings": {},
            }
            for tool in raw
        ]
        raw_transform: Any = None
    elif isinstance(raw, dict):
        raw_tool_plan = raw.get("tool_plan")
        raw_subqueries = raw.get("subqueries")
        raw_transform = raw.get("resultTransform")
    else:
        raise ValueError("route_query 응답은 JSON 객체여야 합니다.")
    if not isinstance(raw_tool_plan, list) or not raw_tool_plan:
        raise ValueError("route_query가 빈 tool_plan을 반환했습니다.")
    if any(not isinstance(tool, str) for tool in raw_tool_plan):
        raise ValueError("tool_plan은 문자열 배열이어야 합니다.")
    tool_plan = list(raw_tool_plan)
    if len(tool_plan) != len(set(tool_plan)):
        raise ValueError("tool_plan에는 같은 도구를 중복 지정할 수 없습니다.")
    unsupported = set(tool_plan) - SUPPORTED_TOOLS
    if unsupported:
        raise ValueError(
            "지원하지 않는 tool_plan 값: " + ", ".join(sorted(unsupported))
        )
    normalized_subqueries = (
        _merge_binding_join_keys(raw_subqueries, shared_join_aliases)
        if recover_missing_binding_join_keys
        else raw_subqueries
    )
    subqueries = validate_route_subqueries(
        normalized_subqueries, shared_join_aliases=shared_join_aliases
    )
    planned_tools = [item["tool"] for item in subqueries]
    if len(planned_tools) != len(set(planned_tools)):
        raise ValueError("도구 하나당 subquery를 정확히 하나만 지정할 수 있습니다.")
    if planned_tools != tool_plan:
        raise ValueError("tool_plan이 subqueries의 실행 순서와 일치하지 않습니다.")
    transform = _validate_transform_shape(raw_transform)
    if transform is not None:
        explicit_quantity = explicit_production_quantity(query)
        if explicit_quantity is not None and float(transform["productionQty"]) != float(
            explicit_quantity
        ):
            raise ValueError(
                "bom_shortage_v1 productionQty가 질문에서 단위와 함께 명시된 "
                f"생산 수량 {explicit_quantity}와 일치하지 않습니다."
            )
        if planned_tools != ["graph", "sql"]:
            raise ValueError("bom_shortage_v1은 GRAPH → SQL 2단계 계획만 지원합니다.")
        graph, sql = subqueries
        if sql["dependsOn"] != [graph["id"]]:
            raise ValueError("bom_shortage_v1 SQL은 GRAPH 단계에 의존해야 합니다.")
        if graph["joinKeys"] != ["componentId"] or sql["joinKeys"] != ["componentId"]:
            raise ValueError(
                "bom_shortage_v1 join key는 양쪽 모두 componentId여야 합니다."
            )
        if sql.get("inputBindings") != {"componentIds": f"{graph['id']}.componentId"}:
            raise ValueError(
                "bom_shortage_v1 componentIds binding 계약이 잘못됐습니다."
            )
    result: RouteDraft = {"tool_plan": tool_plan, "subqueries": subqueries}
    if transform is not None:
        result["resultTransform"] = transform
    return result


def validate_subqueries(
    subqueries: Any, *, allow_empty_required_outputs: bool = False
) -> list[Subquery]:
    """shape, 의존성, binding, join key와 순환 의존성을 검증한다."""
    if not isinstance(subqueries, list) or not subqueries:
        raise ValueError("subqueries는 비어 있지 않은 배열이어야 합니다.")

    validated: list[Subquery] = []
    for index, raw in enumerate(subqueries):
        if not isinstance(raw, dict):
            raise ValueError(f"subqueries[{index}]는 객체여야 합니다.")
        subquery_id = raw.get("id")
        if not isinstance(subquery_id, str) or not subquery_id.strip():
            raise ValueError(
                f"subqueries[{index}].id는 비어 있지 않은 문자열이어야 합니다."
            )
        tool = raw.get("tool")
        if tool not in SUPPORTED_TOOLS:
            raise ValueError(
                f"subquery {subquery_id!r}의 tool이 지원되지 않습니다: {tool!r}"
            )
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"subquery {subquery_id!r}의 question이 비어 있습니다.")

        depends_on = _string_list(raw.get("dependsOn", []), "dependsOn", subquery_id)
        required_outputs = _string_list(
            raw.get("requiredOutputs", []), "requiredOutputs", subquery_id
        )
        if not required_outputs and not allow_empty_required_outputs:
            raise ValueError(
                f"subquery {subquery_id!r}의 requiredOutputs는 비어 있을 수 없습니다."
            )
        join_keys = _string_list(raw.get("joinKeys", []), "joinKeys", subquery_id)
        if not set(join_keys).issubset(required_outputs):
            raise ValueError(
                f"subquery {subquery_id!r}의 joinKeys는 requiredOutputs에 포함돼야 합니다."
            )

        raw_bindings = raw.get("inputBindings", {})
        if not isinstance(raw_bindings, dict) or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or "." not in value
            for key, value in raw_bindings.items()
        ):
            raise ValueError(
                f"subquery {subquery_id!r}의 inputBindings 형식이 잘못됐습니다."
            )

        item: Subquery = {
            "id": subquery_id,
            "tool": tool,
            "question": question,
            "dependsOn": depends_on,
            "requiredOutputs": required_outputs,
            "joinKeys": join_keys,
        }
        if raw_bindings:
            item["inputBindings"] = dict(raw_bindings)
        validated.append(item)

    by_id = {item["id"]: item for item in validated}
    if len(by_id) != len(validated):
        raise ValueError("subquery id는 중복될 수 없습니다.")

    for item in validated:
        subquery_id = item["id"]
        for dependency_id in item["dependsOn"]:
            if dependency_id == subquery_id or dependency_id not in by_id:
                raise ValueError(
                    f"subquery {subquery_id!r}가 존재하지 않는 의존성 "
                    f"{dependency_id!r}을 참조합니다."
                )

        binding_dependencies: set[str] = set()
        for source in item.get("inputBindings", {}).values():
            dependency_id, output_field = source.split(".", 1)
            binding_dependencies.add(dependency_id)
            if dependency_id not in item["dependsOn"]:
                raise ValueError(
                    f"subquery {subquery_id!r}의 binding {source!r}는 dependsOn에 "
                    "선언된 단계만 참조할 수 있습니다."
                )
            if output_field not in by_id[dependency_id]["requiredOutputs"]:
                raise ValueError(
                    f"subquery {subquery_id!r}의 binding {source!r}가 선행 단계의 "
                    "requiredOutputs에 없는 필드를 참조합니다."
                )

        unbound_dependencies = [
            dependency_id
            for dependency_id in item["dependsOn"]
            if dependency_id not in binding_dependencies
        ]
        if unbound_dependencies:
            names = ", ".join(unbound_dependencies)
            raise ValueError(
                f"subquery {subquery_id!r}의 dependsOn {names!r}는 "
                "inputBindings에서 참조되어야 합니다."
            )

        for join_key in item["joinKeys"]:
            if item["dependsOn"] and not any(
                join_key in by_id[dependency_id]["requiredOutputs"]
                for dependency_id in item["dependsOn"]
            ):
                raise ValueError(
                    f"subquery {subquery_id!r}의 join key {join_key!r}가 선행 단계에 "
                    "없습니다."
                )

    pending = {item["id"]: set(item["dependsOn"]) for item in validated}
    ordered: list[Subquery] = []
    while pending:
        ready = [
            item["id"]
            for item in validated
            if item["id"] in pending and not pending[item["id"]]
        ]
        if not ready:
            raise ValueError("subqueries에 순환 의존성이 있습니다.")
        for subquery_id in ready:
            ordered.append(by_id[subquery_id])
            del pending[subquery_id]
            for dependencies in pending.values():
                dependencies.discard(subquery_id)

    if len(ordered) == 2:
        first_join_keys = ordered[0]["joinKeys"]
        second_join_keys = ordered[1]["joinKeys"]
        if bool(first_join_keys) != bool(second_join_keys):
            raise ValueError(
                "HYBRID 계획은 양쪽 subquery에 joinKeys를 모두 지정하거나 "
                "모두 비워야 합니다."
            )
        if first_join_keys and set(first_join_keys) != set(second_join_keys):
            raise ValueError("HYBRID 계획의 양쪽 joinKeys 구성이 일치해야 합니다.")
        if any(item["dependsOn"] for item in ordered) and not first_join_keys:
            raise ValueError("의존 HYBRID 계획에는 양쪽의 공통 joinKeys가 필요합니다.")

    return ordered


def _legacy_subqueries(tool_plan: list[str], query: str) -> list[Subquery]:
    """tool_plan 배열을 단일 단계 실행 계획으로 변환한다."""
    return [
        {
            "id": f"{tool}_query",
            "tool": tool,
            "question": query,
            "dependsOn": [],
            "requiredOutputs": [],
            "joinKeys": [],
        }
        for tool in tool_plan
    ]


def validate_result_transform(
    raw_transform: Any, subqueries: list[Subquery]
) -> BomShortageTransform | None:
    """allowlist된 최종 계산과 그 source 계약을 함께 검증한다."""
    transform = _validate_transform_shape(raw_transform)
    if transform is None:
        return None
    if len(subqueries) != 2 or [item["tool"] for item in subqueries] != [
        "graph",
        "sql",
    ]:
        raise ValueError("bom_shortage_v1은 GRAPH → SQL 2단계 계획만 지원합니다.")
    graph, sql = subqueries
    if sql["dependsOn"] != [graph["id"]]:
        raise ValueError("bom_shortage_v1 SQL은 GRAPH 단계에 의존해야 합니다.")
    if graph["joinKeys"] != ["componentId"] or sql["joinKeys"] != ["componentId"]:
        raise ValueError("bom_shortage_v1 join key는 양쪽 모두 componentId여야 합니다.")
    if set(graph["requiredOutputs"]) != BOM_SHORTAGE_GRAPH_OUTPUTS:
        raise ValueError("bom_shortage_v1 GRAPH requiredOutputs 계약이 잘못됐습니다.")
    if set(sql["requiredOutputs"]) != BOM_SHORTAGE_SQL_OUTPUTS:
        raise ValueError("bom_shortage_v1 SQL requiredOutputs 계약이 잘못됐습니다.")
    if sql.get("inputBindings") != {"componentIds": f"{graph['id']}.componentId"}:
        raise ValueError("bom_shortage_v1 componentIds binding 계약이 잘못됐습니다.")
    return transform


def parse_execution_plan(content: str, query: str) -> ExecutionPlan:
    """LLM의 JSON object 또는 array 응답을 검증된 실행 계획으로 바꾼다."""
    raw = json.loads(content)
    legacy = isinstance(raw, list)
    if isinstance(raw, list):
        raw_tool_plan: Any = raw
        raw_subqueries: Any = None
    elif isinstance(raw, dict):
        raw_tool_plan = raw.get("tool_plan")
        raw_subqueries = raw.get("subqueries")
    else:
        raise ValueError("route_query 응답은 JSON 객체여야 합니다.")

    if not isinstance(raw_tool_plan, list) or not raw_tool_plan:
        raise ValueError("route_query가 빈 tool_plan을 반환했습니다.")
    if any(not isinstance(tool, str) for tool in raw_tool_plan):
        raise ValueError("tool_plan은 문자열 배열이어야 합니다.")
    tool_plan: list[str] = list(raw_tool_plan)
    if len(tool_plan) != len(set(tool_plan)):
        raise ValueError("tool_plan에는 같은 도구를 중복 지정할 수 없습니다.")
    unsupported_tools = set(tool_plan) - SUPPORTED_TOOLS
    if unsupported_tools:
        names = ", ".join(sorted(unsupported_tools))
        raise ValueError(f"지원하지 않는 tool_plan 값: {names}")

    subqueries = _legacy_subqueries(tool_plan, query) if legacy else raw_subqueries
    validated = validate_subqueries(subqueries, allow_empty_required_outputs=legacy)
    planned_tools = [item["tool"] for item in validated]
    if len(planned_tools) != len(set(planned_tools)):
        raise ValueError("도구 하나당 subquery를 정확히 하나만 지정할 수 있습니다.")
    if set(planned_tools) != set(tool_plan):
        raise ValueError("tool_plan과 subqueries의 도구 구성이 일치하지 않습니다.")
    tool_positions = {tool: index for index, tool in enumerate(tool_plan)}
    subqueries_by_id = {item["id"]: item for item in validated}
    for item in validated:
        for dependency_id in item["dependsOn"]:
            dependency_tool = subqueries_by_id[dependency_id]["tool"]
            if tool_positions[dependency_tool] >= tool_positions[item["tool"]]:
                raise ValueError(
                    "tool_plan이 subqueries의 의존 실행 순서와 일치하지 않습니다."
                )
    if planned_tools != tool_plan:
        raise ValueError("tool_plan이 subqueries의 실행 순서와 일치하지 않습니다.")
    transform = validate_result_transform(
        raw.get("resultTransform") if isinstance(raw, dict) else None,
        validated,
    )
    result: ExecutionPlan = {"tool_plan": tool_plan, "subqueries": validated}
    if transform is not None:
        result["resultTransform"] = transform
    return result
