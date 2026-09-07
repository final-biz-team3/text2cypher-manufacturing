"""Select row/field references, then render values without model-authored facts."""

import re
from typing import Any

from orchestrator.grounded.model_calls import typed_call
from orchestrator.grounded.models import AnswerSelection, QueryIntent


def render_selection(
    selection: AnswerSelection,
    pools: dict[str, list[dict[str, Any]]],
    intent: QueryIntent,
) -> str:
    definitions = {o.alias: o for o in intent.outputs}
    used: set[tuple[str, int]] = set()
    selected: dict[str, list[dict[str, Any]]] = {}
    for item in selection.items:
        if item.section not in pools or item.row_index >= len(pools[item.section]):
            raise ValueError("Invalid answer row reference")
        key = (item.section, item.row_index)
        if key in used or len(item.fields) != len(set(item.fields)):
            raise ValueError("Duplicate answer reference")
        used.add(key)
        row = pools[item.section][item.row_index]
        required = set(definitions) & set(row)
        if not required <= set(item.fields) or not set(item.fields) <= set(row):
            raise ValueError(
                "Answer omits a requested field or references a missing one"
            )
        metrics = []
        for field in item.fields:
            if field not in definitions:
                continue
            definition = definitions[field]
            value = row[field]
            unit = f" ({definition.unit})" if definition.unit else ""
            metrics.append(
                {
                    "label": definition.label + unit,
                    "value": (
                        "NULL"
                        if value is None
                        else (
                            str(value)
                            if not isinstance(value, (str, int, float))
                            else value
                        )
                    ),
                }
            )
        if not metrics:
            raise ValueError("Answer item has no requested fields")
        selected.setdefault(item.section, []).append(
            {"title": None, "metrics": metrics}
        )
    if any(rows and section not in selected for section, rows in pools.items()):
        raise ValueError("Answer omits a nonempty section")

    def escape(value: Any) -> str:
        return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", str(value)).replace("\n", " ")

    lines = ["요청한 조건의 조회 결과를 확인했습니다."]
    for section, items in selected.items():
        if section:
            lines.extend(["", "조회 항목"])
        lines.append("")
        for rendered_item in items:
            lines.append(
                "- "
                + "; ".join(
                    f"{escape(m['label'])}: {escape(m['value'])}"
                    for m in rendered_item["metrics"]
                )
            )
    if any(not rows for rows in pools.values()):
        lines.extend(["", "일부 조회 항목에는 해당 조건의 데이터가 없습니다."])
    if len(used) < sum(len(rows) for rows in pools.values()):
        lines.extend(
            [
                "",
                "대표 항목을 요약했습니다. 전체 조회 결과는 결과 표에서 확인할 수 있습니다.",
            ]
        )
    return "\n".join(lines)


async def grounded_answer(
    client: Any, intent: QueryIntent, candidate: dict[str, Any]
) -> dict[str, Any]:
    composed = candidate["composed_result"]
    sections = composed.get("sections") or {}
    pools = (
        {key: value["rows"] for key, value in sections.items()}
        if sections
        else {"": composed.get("rows", [])}
    )
    metadata: dict[str, Any] = {
        "mode": "structured",
        "attemptCount": 0,
        "fallbackReason": None,
        "validationRejected": False,
        "references": [],
    }
    if not any(pools.values()):
        answer = "지정한 조건에 해당하는 조회 결과가 없습니다."
        metadata["mode"] = "fixed"
    else:
        try:
            metadata["attemptCount"] = 1
            selection = await typed_call(
                client,
                AnswerSelection,
                purpose="grounded.answer",
                system=(
                    "Select representative row indices and ALL requested fields present in each selected row. "
                    "Use at most ten rows across sections, cover each nonempty section. "
                    "Do not author facts, labels, units or values. Return references only."
                ),
                payload={
                    "outputs": [o.model_dump() for o in intent.outputs],
                    "sections": pools,
                },
            )
            answer = render_selection(selection, pools, intent)
        except Exception:
            # Deterministic selection preserves exact same-row field relationships.
            items = []
            for section, rows in pools.items():
                if rows:
                    items.append(
                        {
                            "section": section,
                            "row_index": 0,
                            "fields": [
                                o.alias for o in intent.outputs if o.alias in rows[0]
                            ],
                        }
                    )
            selection = AnswerSelection.model_validate({"items": items})
            answer = render_selection(selection, pools, intent)
            metadata.update(
                mode="fallback",
                fallbackReason="invalid_selection",
                validationRejected=True,
            )
        metadata["references"] = [item.model_dump() for item in selection.items]
    conditions = [
        r.evidence.text
        for r in intent.requirements
        if r.kind in {"filter", "period", "limit", "ordering", "grain", "relationship"}
    ]
    if conditions:
        answer += "\n\n적용 조건: " + "; ".join(dict.fromkeys(conditions))
    if intent.assumptions:
        answer += "\n\n문서상 기본값: " + "; ".join(
            f"{a.text} [{a.source}]" for a in intent.assumptions
        )
    if composed.get("truncated"):
        answer += "\n\n조회 한도에 도달해 전체 결과 중 일부만 표시했습니다."
    return {"final_answer": answer, "answer_metadata": metadata}
