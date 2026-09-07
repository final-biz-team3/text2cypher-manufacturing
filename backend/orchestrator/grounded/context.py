"""Versioned physical and business knowledge, with auditable source fragments."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from orchestrator.grounded.models import Evidence, QueryIntent

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class KnowledgeContext:
    sources: dict[str, str]
    fields: frozenset[str]
    digest: str

    def render(self) -> str:
        return json.dumps(self.sources, ensure_ascii=False)

    def prompt_payload(self) -> dict[str, Any]:
        # The full documents already contain each field's definition. Keep every
        # field and relationship, but avoid sending those definitions twice or
        # embedding JSON documents as escaped JSON strings.
        documents = {}
        for key, value in self.sources.items():
            if key in self.fields:
                continue
            try:
                documents[key] = json.loads(value)
            except json.JSONDecodeError:
                documents[key] = value
        return {"sources": documents, "physical_fields": sorted(self.fields)}

    def validate_evidence(self, evidence: Evidence, query: str) -> None:
        source = (
            query
            if evidence.source == "question"
            else self.sources.get(evidence.source)
        )
        if not source or not evidence.text or evidence.text not in source:
            raise ValueError("Evidence must quote an available source exactly")

    def validate_intent(self, intent: QueryIntent, query: str) -> None:
        ids = [r.id for r in intent.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate requirement ids")
        for requirement in intent.requirements:
            self.validate_evidence(requirement.evidence, query)
        for entity in intent.entities:
            if entity.text not in query or entity.source_field not in self.fields:
                raise ValueError(
                    "Entity must reference original text and a physical field"
                )
        for assumption in intent.assumptions:
            self.validate_evidence(assumption, query)
            if assumption.source == "question":
                raise ValueError("Defaults require documented business evidence")
        aliases = [o.alias for o in intent.outputs]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Output aliases must be unique")
        for output in intent.outputs:
            self.validate_evidence(output.evidence, query)
            if output.unit is not None and (
                output.evidence.source == "question"
                or output.unit not in self.sources.get(output.evidence.source, "")
            ):
                raise ValueError("Units require cited business documentation")
            if not output.source_fields or not set(output.source_fields) <= self.fields:
                raise ValueError("Output requires existing physical fields")
            if any(
                not field.startswith(output.tool + ":")
                for field in output.source_fields
            ):
                raise ValueError("Output source ownership mismatch")
        if intent.action == "read" and (not intent.requirements or not intent.outputs):
            raise ValueError("Read interpretation requires requirements and outputs")
        if intent.action == "clarify" and not intent.clarification:
            raise ValueError("Clarification must ask a concrete question")


def load_knowledge(root: Path = ROOT) -> KnowledgeContext:
    sources: dict[str, str] = {}
    fields: set[str] = set()
    sql_path = root / "schema/sql_schema.yaml"
    graph_path = root / "schema/graph_schema.yaml"
    sql = yaml.safe_load(sql_path.read_text(encoding="utf-8"))
    graph = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    for relative, document in (
        ("schema/sql_schema.yaml", sql),
        ("schema/graph_schema.yaml", graph),
    ):
        sources[relative] = json.dumps(document, ensure_ascii=False, default=str)
    for table, spec in sql["tables"].items():
        for column, definition in spec["columns"].items():
            key = f"sql:{table}.{column}"
            fields.add(key)
            sources[key] = json.dumps(definition, ensure_ascii=False)
    for group in ("nodes", "relationships"):
        for label, spec in graph.get(group, {}).items():
            for prop, definition in spec.get("properties", {}).items():
                key = f"graph:{label}.{prop}"
                fields.add(key)
                sources[key] = json.dumps(definition, ensure_ascii=False, default=str)
    # Business meanings are available; question matching/result-shape recipes are not.
    ontology = yaml.safe_load(
        (root / "ontology/manufacturing_terms.yaml").read_text(encoding="utf-8")
    )
    for key in (
        "ontologyVersion",
        "outputRoles",
        "entityRoles",
        "businessConcepts",
        "transforms",
    ):
        value: Any = ontology.get(key)
        if value is not None:
            sources[f"ontology/manufacturing_terms.yaml#{key}"] = json.dumps(
                value, ensure_ascii=False, default=str
            )
    canonical = json.dumps(sources, sort_keys=True, ensure_ascii=False)
    return KnowledgeContext(
        sources, frozenset(fields), hashlib.sha256(canonical.encode()).hexdigest()
    )
