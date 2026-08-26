"""라우터가 반환하는 실행 계획의 공통 타입과 검증 규칙."""

import json
from typing import Any, NotRequired, TypedDict

SUPPORTED_TOOLS = {"sql", "graph"}


class Subquery(TypedDict):
    """하나의 데이터 소스가 담당하는 독립 실행 단위."""

    id: str
    tool: str
    question: str
    dependsOn: list[str]
    requiredOutputs: list[str]
    joinKeys: list[str]
    inputBindings: NotRequired[dict[str, str]]


class ExecutionPlan(TypedDict):
    tool_plan: list[str]
    subqueries: list[Subquery]


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


def validate_subqueries(subqueries: Any) -> list[Subquery]:
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

        for source in item.get("inputBindings", {}).values():
            dependency_id, output_field = source.split(".", 1)
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

    return ordered


def _legacy_subqueries(tool_plan: list[str], query: str) -> list[Subquery]:
    """이전 JSON 배열 응답도 실행 가능하게 하되 평가에서는 계약 차이를 드러낸다."""
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


def parse_execution_plan(content: str, query: str) -> ExecutionPlan:
    """LLM의 신규 object 또는 기존 array 응답을 검증된 실행 계획으로 바꾼다."""
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

    subqueries = (
        _legacy_subqueries(tool_plan, query)
        if legacy
        else validate_subqueries(raw_subqueries)
    )
    validated = validate_subqueries(subqueries)
    planned_tools = list(dict.fromkeys(item["tool"] for item in validated))
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
    return {"tool_plan": tool_plan, "subqueries": validated}
