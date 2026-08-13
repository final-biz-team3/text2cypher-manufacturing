"""프롬프트 생성에 필요한 그래프 스키마 입력 모델을 정의한다."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class NodeSchema(_SchemaModel):
    """노드가 가지는 속성을 표현한다."""

    properties: dict[str, PropertySchema]


class RelationshipSchema(_SchemaModel):
    """관계의 방향과 속성을 표현한다."""

    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    properties: dict[str, PropertySchema]


class GraphSchema(_SchemaModel):
    """프롬프트 생성에 필요한 전체 그래프 스키마를 표현한다."""

    nodes: dict[str, NodeSchema] = Field(min_length=1)
    relationships: dict[str, RelationshipSchema]

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
