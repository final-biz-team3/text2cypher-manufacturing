"""라우팅과 execution plan이 공유하는 구조 계약을 정의한다."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal, NotRequired, TypedDict, cast

from orchestrator.semantic_catalog import QuerySemanticCatalog

SUPPORTED_TOOLS = frozenset({"sql", "graph"})
MAX_SUBQUERIES = 2

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
        "supplierIdA",
        "supplierIdB",
        "workOrderId",
    }
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InputBindingDraft(TypedDict):
    target: str
    sourceSubqueryId: str
    sourceOutput: str


class Subquery(TypedDict):
    id: str
    tool: str
    question: str
    dependsOn: list[str]
    requiredOutputs: list[str]
    joinKeys: list[str]
    inputBindings: NotRequired[dict[str, str]]
    generatorRules: NotRequired[list[str]]


class RouteSubquery(TypedDict):
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


def route_draft_json_schema(
    shared_join_aliases: set[str] | frozenset[str],
    *,
    catalog: QuerySemanticCatalog | None = None,
) -> dict[str, Any]:
    """LLM 경계 schema를 구성한다. tool_plan은 의도적으로 이후에 파생한다."""
    join_aliases = sorted(shared_join_aliases)
    if not join_aliases:
        raise ValueError("route draft join alias enum must not be empty")
    source_outputs = (
        sorted(
            set(catalog.allowed_aliases("sql")) | set(catalog.allowed_aliases("graph"))
        )
        if catalog is not None
        else join_aliases
    )
    return {
        "type": "object",
        "properties": {
            "subqueries": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_SUBQUERIES,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "pattern": _SAFE_IDENTIFIER.pattern},
                        "tool": {
                            "type": "string",
                            "enum": ["sql", "graph"],
                        },
                        "question": {"type": "string", "minLength": 1},
                        "dependsOn": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "joinKeys": {
                            "type": "array",
                            "items": {"type": "string", "enum": join_aliases},
                        },
                        "inputBindings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "target": {
                                        "type": "string",
                                        "pattern": _SAFE_IDENTIFIER.pattern,
                                    },
                                    "sourceSubqueryId": {"type": "string"},
                                    "sourceOutput": {
                                        "type": "string",
                                        "enum": source_outputs,
                                    },
                                },
                                "required": [
                                    "target",
                                    "sourceSubqueryId",
                                    "sourceOutput",
                                ],
                                "additionalProperties": False,
                            },
                        },
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
                            "productionQty": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["type", "productionQty"],
                        "additionalProperties": False,
                    },
                ]
            },
        },
        "required": ["subqueries", "resultTransform"],
        "additionalProperties": False,
    }


ROUTE_DRAFT_JSON_SCHEMA = route_draft_json_schema(DEFAULT_SHARED_JOIN_ALIASES)


def _string_list(value: Any, field: str, subquery_id: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"subquery {subquery_id!r} {field} must be a string array")
    if len(value) != len(set(value)):
        raise ValueError(f"subquery {subquery_id!r} {field} contains duplicates")
    return list(value)


def _validate_transform_shape(raw_transform: Any) -> BomShortageTransform | None:
    if raw_transform is None:
        return None
    if not isinstance(raw_transform, dict) or set(raw_transform) != {
        "type",
        "productionQty",
    }:
        raise ValueError("invalid resultTransform shape")
    if raw_transform.get("type") != "bom_shortage_v1":
        raise ValueError("unsupported resultTransform")
    production_qty = raw_transform.get("productionQty")
    if isinstance(production_qty, bool) or not isinstance(production_qty, int | float):
        raise ValueError(
            "bom_shortage_v1 productionQty must be a finite positive number"
        )
    try:
        is_finite = math.isfinite(production_qty)
    except OverflowError:
        is_finite = False
    if not is_finite or production_qty <= 0:
        raise ValueError(
            "bom_shortage_v1 productionQty must be a finite positive number"
        )
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
_PRODUCTION_COUNTER = re.compile(
    r"(?P<quantity>\d+(?:\.\d+)?|[가-힣]+)\s*(?:개(?:분)?|대)"
    r"(?=$|[\s을를이가의,.?!])"
)


def _korean_counter_quantity(value: str) -> int | None:
    direct = _KOREAN_COUNTER_ONES.get(value)
    if direct is not None:
        return direct
    for prefix, tens in _KOREAN_COUNTER_TENS.items():
        if value.startswith(prefix):
            remainder = value[len(prefix) :]
            if not remainder:
                return tens
            ones = _KOREAN_COUNTER_ONES.get(remainder)
            if ones is not None:
                return tens + ones
    return None


def explicit_production_quantity(query: str) -> int | float | None:
    """formal transform의 생산 단위에 연결된 수량만 읽는다."""
    quantities: list[int | float] = []
    for match in _PRODUCTION_COUNTER.finditer(query):
        raw = match.group("quantity")
        if raw[0].isdigit():
            quantity: int | float = float(raw) if "." in raw else int(raw)
        else:
            parsed = _korean_counter_quantity(raw)
            if parsed is None:
                continue
            quantity = parsed
        quantities.append(quantity)
    if len({float(value) for value in quantities}) != 1:
        return None
    return quantities[0]


def _compile_binding_array(
    value: Any,
    *,
    subquery_id: str,
) -> dict[str, str]:
    if not isinstance(value, list):
        raise ValueError(f"subquery {subquery_id!r} inputBindings must be an array")
    compiled: dict[str, str] = {}
    for index, raw_binding in enumerate(value):
        if not isinstance(raw_binding, dict) or set(raw_binding) != {
            "target",
            "sourceSubqueryId",
            "sourceOutput",
        }:
            raise ValueError(
                f"subquery {subquery_id!r} inputBindings[{index}] has invalid shape"
            )
        target = raw_binding["target"]
        source_id = raw_binding["sourceSubqueryId"]
        source_output = raw_binding["sourceOutput"]
        if (
            not isinstance(target, str)
            or _SAFE_IDENTIFIER.fullmatch(target) is None
            or not isinstance(source_id, str)
            or not source_id
            or not isinstance(source_output, str)
            or _SAFE_IDENTIFIER.fullmatch(source_output) is None
        ):
            raise ValueError(
                f"subquery {subquery_id!r} inputBindings[{index}] has invalid values"
            )
        if target in compiled:
            raise ValueError(
                f"subquery {subquery_id!r} input binding target {target!r} is duplicated"
            )
        compiled[target] = f"{source_id}.{source_output}"
    return compiled


def _topological_order[T: RouteSubquery | Subquery](items: list[T]) -> list[T]:
    by_id = {item["id"]: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("subquery IDs must be unique")
    pending = {item["id"]: set(item["dependsOn"]) for item in items}
    ordered: list[T] = []
    while pending:
        ready = [
            item["id"]
            for item in items
            if item["id"] in pending and not pending[item["id"]]
        ]
        if not ready:
            raise ValueError("subqueries contain a cyclic dependency")
        for subquery_id in ready:
            ordered.append(by_id[subquery_id])
            del pending[subquery_id]
            for dependencies in pending.values():
                dependencies.discard(subquery_id)
    return ordered


def derive_tool_plan(subqueries: list[RouteSubquery] | list[Subquery]) -> list[str]:
    ordered = _topological_order(list(subqueries))
    return [item["tool"] for item in ordered]


def validate_route_subqueries(
    subqueries: Any,
    *,
    shared_join_aliases: set[str] | frozenset[str] = DEFAULT_SHARED_JOIN_ALIASES,
    catalog: QuerySemanticCatalog | None = None,
) -> list[RouteSubquery]:
    """모델 라우팅 경계를 검증하고 executor binding으로 컴파일한다."""
    if not isinstance(subqueries, list) or not subqueries:
        raise ValueError("subqueries must be a non-empty array")
    if len(subqueries) > MAX_SUBQUERIES:
        raise ValueError(f"at most {MAX_SUBQUERIES} subqueries are supported")

    validated: list[RouteSubquery] = []
    for index, raw in enumerate(subqueries):
        if not isinstance(raw, dict):
            raise ValueError(f"subqueries[{index}] must be an object")
        expected_fields = {
            "id",
            "tool",
            "question",
            "dependsOn",
            "joinKeys",
            "inputBindings",
        }
        if set(raw) != expected_fields:
            raise ValueError(
                f"subqueries[{index}] must contain only the route boundary fields"
            )
        subquery_id = raw.get("id")
        if (
            not isinstance(subquery_id, str)
            or _SAFE_IDENTIFIER.fullmatch(subquery_id) is None
        ):
            raise ValueError(f"subqueries[{index}].id is not a safe identifier")
        tool = raw.get("tool")
        if tool not in SUPPORTED_TOOLS:
            raise ValueError(f"subquery {subquery_id!r} has unsupported tool {tool!r}")
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"subquery {subquery_id!r} question is empty")
        depends_on = _string_list(raw.get("dependsOn"), "dependsOn", subquery_id)
        join_keys = _string_list(raw.get("joinKeys"), "joinKeys", subquery_id)
        unknown_join_keys = set(join_keys) - set(shared_join_aliases)
        if unknown_join_keys:
            raise ValueError(
                f"subquery {subquery_id!r} joinKeys are not shared identities: "
                + ", ".join(sorted(unknown_join_keys))
            )
        bindings = _compile_binding_array(
            raw.get("inputBindings"), subquery_id=subquery_id
        )
        item: RouteSubquery = {
            "id": subquery_id,
            "tool": cast(str, tool),
            "question": question,
            "dependsOn": depends_on,
            "joinKeys": join_keys,
        }
        if bindings:
            item["inputBindings"] = bindings
        validated.append(item)

    by_id = {item["id"]: item for item in validated}
    if len(by_id) != len(validated):
        raise ValueError("subquery IDs must be unique")
    tools = [item["tool"] for item in validated]
    if len(tools) != len(set(tools)):
        raise ValueError("only one subquery per source is supported")

    for item in validated:
        unknown_dependencies = set(item["dependsOn"]) - set(by_id)
        if item["id"] in item["dependsOn"] or unknown_dependencies:
            raise ValueError(
                f"subquery {item['id']!r} references an unknown dependency"
            )
        binding_dependencies: set[str] = set()
        for source in item.get("inputBindings", {}).values():
            dependency_id, source_output = source.split(".", 1)
            binding_dependencies.add(dependency_id)
            if dependency_id not in item["dependsOn"]:
                raise ValueError(
                    f"subquery {item['id']!r} binding {source!r} is not in dependsOn"
                )
            producer = by_id[dependency_id]
            if catalog is not None and source_output not in catalog.allowed_aliases(
                producer["tool"]
            ):
                raise ValueError(
                    f"binding source {source_output!r} is not owned by "
                    f"{producer['tool']} producer {dependency_id!r}"
                )
        if set(item["dependsOn"]) != binding_dependencies:
            raise ValueError(
                f"subquery {item['id']!r} dependencies must be referenced by bindings"
            )

    ordered = _topological_order(validated)
    if len(ordered) == 2:
        first_keys = ordered[0]["joinKeys"]
        second_keys = ordered[1]["joinKeys"]
        if bool(first_keys) != bool(second_keys):
            raise ValueError(
                "hybrid joinKeys must be present on both sources or neither"
            )
        if first_keys and first_keys != second_keys:
            raise ValueError("hybrid joinKeys must have the same ordered aliases")
    return ordered


def parse_route_draft(
    content: str,
    query: str,
    *,
    shared_join_aliases: set[str] | frozenset[str] = DEFAULT_SHARED_JOIN_ALIASES,
    catalog: QuerySemanticCatalog | None = None,
) -> RouteDraft:
    """엄격한 route 객체 하나를 파싱하고 DAG에서 실행 순서를 파생한다."""
    raw = json.loads(content)
    if not isinstance(raw, dict) or set(raw) != {"subqueries", "resultTransform"}:
        raise ValueError(
            "route_query response must contain only subqueries and resultTransform"
        )
    subqueries = validate_route_subqueries(
        raw["subqueries"],
        shared_join_aliases=shared_join_aliases,
        catalog=catalog,
    )
    transform = _validate_transform_shape(raw["resultTransform"])
    tool_plan = derive_tool_plan(subqueries)
    if transform is not None:
        explicit_quantity = explicit_production_quantity(query)
        if explicit_quantity is not None and float(transform["productionQty"]) != float(
            explicit_quantity
        ):
            raise ValueError(
                "bom_shortage_v1 productionQty does not match the explicit "
                f"production quantity {explicit_quantity}"
            )
        _validate_bom_shortage_route(subqueries)
    result: RouteDraft = {"tool_plan": tool_plan, "subqueries": subqueries}
    if transform is not None:
        result["resultTransform"] = transform
    return result


def _validate_bom_shortage_route(subqueries: list[RouteSubquery]) -> None:
    if [item["tool"] for item in subqueries] != ["graph", "sql"]:
        raise ValueError("bom_shortage_v1 requires a Graph -> SQL route")
    graph, sql = subqueries
    if sql["dependsOn"] != [graph["id"]]:
        raise ValueError("bom_shortage_v1 SQL must depend on its Graph producer")
    if graph["joinKeys"] != ["componentId"] or sql["joinKeys"] != ["componentId"]:
        raise ValueError("bom_shortage_v1 join key must be componentId on both sources")
    if sql.get("inputBindings") != {"componentIds": f"{graph['id']}.componentId"}:
        raise ValueError("bom_shortage_v1 componentIds binding is invalid")


def validate_subqueries(
    subqueries: Any,
    *,
    allow_empty_required_outputs: bool = False,
) -> list[Subquery]:
    """의미를 다시 해석하지 않고 컴파일된 실행 계약을 검증한다."""
    if not isinstance(subqueries, list) or not subqueries:
        raise ValueError("subqueries must be a non-empty array")
    if len(subqueries) > MAX_SUBQUERIES:
        raise ValueError(f"at most {MAX_SUBQUERIES} subqueries are supported")
    validated: list[Subquery] = []
    for index, raw in enumerate(subqueries):
        if not isinstance(raw, dict):
            raise ValueError(f"subqueries[{index}] must be an object")
        allowed_fields = {
            "id",
            "tool",
            "question",
            "dependsOn",
            "requiredOutputs",
            "joinKeys",
            "inputBindings",
            "generatorRules",
        }
        if unknown_fields := set(raw) - allowed_fields:
            raise ValueError(
                f"subqueries[{index}] contains unknown execution fields: "
                + ", ".join(sorted(unknown_fields))
            )
        subquery_id = raw.get("id")
        if (
            not isinstance(subquery_id, str)
            or _SAFE_IDENTIFIER.fullmatch(subquery_id) is None
        ):
            raise ValueError(f"subqueries[{index}].id is not a safe identifier")
        tool = raw.get("tool")
        if tool not in SUPPORTED_TOOLS:
            raise ValueError(f"subquery {subquery_id!r} has unsupported tool {tool!r}")
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"subquery {subquery_id!r} question is empty")
        depends_on = _string_list(raw.get("dependsOn", []), "dependsOn", subquery_id)
        outputs = _string_list(
            raw.get("requiredOutputs", []), "requiredOutputs", subquery_id
        )
        if not outputs and not allow_empty_required_outputs:
            raise ValueError(f"subquery {subquery_id!r} requiredOutputs is empty")
        join_keys = _string_list(raw.get("joinKeys", []), "joinKeys", subquery_id)
        if not set(join_keys).issubset(outputs):
            raise ValueError(
                f"subquery {subquery_id!r} joinKeys must be required outputs"
            )
        raw_bindings = raw.get("inputBindings", {})
        if not isinstance(raw_bindings, dict):
            raise ValueError(
                f"subquery {subquery_id!r} inputBindings must be an object"
            )
        bindings: dict[str, str] = {}
        for target, source in raw_bindings.items():
            if (
                not isinstance(target, str)
                or _SAFE_IDENTIFIER.fullmatch(target) is None
                or not isinstance(source, str)
                or source.count(".") != 1
            ):
                raise ValueError(f"subquery {subquery_id!r} has an invalid binding")
            bindings[target] = source
        item: Subquery = {
            "id": subquery_id,
            "tool": cast(str, tool),
            "question": question,
            "dependsOn": depends_on,
            "requiredOutputs": outputs,
            "joinKeys": join_keys,
        }
        if bindings:
            item["inputBindings"] = bindings
        generator_rules = raw.get("generatorRules", [])
        if generator_rules:
            item["generatorRules"] = _string_list(
                generator_rules, "generatorRules", subquery_id
            )
        validated.append(item)

    by_id = {item["id"]: item for item in validated}
    if len(by_id) != len(validated):
        raise ValueError("subquery IDs must be unique")
    tools = [item["tool"] for item in validated]
    if len(tools) != len(set(tools)):
        raise ValueError("only one subquery per source is supported")
    for item in validated:
        binding_dependencies: set[str] = set()
        for dependency_id in item["dependsOn"]:
            if dependency_id == item["id"] or dependency_id not in by_id:
                raise ValueError(
                    f"subquery {item['id']!r} references an unknown dependency"
                )
        for source in item.get("inputBindings", {}).values():
            dependency_id, output_field = source.split(".", 1)
            binding_dependencies.add(dependency_id)
            if dependency_id not in item["dependsOn"]:
                raise ValueError(
                    f"subquery {item['id']!r} binding {source!r} is not in dependsOn"
                )
            if output_field not in by_id[dependency_id]["requiredOutputs"]:
                raise ValueError(
                    f"binding {source!r} is not a producer required output"
                )
        if set(item["dependsOn"]) != binding_dependencies:
            raise ValueError(
                f"subquery {item['id']!r} dependencies must be referenced by bindings"
            )

    ordered = _topological_order(validated)
    if len(ordered) == 2:
        first_keys = ordered[0]["joinKeys"]
        second_keys = ordered[1]["joinKeys"]
        if bool(first_keys) != bool(second_keys):
            raise ValueError(
                "hybrid joinKeys must be present on both sources or neither"
            )
        if first_keys and first_keys != second_keys:
            raise ValueError("hybrid joinKeys must have the same ordered aliases")
    return ordered


def validate_result_transform(
    raw_transform: Any,
    subqueries: list[Subquery],
    *,
    catalog: QuerySemanticCatalog | None = None,
) -> BomShortageTransform | None:
    """허용 목록의 formal transform과 구조적 source 계약을 검증한다."""
    transform = _validate_transform_shape(raw_transform)
    if transform is None:
        return None
    if [item["tool"] for item in subqueries] != ["graph", "sql"]:
        raise ValueError("bom_shortage_v1 requires a Graph -> SQL plan")
    graph, sql = subqueries
    graph_route: RouteSubquery = {
        "id": graph["id"],
        "tool": graph["tool"],
        "question": graph["question"],
        "dependsOn": graph["dependsOn"],
        "joinKeys": graph["joinKeys"],
    }
    sql_route: RouteSubquery = {
        "id": sql["id"],
        "tool": sql["tool"],
        "question": sql["question"],
        "dependsOn": sql["dependsOn"],
        "joinKeys": sql["joinKeys"],
    }
    if graph.get("inputBindings"):
        graph_route["inputBindings"] = graph["inputBindings"]
    if sql.get("inputBindings"):
        sql_route["inputBindings"] = sql["inputBindings"]
    _validate_bom_shortage_route([graph_route, sql_route])
    if catalog is None:
        raise ValueError("formal transform validation requires a semantic catalog")
    spec = catalog.transform("bom_shortage_v1")
    expected_graph = frozenset(spec.required_outputs["graph"])
    expected_sql = frozenset(spec.required_outputs["sql"])
    if set(graph["requiredOutputs"]) != expected_graph:
        raise ValueError("bom_shortage_v1 Graph requiredOutputs are invalid")
    if set(sql["requiredOutputs"]) != expected_sql:
        raise ValueError("bom_shortage_v1 SQL requiredOutputs are invalid")
    return transform


__all__ = [
    "BomShortageTransform",
    "DEFAULT_SHARED_JOIN_ALIASES",
    "ExecutionPlan",
    "InputBindingDraft",
    "MAX_SUBQUERIES",
    "ROUTE_DRAFT_JSON_SCHEMA",
    "RouteDraft",
    "RouteSubquery",
    "SUPPORTED_TOOLS",
    "Subquery",
    "derive_tool_plan",
    "explicit_production_quantity",
    "parse_route_draft",
    "route_draft_json_schema",
    "validate_result_transform",
    "validate_route_subqueries",
    "validate_subqueries",
]
