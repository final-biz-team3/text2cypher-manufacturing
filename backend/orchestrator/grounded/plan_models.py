"""Executable request contracts independent of question catalogues."""

from typing import Any, Literal

from pydantic import Field

from orchestrator.grounded.models import Clarification, Contract, Evidence

Scalar = str | int | float | bool | None


class Predicate(Contract):
    op: Literal[
        "and",
        "or",
        "not",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "in",
        "is_null",
        "contains",
    ]
    field: str | None
    value: Scalar
    values: list[Scalar]
    children: list["Predicate"]


class Aggregate(Contract):
    output: str
    function: Literal["count", "sum", "avg", "min", "max"]
    field: str | None
    distinct: bool


class Ordering(Contract):
    field: str
    descending: bool
    nulls_first: bool


class Relationship(Contract):
    source_label: str
    relationship: str
    target_label: str
    direction: Literal["out", "in", "either"]
    min_hops: int = Field(ge=0)
    max_hops: int = Field(ge=1, le=20)


class IntentRequirement(Contract):
    id: str
    kind: Literal[
        "target",
        "output",
        "filter",
        "aggregation",
        "grain",
        "relationship",
        "ordering",
        "limit",
        "join",
        "period",
    ]
    description: str
    evidence: Evidence
    fields: list[str]
    predicate: Predicate | None
    aggregate: Aggregate | None
    group_by: list[str]
    relationship: Relationship | None
    ordering: list[Ordering]
    limit: int | None = Field(ge=0)


class Mention(Contract):
    text: str
    kind: Literal["target", "name", "id", "attribute"]
    source_field: str | None


class Output(Contract):
    id: str
    alias: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    label: str
    source_fields: list[str]
    calculation: str
    value_type: Literal["string", "number", "boolean", "date", "list"]
    nullable: bool
    unit: str | None
    evidence: Evidence


class Meaning(Contract):
    action: Literal["read", "write", "mixed"]
    data_support: Literal["available", "missing", "unknown"]
    missing_facts: list[str]
    requirements: list[IntentRequirement]
    mentions: list[Mention]
    outputs: list[Output]
    assumptions: list[Evidence]
    clarification: Clarification | None


class Projection(Contract):
    output_id: str
    column: str


class Parameter(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    value: Scalar


class Binding(Contract):
    parameter: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    source_step: str
    fields: list[str] = Field(min_length=1)
    # One JSON array of complete rows, never independent arrays.


class SourceStep(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    tool: Literal["sql", "graph"]
    query: str
    parameters: list[Parameter]
    bindings: list[Binding]
    depends_on: list[str]
    requirement_ids: list[str]
    projections: list[Projection]
    relationships: list[Relationship]


class ResultOperation(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    kind: Literal["filter", "project", "distinct", "aggregate", "sort", "limit", "join"]
    inputs: list[str] = Field(min_length=1, max_length=2)
    predicate: Predicate | None
    columns: list[str]
    aggregates: list[Aggregate]
    ordering: list[Ordering]
    limit: int | None = Field(ge=0)
    join_type: Literal["inner", "left", "semi", "anti"] | None
    left_keys: list[str]
    right_keys: list[str]
    requirement_ids: list[str]


class ResultSection(Contract):
    id: str
    title: str
    input: str
    projections: list[Projection]


class QueryPlan(Contract):
    support: Literal["executable", "missing_data", "unsupported"]
    reason: str
    steps: list[SourceStep] = Field(max_length=4)
    operations: list[ResultOperation] = Field(max_length=8)
    sections: list[ResultSection]


class ValidationIssue(Contract):
    kind: Literal["missing", "extra", "contradiction", "unknown", "format"]
    requirement_id: str | None
    step_id: str | None
    question_excerpt: str
    query_excerpt: str
    explanation: str


class ReviewedCheck(Contract):
    requirement_id: str
    step_id: str
    verdict: Literal["supported", "contradicted", "unknown"]
    query_excerpt: str
    explanation: str


class PlanReview(Contract):
    checks: list[ReviewedCheck]
    issues: list[ValidationIssue]


class StepResult(Contract):
    step_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    query: str
    elapsed_ms: float


class PublicColumn(Contract):
    id: str
    label: str
    value_type: str
    unit: str | None


class PublicSection(Contract):
    id: str
    title: str
    columns: list[PublicColumn]
    rows: list[dict[str, Any]]
    truncated: bool


class FinalResult(Contract):
    sections: list[PublicSection]
    truncated: bool


QueryStatus = Literal[
    "answered",
    "empty",
    "clarification",
    "unsupported",
    "unanswerable",
    "unverified",
    "blocked",
    "error",
]
