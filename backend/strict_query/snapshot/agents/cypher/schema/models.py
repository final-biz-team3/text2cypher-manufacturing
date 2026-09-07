"""Text-to-Cypher 프롬프트에 필요한 그래프 스키마 입력 모델을 정의한다."""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PropertyDataType = Literal[
    "BOOLEAN",
    "DATE",
    "FLOAT",
    "INTEGER",
    "LOCAL_DATETIME",
    "STRING",
]


class _SchemaModel(BaseModel):
    """프롬프트 계약에 포함되지 않는 스키마 메타데이터는 무시한다."""

    model_config = ConfigDict(
        extra="ignore",
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class PropertySchema(_SchemaModel):
    """노드 또는 관계 속성의 데이터 타입을 표현한다."""

    data_type: PropertyDataType = Field(alias="type")
    required: bool = Field(default=True, exclude=True)
    aliases: list[str] = Field(default_factory=list)
    source_column: str | None = Field(default=None, alias="sourceColumn", exclude=True)


class NodeSource(_SchemaModel):
    """노드가 매핑되는 PostgreSQL 스키마·테이블을 표현한다."""

    schema_name: str = Field(alias="schema")
    table: str


class NodeSchema(_SchemaModel):
    """노드가 가지는 속성을 표현한다."""

    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, PropertySchema]
    unique_key: str | None = Field(default=None, alias="uniqueKey", exclude=True)
    source: NodeSource | None = Field(default=None, exclude=True)
    output_aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        alias="outputAliases",
        exclude=True,
    )

    @model_validator(mode="after")
    def validate_output_aliases(self) -> Self:
        unknown_properties = set(self.output_aliases) - set(self.properties)
        if unknown_properties:
            names = ", ".join(sorted(unknown_properties))
            raise ValueError(f"Output aliases reference unknown properties: {names}")
        aliases = [alias for values in self.output_aliases.values() for alias in values]
        if any(not alias.strip() for alias in aliases) or len(aliases) != len(
            set(aliases)
        ):
            raise ValueError("Output aliases must be non-empty and unique per node.")
        return self


class RelationshipSchema(_SchemaModel):
    """관계의 방향과 속성을 표현한다."""

    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, PropertySchema]
    output_aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        alias="outputAliases",
        exclude=True,
    )

    @model_validator(mode="after")
    def validate_output_aliases(self) -> Self:
        unknown_properties = set(self.output_aliases) - set(self.properties)
        if unknown_properties:
            names = ", ".join(sorted(unknown_properties))
            raise ValueError(f"Output aliases reference unknown properties: {names}")
        aliases = [alias for values in self.output_aliases.values() for alias in values]
        if any(not alias.strip() for alias in aliases) or len(aliases) != len(
            set(aliases)
        ):
            raise ValueError(
                "Output aliases must be non-empty and unique per relationship."
            )
        return self


class GraphQueryPolicy(_SchemaModel):
    """BOM 탐색에 적용할 기준일과 최대 깊이를 표현한다."""

    bom_as_of_date: str = Field(alias="bomAsOfDate")
    bom_max_depth: int = Field(alias="bomMaxDepth", gt=0)

    @field_validator("bom_as_of_date")
    @classmethod
    def validate_bom_as_of_date(cls, value: str) -> str:
        """BOM 기준일이 ISO 날짜 형식인지 검사한다."""
        date.fromisoformat(value)
        return value


class GraphSchema(_SchemaModel):
    """프롬프트 생성에 필요한 전체 그래프 스키마를 표현한다."""

    nodes: dict[str, NodeSchema] = Field(min_length=1)
    relationships: dict[str, RelationshipSchema]
    query_policy: GraphQueryPolicy | None = Field(
        default=None,
        alias="meta",
        exclude=True,
    )

    @model_validator(mode="after")
    def validate_relationship_references(self) -> Self:
        """관계가 존재하는 노드만 참조하는지 검사한다."""
        for relationship_name, relationship in self.relationships.items():
            if relationship.from_node not in self.nodes:
                raise ValueError(
                    f"Relationship '{relationship_name}' references "
                    f"unknown source node '{relationship.from_node}'."
                )

            if relationship.to_node not in self.nodes:
                raise ValueError(
                    f"Relationship '{relationship_name}' references "
                    f"unknown target node '{relationship.to_node}'."
                )

        return self
