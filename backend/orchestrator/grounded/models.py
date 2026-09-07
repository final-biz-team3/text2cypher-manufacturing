"""Request-scoped contracts; no benchmark identifiers or expected answers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Contract):
    text: str
    source: str
    # source is 'question' or an exact key in KnowledgeContext.sources.


class Requirement(Contract):
    id: str
    kind: Literal[
        "target",
        "output",
        "filter",
        "aggregation",
        "ordering",
        "limit",
        "period",
        "relationship",
        "grain",
        "join",
    ]
    description: str
    evidence: Evidence


class EntityMention(Contract):
    text: str
    kind: Literal["name", "id", "attribute"]
    source_field: str


class OutputDefinition(Contract):
    alias: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    label: str
    tool: Literal["sql", "graph"]
    source_fields: list[str]
    expression: str
    value_type: Literal["string", "number", "boolean", "date", "list"]
    nullable: bool
    unit: str | None
    evidence: Evidence


class Clarification(Contract):
    question: str = Field(min_length=1)
    options: list[str] = Field(max_length=3)


class QueryIntent(Contract):
    action: Literal["read", "write", "mixed", "clarify", "unanswerable"]
    requirements: list[Requirement]
    entities: list[EntityMention]
    outputs: list[OutputDefinition]
    assumptions: list[Evidence]
    clarification: Clarification | None
    reason: str


class RequirementCheck(Contract):
    requirement_id: str
    verdict: Literal["supported", "contradicted", "unknown"]
    tool: Literal["sql", "graph"] | None
    query_excerpt: str
    explanation: str


class CandidateReview(Contract):
    interpretation_complete: bool
    checks: list[RequirementCheck]
    clarification: Clarification | None


class AnswerItem(Contract):
    section: str
    row_index: int = Field(ge=0)
    fields: list[str]


class AnswerSelection(Contract):
    items: list[AnswerItem] = Field(max_length=10)
