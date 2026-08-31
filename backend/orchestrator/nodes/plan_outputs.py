"""Schema-aware canonical output planning between routing and execution."""

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from orchestrator.output_catalog import OutputCatalog, ToolName
from orchestrator.planning import (
    RouteSubquery,
    Subquery,
    validate_result_transform,
    validate_subqueries,
)
from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """당신은 제조 데이터 질의의 schema-aware output planner입니다.
라우팅은 이미 확정됐습니다. 한 subquery가 실제로 반환해야 하는 canonical alias를
허용 목록에서 빠짐없이 고르세요.

규칙:
- 질문의 답으로 표시되는 identity, 이름, scalar, 집계, 경로 필드를 모두 포함합니다.
- 필터나 정렬에만 쓰고 답에 표시하지 않는 필드는 넣지 않습니다.
- 값이 NULL·미등록·비어 있음인지 묻는 목록에서는 그 상태 필드도 반환합니다.
- alias의 source ownership을 바꾸거나 새 alias를 만들지 않습니다.
- join key와 downstream binding source는 다음 deterministic merge에서 추가되지만,
  질문의 답에도 필요하다면 그대로 선택해도 됩니다.
- 동일 alias를 중복하지 않고 자연스러운 결과 열 순서로 반환합니다.
- 평가 ID, fixture, Gold query를 추론하거나 언급하지 않습니다.

canonical 관례:
- SQL의 제품 행은 productId, productName을 사용합니다. finishedProductId나
  rootProductId는 GRAPH에서 관계상 역할을 구분할 때만 사용합니다.
- SQL에서 분류·공급업체·작업장·작업지시·폐기사유별 행을 반환하면 해당 ID와
  이름을 집계값과 함께 반환합니다.
- 이름 alias를 선택하면 같은 entity의 ID alias도 선택하고, ID alias를 선택하면
  허용 목록에 있는 이름 alias도 함께 선택합니다.
- 이름으로 지정된 분류처럼 결과가 한 그룹뿐이어도 그 그룹의 ID와 이름을
  집계값과 함께 반환합니다.
- 위치별 원본 재고는 productId, productName, locationId, locationName, shelf,
  bin, quantity를 반환합니다. 합산 재고는 actualStock을 사용합니다.
- 부족량 답은 safetyStockLevel, actualStock, shortageQty의 계산 근거를 함께
  반환합니다.
- workOrderId가 결과 행의 기준이면 폐기량은 scrappedQty입니다. 여러 작업지시를
  제품 등으로 묶어 workOrderId 없이 합산한 값에만 totalScrappedQty를 사용합니다.
- GRAPH BOM 경로는 시작·종료 entity의 ID와 이름, depth, pathProductIds를
  반환하고 경로 이름이 필요한 계층·연결 설명에는 pathProductNames도 포함합니다.
- rootProductId/rootProductName은 하나의 조립품을 루트로 전체 하위 계층을
  펼칠 때 사용합니다. 지정 완제품에서 부품까지의 연결 경로와 공급 영향 경로는
  finishedProductId/finishedProductName을 사용합니다.
- GRAPH 작업지시 공정은 workOrderId, routingOperationKey, sequence, locationId,
  locationName을 한 묶음으로 반환합니다.
- GRAPH에서 작업장을 거친 제품을 찾으면 locationId, locationName, productId,
  productName을 반환합니다.
- GRAPH의 최하위 BOM 부품은 componentId, componentName, minDepth를 반환합니다.
- 공급 중단의 BOM 영향 경로는 finishedProductId, finishedProductName,
  componentId, componentName, depth, pathProductIds를 반환합니다.
- 두 시작점의 공통 부품은 finishedProductIdA, finishedProductIdB, componentId,
  componentName, minDepthA, minDepthB를 반환합니다.
- workOrderId가 없는 제품·작업장 단위 폐기 합계는 workOrderCount와
  totalScrappedQty를 함께 반환합니다.
- JSON을 쓰기 전에 결과 행의 기준 entity와 계산 근거를 내부적으로 점검하고,
  빠진 ID·이름·경로·집계 근거가 없는지 한 번 확인합니다.

예시:
- SQL "완제품 Vega Bolt의 색상과 표준원가" ->
  ["productId", "productName", "color", "standardCost"]
- SQL "현재 활성 공급업체 수" -> ["activeSupplierCount"]
- GRAPH "Orion Assembly의 하위 부품 경로" ->
  ["rootProductId", "rootProductName", "componentId", "componentName",
   "depth", "pathProductIds", "pathProductNames"]
"""


class OutputPlanningError(ValueError):
    """Keep the failed model response for diagnostics."""

    def __init__(self, message: str, raw_response: str) -> None:
        super().__init__(message)
        self.raw_response = raw_response


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
            }
        },
        "required": ["requiredOutputs"],
        "additionalProperties": False,
    }


def _parse_outputs(content: str, *, tool: str, catalog: OutputCatalog) -> list[str]:
    raw = json.loads(content)
    if not isinstance(raw, dict) or set(raw) != {"requiredOutputs"}:
        raise ValueError(
            "output planner 응답은 requiredOutputs만 가진 객체여야 합니다."
        )
    outputs = raw["requiredOutputs"]
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("requiredOutputs는 비어 있지 않은 문자열 배열이어야 합니다.")
    if any(not isinstance(alias, str) or not alias for alias in outputs):
        raise ValueError("requiredOutputs는 비어 있지 않은 문자열 배열이어야 합니다.")
    if len(outputs) != len(set(outputs)):
        raise ValueError("requiredOutputs에 중복 alias가 있습니다.")
    unknown = set(outputs) - set(catalog.allowed_aliases(tool))
    if unknown:
        raise ValueError(
            f"{tool} source ownership을 위반한 unknown alias: "
            + ", ".join(sorted(unknown))
        )
    return outputs


def _ordered_union(*groups: list[str]) -> list[str]:
    result: list[str] = []
    for group in groups:
        for item in group:
            if item not in result:
                result.append(item)
    return result


_IDENTITY_NAME_PAIRS = (
    ("productId", "productName"),
    ("componentId", "componentName"),
    ("finishedProductId", "finishedProductName"),
    ("rootProductId", "rootProductName"),
    ("supplierId", "supplierName"),
    ("supplierIdA", "supplierNameA"),
    ("supplierIdB", "supplierNameB"),
    ("categoryId", "categoryName"),
    ("locationId", "locationName"),
    ("scrapReasonId", "scrapReasonName"),
)

_SQL_ENTITY_ALIASES = {
    "productId": "productId",
    "productName": "productName",
    "productCategoryId": "categoryId",
    "productCategoryName": "categoryName",
    "supplierId": "supplierId",
    "supplierName": "supplierName",
    "locationId": "locationId",
    "locationName": "locationName",
    "scrapReasonId": "scrapReasonId",
    "scrapReasonName": "scrapReasonName",
    "workOrderId": "workOrderId",
}


def _resolved_entity_outputs(entity: object, tool: str, question: str) -> list[str]:
    values = entity if isinstance(entity, list) else [entity]
    mappings = dict(_SQL_ENTITY_ALIASES)
    if tool == "graph":
        mappings = {
            key: alias
            for key, alias in mappings.items()
            if key.startswith(("location", "supplier", "scrapReason", "workOrder"))
        }
    elif tool != "sql":
        return []

    relevant_values = []
    folded_question = question.casefold()
    for value in values:
        if not isinstance(value, dict):
            continue
        names = [
            item
            for key, item in value.items()
            if key.endswith("Name") and isinstance(item, str) and item
        ]
        ids = [
            item
            for key, item in value.items()
            if key.endswith("Id") and isinstance(item, int | str)
        ]
        if any(name.casefold() in folded_question for name in names) or any(
            str(item) in question for item in ids
        ):
            relevant_values.append(value)
    return _ordered_union(
        *[
            [
                alias
                for key, alias in mappings.items()
                if isinstance(value, dict) and value.get(key) is not None
            ]
            for value in relevant_values
        ]
    )


def _full_hierarchy_requested(question: str, entity: object) -> bool:
    folded = question.casefold()
    entity_count = len(entity) if isinstance(entity, list) else int(entity is not None)
    return (
        entity_count <= 1
        and any(
            token in folded
            for token in (
                "부품 트리",
                "계층",
                "hierarch",
                " tree",
                "펼쳐",
                "아래로",
            )
        )
        and not any(token in folded for token in ("최하위", "말단", "leaf"))
    )


def _common_component_summary_requested(question: str, entity: object) -> bool:
    if not isinstance(entity, list):
        return False
    product_count = sum(
        isinstance(item, dict) and item.get("productId") is not None for item in entity
    )
    folded = question.casefold()
    return (
        product_count >= 2
        and any(token in folded for token in ("공통", "모두 들어", "common", "교집합"))
        and not any(token in folded for token in ("경로", "path"))
    )


def _normalized_term(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _search_tokens(value: str) -> set[str]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return {
        token.casefold()
        for token in re.split(r"[^0-9A-Za-z가-힣]+", expanded)
        if len(token) >= 2
    }


def _missing_value_target(
    *,
    tool: str,
    context: str,
    catalog: OutputCatalog,
) -> tuple[str | None, set[str]]:
    """Resolve a nullable property only when schema and owner context are decisive."""
    if tool == "sql":
        source: ToolName = "sql"
    elif tool == "graph":
        source = "graph"
    else:
        raise ValueError(f"지원하지 않는 output source입니다: {tool!r}")
    identity_aliases = catalog.identity_aliases_by_tool[source]
    folded_context = context.casefold()
    normalized_context = _normalized_term(context)
    candidates: set[str] = set()
    direct_matches: set[str] = set()

    for alias, spec in catalog.by_tool[source].items():
        if (
            spec.calculation_type != "physical"
            or not spec.nullable
            or alias in identity_aliases
            or alias.endswith("Name")
        ):
            continue
        terms = tuple(term for term in spec.search_terms if term.strip())
        if any(
            (normalized := _normalized_term(term)) and normalized in normalized_context
            for term in terms
        ):
            direct_matches.add(alias)
        if any(
            token in folded_context for term in terms for token in _search_tokens(term)
        ):
            candidates.add(alias)

    selectable = direct_matches or candidates
    if len(selectable) == 1:
        return next(iter(selectable)), candidates | direct_matches
    if not selectable:
        return None, set()

    owner_matches = {
        alias
        for alias in selectable
        if any(
            (normalized := _normalized_term(owner)) and normalized in normalized_context
            for owner in catalog.by_tool[source][alias].owner_terms
        )
    }
    if len(owner_matches) == 1:
        return next(iter(owner_matches)), candidates | direct_matches
    return None, candidates | direct_matches


def _owner_qualified_property_outputs(
    *,
    tool: str,
    context: str,
    catalog: OutputCatalog,
) -> list[str]:
    """Recover explicit schema properties split into owner + short property words.

    Korean users commonly shorten ``제품번호`` to ``제품 ... 번호``. A property is
    completed only when one of its schema search terms is a compound beginning with
    an owner alias and both halves occur in the question. This avoids a broad keyword
    table and remains tied to the loaded schema.
    """
    if tool == "sql":
        source: ToolName = "sql"
    elif tool == "graph":
        source = "graph"
    else:
        raise ValueError(f"지원하지 않는 output source입니다: {tool!r}")
    normalized_context = _normalized_term(context)
    identity_aliases = catalog.identity_aliases_by_tool[source]
    matches: list[str] = []
    for alias, spec in catalog.by_tool[source].items():
        if (
            spec.calculation_type != "physical"
            or alias in identity_aliases
            or alias.endswith("Name")
        ):
            continue
        matched = False
        for owner in spec.owner_terms:
            normalized_owner = _normalized_term(owner)
            if len(normalized_owner) < 2 or normalized_owner not in normalized_context:
                continue
            for term in spec.search_terms:
                normalized_term = _normalized_term(term)
                if not normalized_term.startswith(normalized_owner):
                    continue
                remainder = normalized_term[len(normalized_owner) :]
                if len(remainder) >= 2 and remainder in normalized_context:
                    matched = True
                    break
            if matched:
                break
        if matched:
            matches.append(alias)
    return matches


def _complete_outputs(
    outputs: list[str],
    *,
    tool: str,
    entity: object,
    question: str,
    original_question: str,
    join_keys: list[str],
    catalog: OutputCatalog,
) -> list[str]:
    allowed = set(catalog.allowed_aliases(tool))
    normalized = list(outputs)
    original = original_question.casefold()
    context = f"{original_question} {question}".casefold()

    if tool == "sql":
        normalized = [
            (
                "productId"
                if alias
                in {
                    "finishedProductId",
                    "finishedProductIdA",
                    "finishedProductIdB",
                    "rootProductId",
                }
                else (
                    "productName"
                    if alias in {"finishedProductName", "rootProductName"}
                    else alias
                )
            )
            for alias in normalized
        ]
        normalized = _ordered_union(normalized)

    normalized = _ordered_union(
        normalized,
        _owner_qualified_property_outputs(
            tool=tool,
            context=context,
            catalog=catalog,
        ),
    )

    if any(
        marker in context
        for marker in (
            "null",
            "미등록",
            "등록되지",
            "비어",
            "값이 없",
            "not set",
            "missing",
        )
    ):
        missing_alias, equivalent_candidates = _missing_value_target(
            tool=tool,
            context=context,
            catalog=catalog,
        )
        if missing_alias is not None:
            normalized = [
                alias
                for alias in normalized
                if alias not in equivalent_candidates or alias == missing_alias
            ]
            normalized = _ordered_union(normalized, [missing_alias])

    if tool == "sql" and any(
        token in context
        for token in (
            "외부 구매",
            "구매 부품",
            "구매 제품",
            "자체 생산 대상이 아닌",
            "자체 생산하지",
            "externally purchased",
            "not made",
        )
    ):
        normalized = [
            "purchasedProductCount" if alias == "productCount" else alias
            for alias in normalized
        ]

    location_detail = any(
        token in original
        for token in (
            "위치",
            "위치별",
            "어디",
            "작업장",
            "창고",
            "선반",
            "where",
            "shelf",
            "bin",
        )
    )
    inventory_requested = any(
        token in context for token in ("재고", "stock", "inventory")
    )
    if tool == "sql" and inventory_requested:
        if location_detail:
            normalized = [alias for alias in normalized if alias != "actualStock"]
            normalized = _ordered_union(
                normalized,
                [
                    "productId",
                    "productName",
                    "locationId",
                    "locationName",
                    "shelf",
                    "bin",
                    "quantity",
                ],
            )
        else:
            normalized = [
                alias
                for alias in normalized
                if alias
                not in {"locationId", "locationName", "shelf", "bin", "quantity"}
            ]
            normalized = _ordered_union(normalized, ["actualStock"])

    if tool == "graph" and _full_hierarchy_requested(original_question, entity):
        normalized = [
            (
                "rootProductId"
                if alias == "finishedProductId"
                else "rootProductName" if alias == "finishedProductName" else alias
            )
            for alias in normalized
        ]
        normalized = ["depth" if alias == "minDepth" else alias for alias in normalized]
        normalized = _ordered_union(normalized)
        normalized = _ordered_union(
            normalized,
            [
                "rootProductId",
                "rootProductName",
                "componentId",
                "componentName",
                "depth",
                "pathProductIds",
                "pathProductNames",
            ],
        )
    if tool == "graph" and _common_component_summary_requested(
        original_question, entity
    ):
        normalized = [
            "finishedProductIdA",
            "finishedProductIdB",
            "componentId",
            "componentName",
            "minDepthA",
            "minDepthB",
        ]
    if "workOrderId" in normalized and "totalScrappedQty" in normalized:
        index = normalized.index("totalScrappedQty")
        normalized[index] = "scrappedQty"
        normalized = _ordered_union(normalized)

    completed = _ordered_union(
        normalized,
        _resolved_entity_outputs(entity, tool, question),
    )
    selected = set(completed)
    if tool == "graph" and {"routingOperationKey", "sequence"}.issubset(selected):
        completed = _ordered_union(completed, ["workOrderId"])
        selected.add("workOrderId")
    for identity, name in _IDENTITY_NAME_PAIRS:
        if identity in allowed and name in allowed and selected & {identity, name}:
            completed = _ordered_union(completed, [identity, name])
            selected.update((identity, name))

    if (
        tool == "sql"
        and "workOrderId" in selected | set(join_keys)
        and selected
        & {
            "scrappedQty",
            "scrapReasonId",
            "scrapReasonName",
        }
    ):
        completed = [
            alias for alias in completed if alias not in {"locationId", "locationName"}
        ]
        completed = _ordered_union(
            completed,
            [
                "productId",
                "productName",
                "scrapReasonId",
                "scrapReasonName",
                "scrappedQty",
            ],
        )
    if (
        tool == "sql"
        and "totalScrappedQty" in selected
        and "workOrderId" not in selected
    ):
        completed = _ordered_union(completed, ["workOrderCount"])
    if tool == "graph" and {"componentId", "finishedProductId"}.issubset(selected):
        completed = _ordered_union(completed, ["depth", "pathProductIds"])
    if tool == "graph" and "pathProductIds" in completed:
        completed = _ordered_union(completed, ["pathProductNames"])
    return completed


def _outgoing_binding_outputs(
    route_subqueries: list[RouteSubquery],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {item["id"]: [] for item in route_subqueries}
    for item in route_subqueries:
        for source in item.get("inputBindings", {}).values():
            source_id, alias = source.split(".", 1)
            if alias not in result[source_id]:
                result[source_id].append(alias)
    return result


def _with_required_outputs(
    route_subquery: RouteSubquery,
    planned_outputs: list[str],
    outgoing_outputs: list[str],
) -> Subquery:
    outputs = _ordered_union(
        planned_outputs,
        route_subquery["joinKeys"],
        outgoing_outputs,
    )
    item: Subquery = {
        "id": route_subquery["id"],
        "tool": route_subquery["tool"],
        "question": route_subquery["question"],
        "dependsOn": route_subquery["dependsOn"],
        "requiredOutputs": outputs,
        "joinKeys": route_subquery["joinKeys"],
    }
    if route_subquery.get("inputBindings"):
        item["inputBindings"] = dict(route_subquery["inputBindings"])
    return item


def _validate_source_outputs(subquery: Subquery, catalog: OutputCatalog) -> None:
    allowed = set(catalog.allowed_aliases(subquery["tool"]))
    unknown = set(subquery["requiredOutputs"]) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(
            f"subquery {subquery['id']!r}의 {subquery['tool']} source ownership을 "
            f"위반한 output alias: {names}"
        )


def _bom_shortage_outputs(tool: str) -> list[str]:
    if tool == "graph":
        return [
            "finishedProductId",
            "finishedProductName",
            "componentId",
            "componentName",
            "depth",
            "pathProductIds",
            "quantityPerAssembly",
            "supplierId",
            "supplierName",
        ]
    return ["componentId", "makeFlag", "actualStock"]


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
            raise ValueError("routeDraft가 없어 output planning을 수행할 수 없습니다.")
        draft = route_draft
        route_subqueries = draft.get("subqueries")
        if not isinstance(route_subqueries, list):
            raise ValueError("routeDraft.subqueries가 배열이 아닙니다.")
        if len(route_subqueries) > 1 and all(
            not subquery.get("dependsOn") for subquery in route_subqueries
        ):
            planned_groups = await asyncio.gather(
                *(
                    plan_outputs(
                        {
                            **state,
                            "tool_plan": [subquery["tool"]],
                            "routeDraft": {
                                "tool_plan": [subquery["tool"]],
                                "subqueries": [subquery],
                            },
                            "resultTransform": None,
                        }
                    )
                    for subquery in route_subqueries
                )
            )
            combined = [group["subqueries"][0] for group in planned_groups]
            validated = validate_subqueries(combined)
            logger.info(
                "plan_outputs: independent subqueries=%s outputs=%s",
                [item["id"] for item in validated],
                [item["requiredOutputs"] for item in validated],
            )
            return {"subqueries": validated, "resultTransform": None}
        outgoing = _outgoing_binding_outputs(route_subqueries)
        transform = state.get("resultTransform")
        planned: list[Subquery] = []

        for route_subquery in route_subqueries:
            tool = route_subquery["tool"]
            execution_subquery = route_subquery
            if (
                isinstance(transform, dict)
                and transform.get("type") == "bom_shortage_v1"
            ):
                selected = _bom_shortage_outputs(tool)
                if tool == "graph":
                    execution_subquery = {
                        **route_subquery,
                        "question": (
                            f"{route_subquery['question']} 공급업체가 없는 부품도 "
                            "보존하고 active 공급업체를 선택적으로 연결한다. 모든 유효 "
                            "BOM component 경로를 보존하고 sellableFinishedGood로 사전 "
                            "필터하지 않는다. pathProductIds는 nodes(path) 순서로 "
                            "finishedProduct에서 component 방향으로 반환하고 reverse하지 "
                            "않는다. quantityPerAssembly도 relationships(path) 순서를 "
                            "유지한다. 외부 구매 "
                            "판정은 SQL makeFlag에서 수행한다."
                        ),
                    }
                else:
                    execution_subquery = {
                        **route_subquery,
                        "question": (
                            "앞 단계 componentId별 makeFlag와 현재 재고 합계를 "
                            "조회한다. 필요한 수량과 부족량은 resultTransform에서 "
                            "계산한다."
                        ),
                    }
            else:
                schema_description = catalog.describe(tool)
                entity_json = json.dumps(state.get("entity"), ensure_ascii=False)
                user_content = (
                    f"source: {tool}\n"
                    f"original question: {state['query']}\n"
                    f"subquery: {route_subquery['question']}\n"
                    f"resolved entity: {entity_json}\n"
                    f"join keys: {json.dumps(route_subquery['joinKeys'])}\n"
                    "허용 canonical aliases:\n"
                    f"{schema_description}\n"
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
                selected = []
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
                            raise ValueError("output planner가 빈 응답을 반환했습니다.")
                        selected = _parse_outputs(
                            content,
                            tool=tool,
                            catalog=catalog,
                        )
                        break
                    except ValueError as exc:
                        if attempt == 1:
                            raise OutputPlanningError(str(exc), last_content) from exc
                        messages = [
                            *messages,
                            {"role": "assistant", "content": last_content},
                            {
                                "role": "user",
                                "content": (
                                    "위 출력 계획이 검증에 실패했습니다: "
                                    f"{exc}\n허용 alias만 사용해 JSON 객체 전체를 "
                                    "다시 생성하세요."
                                ),
                            },
                        ]
                selected = _complete_outputs(
                    selected,
                    tool=tool,
                    entity=state.get("entity"),
                    question=route_subquery["question"],
                    original_question=state["query"],
                    join_keys=route_subquery["joinKeys"],
                    catalog=catalog,
                )
                if tool == "graph" and _full_hierarchy_requested(
                    state["query"], state.get("entity")
                ):
                    execution_subquery = {
                        **route_subquery,
                        "question": state["query"],
                    }
                if tool == "graph" and _common_component_summary_requested(
                    state["query"], state.get("entity")
                ):
                    execution_subquery = {
                        **route_subquery,
                        "question": state["query"],
                    }
                output_roles = set(selected)
                entity = state.get("entity")
                if tool == "sql" and "totalRejectedQty" in output_roles:
                    execution_subquery = {
                        **execution_subquery,
                        "question": (
                            f"{route_subquery['question']} 공급업체별 순위 기준은 "
                            "purchaseorderid 건수가 아니라 rejectedqty 합계이며, "
                            "totalRejectedQty를 내림차순으로 정렬한다."
                        ),
                    }
                if (
                    tool == "graph"
                    and isinstance(entity, list)
                    and sum(
                        isinstance(item, dict) and item.get("productId") is not None
                        for item in entity
                    )
                    >= 2
                    and {
                        "finishedProductId",
                        "componentId",
                        "pathProductIds",
                    }.issubset(output_roles)
                ):
                    execution_subquery = {
                        **execution_subquery,
                        "question": (
                            f"{state['query']} pathProductIds와 pathProductNames는 "
                            "nodes(path) 순서로 finishedProduct에서 component 방향으로 "
                            "반환하고 reverse하지 않는다."
                        ),
                    }
                if (
                    tool == "graph"
                    and isinstance(entity, dict)
                    and entity.get("supplierId") is not None
                    and {
                        "componentId",
                        "finishedProductId",
                        "pathProductIds",
                    }.issubset(output_roles)
                ):
                    execution_subquery = {
                        **execution_subquery,
                        "question": (
                            f"{state['query']} pathProductIds는 component에서 "
                            "finishedProduct 방향으로 반환한다."
                        ),
                    }
                if tool == "graph" and {
                    "workOrderId",
                    "routingOperationKey",
                    "sequence",
                }.issubset(output_roles):
                    execution_subquery = {
                        **execution_subquery,
                        "question": (
                            f"{state['query']} 질문의 숫자는 workOrderId이며 "
                            "Product.productId가 아니다."
                        ),
                    }
            completed_subquery = _with_required_outputs(
                execution_subquery,
                selected,
                outgoing[execution_subquery["id"]],
            )
            _validate_source_outputs(completed_subquery, catalog)
            planned.append(completed_subquery)

        validated = validate_subqueries(planned)
        validated_transform = validate_result_transform(transform, validated)
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
