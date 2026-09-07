"""Conservative capability admission and expression-based legacy adaptation."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from orchestrator.grounded.plan_engine import PlanError
from orchestrator.grounded.plan_models import (
    Meaning,
    Projection,
    QueryPlan,
    ResultSection,
    SourceStep,
)


@lru_cache(maxsize=1)
def _legacy_physical_fields() -> frozenset[str]:
    from strict_query.snapshot.agents.sql.schema.loader import load_sql_schema

    schema = load_sql_schema(
        Path(__file__).resolve().parents[2] / "strict_query/schema/sql_schema.yaml"
    )
    return frozenset(
        f"sql:{table}.{column}"
        for table, spec in schema.tables.items()
        for column in spec.columns
    )


def strict_eligible(meaning: Meaning, plan: QueryPlan, catalog: Any) -> bool:
    # The snapshot cannot represent arbitrary DAGs, request-local postprocessing,
    # dynamic aggregates or outputs outside its physical/semantic catalogue.
    if (
        len(plan.steps) != 1
        or plan.operations
        or plan.steps[0].bindings
        or plan.steps[0].tool != "sql"
    ):
        return False
    allowed_fields: set[str] = set()
    for spec in catalog.by_tool["sql"].values():
        allowed_fields.update("sql:" + path for path in spec.schema_paths)
    allowed_fields &= _legacy_physical_fields()
    if any(not set(o.source_fields) <= allowed_fields for o in meaning.outputs):
        return False
    # Expressions requiring application computation or unknown semantic operators
    # are sent straight to the general path, never matched by question words.
    supported_kinds = {
        "target",
        "output",
        "filter",
        "period",
        "ordering",
        "limit",
        "grain",
        "aggregation",
    }
    if any(
        r.kind not in supported_kinds
        or not set(r.fields) <= allowed_fields
        or r.aggregate is not None
        for r in meaning.requirements
    ):
        return False
    try:
        tree = sqlglot.parse_one(plan.steps[0].query, read="postgres")
        if (
            not isinstance(tree, exp.Select)
            or tree.args.get("with_")
            or list(tree.find_all(exp.Subquery))
            or list(tree.find_all(exp.Join))
        ):
            return False
        for projection in tree.expressions:
            body = projection.this if isinstance(projection, exp.Alias) else projection
            # Legacy aggregate names alone do not establish an equivalent
            # function, operand and grain. Such requests use the general plan.
            if not isinstance(body, exp.Column):
                return False
    except sqlglot.errors.ParseError:
        return False
    return True


def _projection_signatures(query: str) -> dict[str, str]:
    tree = sqlglot.parse_one(query, read="postgres")
    if not isinstance(tree, exp.Select):
        raise PlanError("Legacy candidate is not a select")
    tables = {
        t.alias_or_name.casefold(): f"{t.db}.{t.name}".strip(".").casefold()
        for t in tree.find_all(exp.Table)
    }
    signatures = {}
    for projection in tree.expressions:
        body = (
            projection.this if isinstance(projection, exp.Alias) else projection
        ).copy()
        for column in body.find_all(exp.Column):
            owner = (
                tables.get(column.table.casefold())
                if column.table
                else next(iter(tables.values())) if len(tables) == 1 else None
            )
            if owner is None:
                raise PlanError("Cannot establish legacy output provenance")
            column.set("table", exp.to_identifier(owner))
            column.set("this", exp.to_identifier(column.name.casefold()))
        alias = projection.alias_or_name
        if alias in signatures:
            raise PlanError("Duplicate legacy alias")
        signatures[alias] = body.sql(dialect="postgres", normalize=True)
    return signatures


def adapt_strict_plan(plan: QueryPlan, candidate: dict[str, Any]) -> QueryPlan:
    if (
        candidate.get("query_failure")
        or candidate.get("error")
        or not candidate.get("sql_query")
    ):
        raise PlanError("Strict candidate did not produce a query")
    expected = _projection_signatures(plan.steps[0].query)
    actual = _projection_signatures(candidate["sql_query"])
    mapping = {}
    for key, signature in expected.items():
        matches = [alias for alias, value in actual.items() if value == signature]
        if len(matches) != 1:
            raise PlanError("Legacy output has no unique expression-equivalent mapping")
        mapping[key] = matches[0]
    step = SourceStep(
        id="legacy",
        tool="sql",
        query=candidate["sql_query"],
        parameters=[],
        bindings=[],
        depends_on=[],
        requirement_ids=list(plan.steps[0].requirement_ids),
        relationships=[],
        projections=[
            Projection(output_id=p.output_id, column=mapping[p.column])
            for p in plan.steps[0].projections
            if p.column in mapping
        ],
    )
    return QueryPlan(
        support="executable",
        reason="Adapted legacy candidate",
        steps=[step],
        operations=[],
        sections=[
            ResultSection(
                id=s.id,
                title=s.title,
                input="legacy",
                projections=[
                    Projection(output_id=p.output_id, column=mapping[p.column])
                    for p in s.projections
                ],
            )
            for s in plan.sections
        ],
    )
