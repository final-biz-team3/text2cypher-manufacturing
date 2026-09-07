"""Resolve spelling variants only when a schema reference has one owner.

No question examples, value matching, or fuzzy semantic substitutions are used.
"""

import json
from collections import defaultdict

from orchestrator.grounded.context import KnowledgeContext
from orchestrator.grounded.plan_models import Meaning, Predicate


def link_references(meaning: Meaning, knowledge: KnowledgeContext) -> Meaning:
    result = meaning.model_copy(deep=True)
    index: dict[str, set[str]] = defaultdict(set)
    for field in knowledge.fields:
        tool, path = field.split(":", 1)
        parts = path.split(".")
        for spelling in (
            field,
            path,
            ".".join(parts[-2:]),
            tool + ":" + ".".join(parts[-2:]),
        ):
            index[spelling.casefold()].add(field)

    def resolve(value: str) -> str:
        matches = index.get(value.casefold(), set())
        return next(iter(matches)) if len(matches) == 1 else value

    def predicate(value: Predicate) -> None:
        if value.field:
            value.field = resolve(value.field)
        for child in value.children:
            predicate(child)

    graph = json.loads(knowledge.sources.get("schema/graph_schema.yaml", "{}"))

    def graph_key(value: str, group: str) -> str:
        matches = [
            key for key in graph.get(group, {}) if key.casefold() == value.casefold()
        ]
        return matches[0] if len(matches) == 1 else value

    for requirement in result.requirements:
        requirement.fields = [resolve(f) for f in requirement.fields]
        requirement.group_by = [resolve(f) for f in requirement.group_by]
        for order in requirement.ordering:
            order.field = resolve(order.field)
        if requirement.predicate:
            predicate(requirement.predicate)
        if requirement.aggregate and requirement.aggregate.field:
            requirement.aggregate.field = resolve(requirement.aggregate.field)
        if requirement.relationship:
            relation = requirement.relationship
            relation.source_label = graph_key(relation.source_label, "nodes")
            relation.target_label = graph_key(relation.target_label, "nodes")
            relation.relationship = graph_key(relation.relationship, "relationships")
    for output in result.outputs:
        output.source_fields = [resolve(f) for f in output.source_fields]
    for mention in result.mentions:
        if mention.source_field:
            mention.source_field = resolve(mention.source_field)
    return result
