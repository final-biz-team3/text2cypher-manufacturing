"""프롬프트 생성에 필요한 SQL 스키마 입력 모델을 정의한다."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ColumnDataType = Literal["INTEGER", "SMALLINT", "NUMERIC", "VARCHAR"]


class _SchemaModel(BaseModel):
    """프롬프트 계약에 포함되지 않는 스키마 메타데이터는 무시한다."""

    model_config = ConfigDict(
        extra="ignore",
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class ColumnSchema(_SchemaModel):
    """SQL 컬럼의 타입, 키 제약조건과 사용자 용어를 표현한다."""

    data_type: ColumnDataType = Field(alias="type")
    primary_key: bool = Field(default=False, alias="primaryKey")
    nullable: bool = True
    aliases: list[str] = Field(default_factory=list)


class TableSchema(_SchemaModel):
    """SQL 테이블의 컬럼과 사용자 용어를 표현한다."""

    aliases: list[str] = Field(default_factory=list)
    columns: dict[str, ColumnSchema]


class JoinSchema(_SchemaModel):
    """두 SQL 컬럼 사이의 외래 키 조인 방향을 표현한다."""

    from_column: str = Field(alias="from")
    to_column: str = Field(alias="to")


class SqlSchema(_SchemaModel):
    """프롬프트 생성에 필요한 전체 SQL 스키마를 표현한다."""

    tables: dict[str, TableSchema] = Field(min_length=1)
    joins: list[JoinSchema]

    @model_validator(mode="after")
    def validate_join_references(self) -> Self:
        """조인의 양 끝점이 존재하는 ``schema.table.column``인지 검사한다."""
        for join_index, join in enumerate(self.joins):
            self._validate_join_endpoint(join_index, "from", join.from_column)
            self._validate_join_endpoint(join_index, "to", join.to_column)

        return self

    def _validate_join_endpoint(
        self,
        join_index: int,
        endpoint_name: str,
        endpoint: str,
    ) -> None:
        """단일 조인 끝점의 형식과 테이블·컬럼 참조를 검사한다."""
        parts = endpoint.split(".")
        if len(parts) != 3 or any(not part for part in parts):
            raise ValueError(
                f"Join at index {join_index} has invalid {endpoint_name} endpoint "
                f"'{endpoint}'; expected 'schema.table.column'."
            )

        table_name = ".".join(parts[:2])
        column_name = parts[2]
        table = self.tables.get(table_name)

        if table is None:
            raise ValueError(
                f"Join at index {join_index} references unknown table "
                f"'{table_name}' in {endpoint_name} endpoint."
            )

        if column_name not in table.columns:
            raise ValueError(
                f"Join at index {join_index} references unknown column "
                f"'{endpoint}' in {endpoint_name} endpoint."
            )
