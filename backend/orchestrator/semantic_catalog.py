"""Physical schemas and declarative manufacturing semantics compiled together."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from agents.cypher.schema.models import GraphSchema
from agents.sql.schema.models import SqlSchema

ToolName = Literal["sql", "graph"]
SemanticOperation = Literal[
    "roleProjection",
    "count",
    "countDistinct",
    "sum",
    "average",
    "difference",
    "clampedDifference",
    "pathLength",
    "minimumPathLength",
    "orderedPathProjection",
]
SemanticKind = Literal["physical", "role", "aggregate", "derived", "path"]
ValueType = Literal["identity", "name", "scalar", "path"]
PredicateOperator = Literal["equals"]
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _SemanticModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class SourceMappings(_SemanticModel):
    sql: list[NonEmptyString] = Field(default_factory=list)
    graph: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_paths(self) -> Self:
        for source in ("sql", "graph"):
            paths = getattr(self, source)
            if len(paths) != len(set(paths)):
                raise ValueError(f"{source} mappings must be unique")
        if not self.sql and not self.graph:
            raise ValueError("semantic mapping must name at least one source path")
        return self


class OutputRoleDefinition(_SemanticModel):
    role_id: NonEmptyString = Field(alias="roleId")
    alias: NonEmptyString
    canonical: NonEmptyString
    terms: list[NonEmptyString] = Field(default_factory=list)
    value_type: ValueType = Field(alias="valueType")
    mappings: SourceMappings

    @model_validator(mode="after")
    def validate_terms(self) -> Self:
        _validate_local_terms(self.canonical, self.terms, self.alias)
        return self


class IdentityProjectionDefinition(_SemanticModel):
    keys: list[NonEmptyString] = Field(min_length=1)
    labels: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_aliases(self) -> Self:
        _validate_unique(self.keys, "identity key alias")
        _validate_unique(self.labels, "identity label alias")
        overlap = set(self.keys) & set(self.labels)
        if overlap:
            raise ValueError(
                "identity keys and labels must be disjoint: "
                + ", ".join(sorted(overlap))
            )
        return self


class EntityRoleDefinition(_SemanticModel):
    role_id: NonEmptyString = Field(alias="roleId")
    canonical: NonEmptyString
    terms: list[NonEmptyString] = Field(default_factory=list)
    identity_projection: dict[ToolName, IdentityProjectionDefinition] = Field(
        alias="identityProjection"
    )

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        _validate_local_terms(self.canonical, self.terms, self.role_id)
        if not self.identity_projection:
            raise ValueError("entity role must define at least one source projection")
        return self


class TypedPredicate(_SemanticModel):
    field: NonEmptyString
    operator: PredicateOperator
    value: bool | int | float | NonEmptyString


class BusinessConceptDefinition(_SemanticModel):
    concept_id: NonEmptyString = Field(alias="conceptId")
    alias: NonEmptyString
    canonical: NonEmptyString
    terms: list[NonEmptyString] = Field(default_factory=list)
    kind: Literal["aggregate", "derived", "path"]
    sources: list[ToolName] = Field(min_length=1)
    grain: list[NonEmptyString] = Field(default_factory=list)
    operation: SemanticOperation
    inputs: list[NonEmptyString] = Field(default_factory=list)
    predicates: list[TypedPredicate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        _validate_local_terms(self.canonical, self.terms, self.alias)
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("concept sources must be unique")
        if len(self.grain) != len(set(self.grain)):
            raise ValueError("concept grain aliases must be unique")
        if len(self.inputs) != len(set(self.inputs)):
            raise ValueError("concept inputs must be unique")
        return self


class TransformDefinition(_SemanticModel):
    transform_id: Literal["bom_shortage_v1"] = Field(alias="transformId")
    output_scale: int = Field(alias="outputScale", ge=0, le=12)
    required_outputs: dict[ToolName, list[NonEmptyString]] = Field(
        alias="requiredOutputs"
    )

    @model_validator(mode="after")
    def validate_outputs(self) -> Self:
        if set(self.required_outputs) != {"sql", "graph"}:
            raise ValueError("transform requiredOutputs must define sql and graph")
        for source, aliases in self.required_outputs.items():
            if not aliases or len(aliases) != len(set(aliases)):
                raise ValueError(
                    f"transform {source} requiredOutputs must be non-empty and unique"
                )
        return self


class ManufacturingOntology(_SemanticModel):
    ontology_version: NonEmptyString = Field(alias="ontologyVersion")
    output_roles: list[OutputRoleDefinition] = Field(
        default_factory=list, alias="outputRoles"
    )
    entity_roles: list[EntityRoleDefinition] = Field(alias="entityRoles", min_length=1)
    business_concepts: list[BusinessConceptDefinition] = Field(
        default_factory=list, alias="businessConcepts"
    )
    transforms: list[TransformDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identifiers(self) -> Self:
        _validate_unique([role.role_id for role in self.output_roles], "output role ID")
        _validate_unique([role.role_id for role in self.entity_roles], "entity role ID")
        _validate_unique(
            [concept.concept_id for concept in self.business_concepts],
            "business concept ID",
        )
        _validate_unique(
            [transform.transform_id for transform in self.transforms], "transform ID"
        )
        semantic_aliases = [role.alias for role in self.output_roles] + [
            concept.alias for concept in self.business_concepts
        ]
        _validate_unique(semantic_aliases, "semantic alias")
        return self


def _normalized_term(value: str) -> str:
    return " ".join(value.casefold().split())


def _validate_local_terms(canonical: str, terms: list[str], alias: str) -> None:
    normalized = [_normalized_term(value) for value in (canonical, *terms)]
    if any(not value for value in normalized):
        raise ValueError(f"semantic terms for {alias!r} must not normalize to empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(
            f"canonical and alternative terms for {alias!r} must be unique"
        )


def _validate_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label}s must be unique")


@dataclass(frozen=True)
class AliasSpec:
    alias: str
    canonical: str
    canonical_source: ToolName
    sources: frozenset[ToolName]
    kind: SemanticKind
    value_type: ValueType
    schema_paths: tuple[str, ...]
    terms: tuple[str, ...]
    owner_terms: tuple[str, ...]
    nullable: bool
    grain: tuple[str, ...] = ()
    operation: SemanticOperation | None = None
    inputs: tuple[str, ...] = ()
    predicates: tuple[TypedPredicate, ...] = ()

    @property
    def meaning(self) -> str:
        """Compatibility name for callers while canonical is the source of truth."""
        return self.canonical

    @property
    def calculation_type(self) -> str:
        """Compatibility name for the previous output catalog contract."""
        return self.kind

    @property
    def search_terms(self) -> tuple[str, ...]:
        return self.terms


@dataclass(frozen=True)
class TransformSpec:
    transform_id: str
    output_scale: int
    required_outputs: Mapping[ToolName, tuple[str, ...]]
    generator_rules: Mapping[ToolName, tuple[str, ...]]


@dataclass(frozen=True)
class IdentityProjection:
    keys: tuple[str, ...]
    labels: tuple[str, ...]

    @property
    def display_aliases(self) -> tuple[str, ...]:
        return (*self.keys, *self.labels)


@dataclass(frozen=True)
class EntityRoleSpec:
    role_id: str
    canonical: str
    terms: tuple[str, ...]
    projections: Mapping[ToolName, IdentityProjection]


@dataclass(frozen=True)
class QuerySemanticCatalog:
    ontology_version: str
    by_tool: Mapping[ToolName, Mapping[str, AliasSpec]]
    identity_aliases_by_tool: Mapping[ToolName, frozenset[str]]
    shared_join_aliases: frozenset[str]
    entity_roles: Mapping[str, EntityRoleSpec]
    transforms: Mapping[str, TransformSpec]
    fingerprint: str

    def allowed_aliases(self, tool: str) -> tuple[str, ...]:
        source = _tool_name(tool)
        return tuple(sorted(self.by_tool[source]))

    def describe(self, tool: str) -> str:
        """Render physical ownership and semantic provenance for a model prompt."""
        source = _tool_name(tool)
        lines: list[str] = []
        for alias, spec in sorted(self.by_tool[source].items()):
            details = [
                f"kind={spec.kind}",
                f"valueType={spec.value_type}",
                f"meaning={spec.canonical}",
                f"terms={', '.join(spec.terms)}",
            ]
            if spec.schema_paths:
                details.append(f"paths={', '.join(spec.schema_paths)}")
            if spec.owner_terms:
                details.append(f"owners={', '.join(spec.owner_terms)}")
            details.append(f"sources={', '.join(sorted(spec.sources))}")
            details.append(f"nullable={'true' if spec.nullable else 'false'}")
            if spec.grain:
                details.append(f"grain={', '.join(spec.grain)}")
            if spec.operation is not None:
                details.append(f"operation={spec.operation}")
            if spec.inputs:
                details.append(f"inputs={', '.join(spec.inputs)}")
            if spec.predicates:
                predicates = ", ".join(
                    f"{item.field} {item.operator} {item.value!r}"
                    for item in spec.predicates
                )
                details.append(f"predicates={predicates}")
            lines.append(f"- {alias}: " + "; ".join(details))
        return "\n".join(lines)

    def allowed_entity_roles(self, tool: str) -> tuple[str, ...]:
        source = _tool_name(tool)
        return tuple(
            sorted(
                role_id
                for role_id, spec in self.entity_roles.items()
                if source in spec.projections
            )
        )

    def identity_projection(self, role_id: str, tool: str) -> IdentityProjection:
        source = _tool_name(tool)
        try:
            role = self.entity_roles[role_id]
        except KeyError as exc:
            raise ValueError(f"unknown entity role: {role_id!r}") from exc
        try:
            return role.projections[source]
        except KeyError as exc:
            raise ValueError(
                f"entity role {role_id!r} is unavailable from {source}"
            ) from exc

    def describe_entity_roles(self, tool: str) -> str:
        source = _tool_name(tool)
        lines: list[str] = []
        for role_id in self.allowed_entity_roles(source):
            role = self.entity_roles[role_id]
            projection = role.projections[source]
            labels = ", ".join(projection.labels) if projection.labels else "none"
            lines.append(
                f"- {role_id}: meaning={role.canonical}; "
                f"terms={', '.join(role.terms)}; keys={', '.join(projection.keys)}; "
                f"labels={labels}"
            )
        return "\n".join(lines)

    def transform(self, transform_id: str) -> TransformSpec:
        try:
            return self.transforms[transform_id]
        except KeyError as exc:
            raise ValueError(f"unsupported transform: {transform_id!r}") from exc


# Transitional import name. The implementation and source of truth are semantic.
OutputCatalog = QuerySemanticCatalog


def _tool_name(tool: str) -> ToolName:
    if tool == "sql":
        return "sql"
    if tool == "graph":
        return "graph"
    raise ValueError(f"unsupported output source: {tool!r}")


def load_manufacturing_ontology(path: str | Path) -> ManufacturingOntology:
    ontology_path = Path(path)
    with ontology_path.open(encoding="utf-8") as ontology_file:
        data = yaml.safe_load(ontology_file)
    return ManufacturingOntology.model_validate(data)


def build_query_semantic_catalog(
    sql_schema: SqlSchema,
    graph_schema: GraphSchema,
    ontology: ManufacturingOntology,
) -> QuerySemanticCatalog:
    by_tool: dict[ToolName, dict[str, AliasSpec]] = {"sql": {}, "graph": {}}
    identity_aliases: dict[ToolName, set[str]] = {"sql": set(), "graph": set()}
    sql_paths, graph_paths = _compile_physical_aliases(
        sql_schema, graph_schema, by_tool, identity_aliases
    )
    available_paths: dict[ToolName, set[str]] = {
        "sql": sql_paths,
        "graph": graph_paths,
    }

    for output_role in ontology.output_roles:
        source_names: tuple[ToolName, ...] = ("sql", "graph")
        role_sources: frozenset[ToolName] = frozenset(
            source for source in source_names if getattr(output_role.mappings, source)
        )
        for source in role_sources:
            mappings = tuple(getattr(output_role.mappings, source))
            unknown_paths = set(mappings) - available_paths[source]
            if unknown_paths:
                raise ValueError(
                    f"role {output_role.role_id!r} references unknown {source} paths: "
                    + ", ".join(sorted(unknown_paths))
                )
            _reject_alias_collision(by_tool[source], output_role.alias, "output role")
            by_tool[source][output_role.alias] = AliasSpec(
                alias=output_role.alias,
                canonical=output_role.canonical,
                canonical_source=source,
                sources=role_sources,
                kind="role",
                value_type=output_role.value_type,
                schema_paths=mappings,
                terms=(
                    output_role.alias,
                    output_role.canonical,
                    *sorted(output_role.terms, key=_normalized_term),
                ),
                owner_terms=tuple(
                    dict.fromkeys(path.rsplit(".", 1)[0] for path in mappings)
                ),
                nullable=any(
                    _path_nullable(source, path, sql_schema, graph_schema)
                    for path in mappings
                ),
                operation="roleProjection",
            )
            if output_role.value_type == "identity":
                identity_aliases[source].add(output_role.alias)

    for concept in ontology.business_concepts:
        for source in concept.sources:
            _reject_alias_collision(by_tool[source], concept.alias, "business concept")
            missing_inputs = set(concept.inputs) - set(by_tool[source])
            missing_grain = set(concept.grain) - set(by_tool[source])
            missing_predicates = {
                predicate.field
                for predicate in concept.predicates
                if predicate.field not in by_tool[source]
            }
            if missing_inputs or missing_grain or missing_predicates:
                missing = missing_inputs | missing_grain | missing_predicates
                raise ValueError(
                    f"concept {concept.concept_id!r} references aliases unavailable "
                    f"from {source}: {', '.join(sorted(missing))}"
                )
            by_tool[source][concept.alias] = AliasSpec(
                alias=concept.alias,
                canonical=concept.canonical,
                canonical_source=concept.sources[0],
                sources=frozenset(concept.sources),
                kind=concept.kind,
                value_type="path" if concept.kind == "path" else "scalar",
                schema_paths=(),
                terms=(
                    concept.alias,
                    concept.canonical,
                    *sorted(concept.terms, key=_normalized_term),
                ),
                owner_terms=(),
                nullable=False,
                grain=tuple(concept.grain),
                operation=concept.operation,
                inputs=tuple(concept.inputs),
                predicates=tuple(concept.predicates),
            )

    entity_roles: dict[str, EntityRoleSpec] = {}
    for entity_role in ontology.entity_roles:
        projections: dict[ToolName, IdentityProjection] = {}
        for source, definition in entity_role.identity_projection.items():
            available = by_tool[source]
            projection_aliases = {*definition.keys, *definition.labels}
            unknown = projection_aliases - set(available)
            if unknown:
                raise ValueError(
                    f"entity role {entity_role.role_id!r} references aliases unavailable "
                    f"from {source}: {', '.join(sorted(unknown))}"
                )
            invalid_keys = [
                alias
                for alias in definition.keys
                if available[alias].value_type != "identity"
            ]
            if invalid_keys:
                raise ValueError(
                    f"entity role {entity_role.role_id!r} has non-identity "
                    f"{source} keys: " + ", ".join(sorted(invalid_keys))
                )
            invalid_labels = [
                alias
                for alias in definition.labels
                if available[alias].value_type in {"identity", "path"}
                or available[alias].kind in {"aggregate", "derived", "path"}
            ]
            if invalid_labels:
                raise ValueError(
                    f"entity role {entity_role.role_id!r} has non-display "
                    f"{source} labels: " + ", ".join(sorted(invalid_labels))
                )
            projection_specs = [available[alias] for alias in definition.keys]
            shared_key_owners = set.intersection(
                *(_physical_owners(spec) for spec in projection_specs)
            )
            if not shared_key_owners:
                raise ValueError(
                    f"entity role {entity_role.role_id!r} has unrelated "
                    f"{source} identity key owners"
                )
            unrelated_labels = [
                alias
                for alias in definition.labels
                if not (_physical_owners(available[alias]) & shared_key_owners)
            ]
            if unrelated_labels:
                raise ValueError(
                    f"entity role {entity_role.role_id!r} has labels unrelated to "
                    f"its {source} identity owner: "
                    + ", ".join(sorted(unrelated_labels))
                )
            for alias in definition.labels:
                if available[alias].value_type == "scalar":
                    available[alias] = replace(available[alias], value_type="name")
            projections[source] = IdentityProjection(
                keys=tuple(definition.keys),
                labels=tuple(definition.labels),
            )
        entity_roles[entity_role.role_id] = EntityRoleSpec(
            role_id=entity_role.role_id,
            canonical=entity_role.canonical,
            terms=(
                entity_role.role_id,
                entity_role.canonical,
                *sorted(entity_role.terms, key=_normalized_term),
            ),
            projections=MappingProxyType(projections),
        )
    for source in ("sql", "graph"):
        if not any(source in role.projections for role in entity_roles.values()):
            raise ValueError(f"semantic catalog has no {source} entity roles")

    transforms: dict[str, TransformSpec] = {}
    for transform in ontology.transforms:
        compiled_outputs: dict[ToolName, tuple[str, ...]] = {}
        for source, aliases in transform.required_outputs.items():
            unknown = set(aliases) - set(by_tool[source])
            if unknown:
                raise ValueError(
                    f"transform {transform.transform_id!r} references unknown "
                    f"{source} outputs: {', '.join(sorted(unknown))}"
                )
            compiled_outputs[source] = tuple(aliases)
        transforms[transform.transform_id] = TransformSpec(
            transform_id=transform.transform_id,
            output_scale=transform.output_scale,
            required_outputs=MappingProxyType(compiled_outputs),
            generator_rules=MappingProxyType(
                _transform_generator_rules(transform.transform_id)
            ),
        )

    if any(not aliases for aliases in identity_aliases.values()):
        raise ValueError("SQL and Graph schemas must expose source identity aliases")
    shared_join_aliases = identity_aliases["sql"] & identity_aliases["graph"]
    if not shared_join_aliases:
        raise ValueError("SQL and Graph must share at least one identity alias")

    fingerprint_payload = {
        "ontologyVersion": ontology.ontology_version,
        "aliases": {
            source: {
                alias: {
                    "canonical": spec.canonical,
                    "kind": spec.kind,
                    "valueType": spec.value_type,
                    "paths": spec.schema_paths,
                    "owners": spec.owner_terms,
                    "nullable": spec.nullable,
                    "terms": sorted(spec.terms, key=_normalized_term),
                    "sources": sorted(spec.sources),
                    "grain": spec.grain,
                    "operation": spec.operation,
                    "inputs": spec.inputs,
                    "predicates": sorted(
                        (
                            item.field,
                            item.operator,
                            json.dumps(item.value, ensure_ascii=False, sort_keys=True),
                        )
                        for item in spec.predicates
                    ),
                }
                for alias, spec in sorted(specs.items())
            }
            for source, specs in by_tool.items()
        },
        "transforms": {
            transform_id: {
                "outputScale": spec.output_scale,
                "requiredOutputs": {
                    source: sorted(aliases)
                    for source, aliases in spec.required_outputs.items()
                },
            }
            for transform_id, spec in sorted(transforms.items())
        },
        "entityRoles": {
            role_id: {
                "canonical": role.canonical,
                "terms": sorted(role.terms, key=_normalized_term),
                "identityProjection": {
                    source: {
                        "keys": projection.keys,
                        "labels": projection.labels,
                    }
                    for source, projection in sorted(role.projections.items())
                },
            }
            for role_id, role in sorted(entity_roles.items())
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return QuerySemanticCatalog(
        ontology_version=ontology.ontology_version,
        by_tool=MappingProxyType(
            {
                source: MappingProxyType(dict(aliases))
                for source, aliases in by_tool.items()
            }
        ),
        identity_aliases_by_tool=MappingProxyType(
            {source: frozenset(aliases) for source, aliases in identity_aliases.items()}
        ),
        shared_join_aliases=frozenset(shared_join_aliases),
        entity_roles=MappingProxyType(dict(entity_roles)),
        transforms=MappingProxyType(dict(transforms)),
        fingerprint=fingerprint,
    )


def _compile_physical_aliases(
    sql_schema: SqlSchema,
    graph_schema: GraphSchema,
    by_tool: dict[ToolName, dict[str, AliasSpec]],
    identity_aliases: dict[ToolName, set[str]],
) -> tuple[set[str], set[str]]:
    sql_paths: set[str] = set()
    graph_paths: set[str] = set()
    for table_name, table in sql_schema.tables.items():
        owner_terms = (table_name, table_name.rsplit(".", 1)[-1], *table.aliases)
        for column_name, column in table.columns.items():
            path = f"{table_name}.{column_name}"
            sql_paths.add(path)
            for alias in table.output_aliases.get(column_name, []):
                _merge_physical_alias(
                    by_tool["sql"],
                    alias=alias,
                    source="sql",
                    path=path,
                    terms=(alias, column_name, *column.aliases),
                    owner_terms=owner_terms,
                    nullable=column.nullable,
                    is_identity=column.primary_key,
                )
                if column.primary_key:
                    identity_aliases["sql"].add(alias)

    for node_name, node in graph_schema.nodes.items():
        owner_terms = (node_name, *node.aliases)
        for property_name, property_schema in node.properties.items():
            path = f"{node_name}.{property_name}"
            graph_paths.add(path)
            for alias in node.output_aliases.get(property_name, []):
                _merge_physical_alias(
                    by_tool["graph"],
                    alias=alias,
                    source="graph",
                    path=path,
                    terms=(alias, property_name, *property_schema.aliases),
                    owner_terms=owner_terms,
                    nullable=not property_schema.required,
                    is_identity=property_name == node.unique_key,
                )
                if property_name == node.unique_key:
                    identity_aliases["graph"].add(alias)

    for relationship_name, relationship in graph_schema.relationships.items():
        owner_terms = (relationship_name, *relationship.aliases)
        for property_name, property_schema in relationship.properties.items():
            path = f"{relationship_name}.{property_name}"
            graph_paths.add(path)
            for alias in relationship.output_aliases.get(property_name, []):
                _merge_physical_alias(
                    by_tool["graph"],
                    alias=alias,
                    source="graph",
                    path=path,
                    terms=(alias, property_name, *property_schema.aliases),
                    owner_terms=owner_terms,
                    nullable=not property_schema.required,
                    is_identity=False,
                )
    return sql_paths, graph_paths


def _transform_generator_rules(
    transform_id: str,
) -> dict[ToolName, tuple[str, ...]]:
    """Render the allowlisted transform's execution semantics, never query syntax."""
    if transform_id != "bom_shortage_v1":
        raise ValueError(f"unsupported transform: {transform_id!r}")
    return {
        "graph": (
            "Preserve every valid BOM component path in anchor-to-destination order; "
            "do not pre-filter components by purchase classification.",
            "Supplier fan-out is optional: preserve a component path when no active "
            "supplier exists and return null supplier identity fields for that row.",
            "pathProductIds and quantityPerAssembly arrays must be aligned to the same "
            "BOM path direction and row.",
        ),
        "sql": (
            "The binding componentIds defines the complete lookup domain. Return one "
            "row per distinct component with makeFlag and actualStock; do not drop "
            "internally manufactured components.",
            "Required quantity and shortage are calculated by the composer, not SQL.",
        ),
    }


def _merge_physical_alias(
    target: dict[str, AliasSpec],
    *,
    alias: str,
    source: ToolName,
    path: str,
    terms: tuple[str, ...],
    owner_terms: tuple[str, ...],
    nullable: bool,
    is_identity: bool,
) -> None:
    existing = target.get(alias)
    if existing is None:
        target[alias] = AliasSpec(
            alias=alias,
            canonical=f"{path} physical field",
            canonical_source=source,
            sources=frozenset({source}),
            kind="physical",
            value_type="identity" if is_identity else "scalar",
            schema_paths=(path,),
            terms=tuple(dict.fromkeys(terms)),
            owner_terms=tuple(dict.fromkeys(owner_terms)),
            nullable=nullable,
        )
        return
    if existing.kind != "physical":
        raise ValueError(f"physical alias {alias!r} collides with semantic output")
    target[alias] = AliasSpec(
        alias=alias,
        canonical=existing.canonical,
        canonical_source=source,
        sources=existing.sources,
        kind="physical",
        value_type=(
            "identity"
            if is_identity or existing.value_type == "identity"
            else existing.value_type
        ),
        schema_paths=(*existing.schema_paths, path),
        terms=tuple(dict.fromkeys((*existing.terms, *terms))),
        owner_terms=tuple(dict.fromkeys((*existing.owner_terms, *owner_terms))),
        nullable=existing.nullable or nullable,
    )


def _reject_alias_collision(
    target: dict[str, AliasSpec], alias: str, semantic_kind: str
) -> None:
    if alias in target:
        raise ValueError(
            f"{semantic_kind} alias {alias!r} collides with a physical or semantic alias"
        )


def _physical_owners(spec: AliasSpec) -> set[str]:
    """Return canonical physical owners behind a physical or role alias."""
    return {path.rsplit(".", 1)[0] for path in spec.schema_paths}


def _path_nullable(
    source: ToolName,
    path: str,
    sql_schema: SqlSchema,
    graph_schema: GraphSchema,
) -> bool:
    if source == "sql":
        table_name, column_name = path.rsplit(".", 1)
        return sql_schema.tables[table_name].columns[column_name].nullable
    owner, property_name = path.split(".", 1)
    if owner in graph_schema.nodes:
        return not graph_schema.nodes[owner].properties[property_name].required
    return not graph_schema.relationships[owner].properties[property_name].required


def load_query_semantic_catalog(
    *,
    sql_schema: SqlSchema,
    graph_schema: GraphSchema,
    ontology_path: str | Path,
) -> QuerySemanticCatalog:
    ontology = load_manufacturing_ontology(ontology_path)
    return build_query_semantic_catalog(sql_schema, graph_schema, ontology)
