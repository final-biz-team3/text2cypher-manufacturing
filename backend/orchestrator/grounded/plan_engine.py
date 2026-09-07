"""Bounded relational operations over request-local step results."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from functools import cmp_to_key
from typing import Any

from orchestrator.grounded.plan_models import (
    FinalResult,
    Meaning,
    Predicate,
    PublicColumn,
    PublicSection,
    QueryPlan,
    ResultOperation,
    SourceStep,
    StepResult,
)


class PlanError(ValueError):
    rejected_plan: dict[str, Any] | None = None


def validate_plan(plan: QueryPlan, meaning: Meaning) -> None:
    if plan.support != "executable":
        return
    if not plan.steps or not plan.sections:
        raise PlanError("Executable plans need source steps and final sections")
    ids = [s.id for s in plan.steps] + [s.id for s in plan.operations]
    if len(ids) != len(set(ids)):
        raise PlanError("Step IDs must be unique")
    output_ids = {o.id for o in meaning.outputs}
    req_ids = {r.id for r in meaning.requirements}
    covered: set[str] = set()
    known = {s.id for s in plan.steps}
    dependencies = {s.id: set(s.depends_on) for s in plan.steps}
    for step in plan.steps:
        covered.update(step.requirement_ids)
        if not set(step.requirement_ids) <= req_ids:
            raise PlanError("Unknown requirement reference")
        if not set(step.depends_on) <= known or step.id in step.depends_on:
            raise PlanError("Unknown or self dependency")
        names = [p.name for p in step.parameters] + [b.parameter for b in step.bindings]
        if len(names) != len(set(names)):
            raise PlanError("Duplicate parameter name")
        if any(b.source_step not in step.depends_on for b in step.bindings):
            raise PlanError("Bindings must name an explicit dependency")
        # Source projections are local metadata; internal join/aggregate columns
        # are not user outputs. Only final sections bind public output IDs.
        local_ids = [p.output_id for p in step.projections]
        if len(local_ids) != len(set(local_ids)):
            raise PlanError("Duplicate source output reference")
    finished: set[str] = set()
    while dependencies:
        ready = {k for k, v in dependencies.items() if v <= finished}
        if not ready:
            raise PlanError("Source dependency cycle")
        finished.update(ready)
        dependencies = {k: v for k, v in dependencies.items() if k not in ready}
    for op in plan.operations:
        if not set(op.inputs) <= known:
            raise PlanError("Operations must be topologically ordered")
        if not set(op.requirement_ids) <= req_ids:
            raise PlanError("Unknown operation requirement")
        covered.update(op.requirement_ids)
        if op.kind == "join":
            if (
                len(op.inputs) != 2
                or not op.join_type
                or not op.left_keys
                or len(op.left_keys) != len(op.right_keys)
            ):
                raise PlanError("Join requires two inputs and paired keys")
        elif len(op.inputs) != 1:
            raise PlanError("Unary operation requires one input")
        if op.kind == "filter" and op.predicate is None:
            raise PlanError("Filter predicate missing")
        if op.kind == "limit" and op.limit is None:
            raise PlanError("Limit missing")
        known.add(op.id)
    if covered != req_ids:
        raise PlanError("Plan does not assign every requirement")
    section_names = [section.id for section in plan.sections]
    if len(section_names) != len(set(section_names)):
        raise PlanError("Final section IDs must be unique")
    visible = set()
    for section in plan.sections:
        if section.input not in known:
            raise PlanError("Unknown final source")
        section_ids = [p.output_id for p in section.projections]
        if len(section_ids) != len(set(section_ids)):
            raise PlanError("Duplicate final output")
        visible.update(section_ids)
    if visible != output_ids:
        raise PlanError("Final sections must cover exactly the requested outputs")


def _value(row: dict[str, Any], field: str | None) -> Any:
    if field is None or field not in row:
        raise PlanError("Operation references a missing column")
    return row[field]


def predicate_value(predicate: Predicate, row: dict[str, Any]) -> bool | None:
    op = predicate.op
    if op in {"and", "or", "not"}:
        values = [predicate_value(p, row) for p in predicate.children]
        if not values or (op == "not" and len(values) != 1):
            raise PlanError("Invalid boolean predicate")
        if op == "not":
            return None if values[0] is None else not values[0]
        if op == "and":
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    left = _value(row, predicate.field)
    right: Any = predicate.value
    if op == "is_null":
        return left is None
    if left is None:
        return None
    if op == "in":
        return (
            True
            if left in predicate.values
            else None if None in predicate.values else False
        )
    if right is None:
        return None
    if isinstance(left, (datetime, date)) and isinstance(right, str):
        right = type(left).fromisoformat(right)
    operations = {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "lt": lambda: left < right,
        "le": lambda: left <= right,
        "gt": lambda: left > right,
        "ge": lambda: left >= right,
        "contains": lambda: str(right) in str(left),
    }
    return bool(operations[op]())


def _key(values: list[Any]) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, bool):
            return ["bool", value]
        if isinstance(value, (int, float, Decimal)):
            return ["number", str(Decimal(str(value)).normalize())]
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(v) for v in value]
        return [type(value).__name__, str(value)]

    return json.dumps(normalize(values), sort_keys=True, ensure_ascii=False)


def apply_operation(
    op: ResultOperation, results: dict[str, StepResult], cap: int
) -> StepResult:
    inputs = [results[name] for name in op.inputs]
    if any(s.truncated for s in inputs):
        raise PlanError(
            "Incomplete intermediate result cannot be filtered, joined, aggregated or ranked"
        )
    source = inputs[0]
    rows = source.rows
    columns = list(source.columns)
    if op.kind == "filter":
        assert op.predicate is not None
        rows = [r for r in rows if predicate_value(op.predicate, r) is True]
    elif op.kind == "project":
        if not set(op.columns) <= set(columns):
            raise PlanError("Missing projection column")
        columns = op.columns
        rows = [{k: r[k] for k in columns} for r in rows]
    elif op.kind == "distinct":
        keys = op.columns or columns
        if not set(keys) <= set(columns):
            raise PlanError("Missing distinct key")
        seen = set()
        distinct_rows = []
        for row in rows:
            key = _key([row[k] for k in keys])
            if key not in seen:
                seen.add(key)
                distinct_rows.append(row)
        rows = distinct_rows
    elif op.kind == "aggregate":
        if not set(op.columns) <= set(columns):
            raise PlanError("Missing grouping column")
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(_key([row[k] for k in op.columns]), []).append(row)
        if not rows and not op.columns:
            groups["[]"] = []
        output = []
        for members in groups.values():
            row = {k: members[0][k] for k in op.columns}
            for agg in op.aggregates:
                if agg.field is not None and agg.field not in columns:
                    raise PlanError("Missing aggregate column")
                values = (
                    [m[agg.field] for m in members if m[agg.field] is not None]
                    if agg.field
                    else [1 for _ in members]
                )
                if agg.distinct:
                    values = list({_key([v]): v for v in values}.values())
                if agg.function == "count":
                    value: Any = len(values)
                elif not values:
                    value = None
                elif agg.function in {"sum", "avg"}:
                    if any(
                        isinstance(v, bool) or not isinstance(v, (int, float, Decimal))
                        for v in values
                    ):
                        raise PlanError("Non-numeric aggregation")
                    value = sum(values)
                    if agg.function == "avg":
                        value /= len(values)
                else:
                    value = min(values) if agg.function == "min" else max(values)
                row[agg.output] = value
            output.append(row)
        rows = output
        columns = list(op.columns) + [a.output for a in op.aggregates]
    elif op.kind == "sort":
        if any(order.field not in columns for order in op.ordering):
            raise PlanError("Missing ordering column")

        def compare(a: dict[str, Any], b: dict[str, Any]) -> int:
            for order in op.ordering:
                left, right = a[order.field], b[order.field]
                if left is None or right is None:
                    delta = (
                        0
                        if left is right
                        else (-1 if left is None else 1)
                        * (1 if order.nulls_first else -1)
                    )
                else:
                    delta = ((left > right) - (left < right)) * (
                        -1 if order.descending else 1
                    )
                if delta:
                    return delta
            return 0

        rows = sorted(rows, key=cmp_to_key(compare))
    elif op.kind == "limit":
        rows = rows[: op.limit]
    elif op.kind == "join":
        right = inputs[1]
        if not set(op.left_keys) <= set(columns) or not set(op.right_keys) <= set(
            right.columns
        ):
            raise PlanError("Missing join column")
        index: dict[str, list[dict[str, Any]]] = {}
        for row in right.rows:
            values = [row[k] for k in op.right_keys]
            if all(v is not None for v in values):
                index.setdefault(_key(values), []).append(row)
        output = []
        for left in rows:
            values = [left[k] for k in op.left_keys]
            matches = (
                index.get(_key(values), [])
                if all(v is not None for v in values)
                else []
            )
            if op.join_type in {"semi", "anti"}:
                if bool(matches) == (op.join_type == "semi"):
                    output.append(dict(left))
            else:
                for match in matches or (
                    [dict.fromkeys(right.columns)] if op.join_type == "left" else []
                ):
                    merged = dict(left)
                    for key in right.columns:
                        if key in merged:
                            if key not in op.right_keys and match[key] != merged[key]:
                                raise PlanError(
                                    "Ambiguous joined output; alias source columns uniquely"
                                )
                        else:
                            merged[key] = match[key]
                    output.append(merged)
                    if len(output) > cap:
                        raise PlanError("Join exceeds complete intermediate row budget")
        rows = output
        if op.join_type not in {"semi", "anti"}:
            columns = list(dict.fromkeys(columns + right.columns))
    if len(rows) > cap:
        raise PlanError("Intermediate result exceeds row budget")
    return StepResult(
        step_id=op.id,
        columns=columns,
        rows=rows,
        truncated=False,
        query="",
        elapsed_ms=0,
    )


async def execute_plan(
    plan: QueryPlan,
    meaning: Meaning,
    execute: Callable[[SourceStep, dict[str, Any]], Awaitable[StepResult]],
    cap: int = 200,
) -> dict[str, StepResult]:
    validate_plan(plan, meaning)
    results: dict[str, StepResult] = {}
    pending = list(plan.steps)
    while pending:
        ready = [s for s in pending if set(s.depends_on) <= results.keys()]
        if not ready:
            raise PlanError("Unresolved dependency")

        async def run(step: SourceStep) -> StepResult:
            params: dict[str, Any] = {p.name: p.value for p in step.parameters}
            for binding in step.bindings:
                source = results[binding.source_step]
                if source.truncated:
                    raise PlanError("Binding source is incomplete")
                if not set(binding.fields) <= set(source.columns):
                    raise PlanError("Binding column missing")
                # Empty input is still executed: count(empty) is zero, not no rows.
                params[binding.parameter] = [
                    {f: row[f] for f in binding.fields} for row in source.rows
                ]
            return await execute(step, params)

        tasks = [asyncio.create_task(run(step)) for step in ready]
        try:
            completed = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        results.update({r.step_id: r for r in completed})
        pending = [s for s in pending if s not in ready]
    for op in plan.operations:
        results[op.id] = apply_operation(op, results, cap)
    return results


def final_result(
    plan: QueryPlan, meaning: Meaning, results: dict[str, StepResult]
) -> FinalResult:
    from orchestrator.grounded.validation import value_matches

    definitions = {o.id: o for o in meaning.outputs}
    sections = []
    for section in plan.sections:
        source = results[section.input]
        columns = []
        for projection in section.projections:
            output = definitions[projection.output_id]
            if projection.column not in source.columns:
                raise PlanError("Final output column missing")
            if any(
                not value_matches(
                    row[projection.column], output.value_type, output.nullable
                )
                for row in source.rows
            ):
                raise PlanError("Final output type mismatch")
            columns.append(
                PublicColumn(
                    id=output.id,
                    label=output.label,
                    value_type=output.value_type,
                    unit=output.unit,
                )
            )
        sections.append(
            PublicSection(
                id=section.id,
                title=section.title,
                columns=columns,
                rows=[
                    {p.output_id: row[p.column] for p in section.projections}
                    for row in source.rows
                ],
                truncated=source.truncated,
            )
        )
    return FinalResult(sections=sections, truncated=any(s.truncated for s in sections))
