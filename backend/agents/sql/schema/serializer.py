"""SQL 스키마를 Text-to-SQL 프롬프트용 문자열로 직렬화한다.

물리 테이블·컬럼과 외래 키 조인을 먼저 출력하고, 한글 업무 용어가 있으면
물리 스키마 문법과 섞이지 않도록 별도의 alias 영역에 출력한다. 테이블, 컬럼,
조인은 ``SqlSchema``에 저장된 YAML 입력 순서를 그대로 유지한다.
"""

from agents.sql.schema.models import ColumnSchema, SqlSchema, TableSchema


def _format_column(name: str, column: ColumnSchema) -> str:
    """컬럼의 타입과 null 제약조건을 한 줄 조각으로 표현한다."""
    attributes: list[str] = [column.data_type]

    if not column.nullable:
        attributes.append("NOT NULL")

    return f"{name}: {' | '.join(attributes)}"


def _format_table(name: str, table: TableSchema) -> str:
    """컬럼과 단일·복합 PK를 포함한 테이블 스키마를 표현한다."""
    definitions = [
        _format_column(column_name, column)
        for column_name, column in table.columns.items()
    ]
    primary_key_columns = [
        column_name
        for column_name, column in table.columns.items()
        if column.primary_key
    ]

    if primary_key_columns:
        definitions.append(f"PRIMARY KEY ({', '.join(primary_key_columns)})")

    return f"{name} {{{', '.join(definitions)}}}"


def _format_aliases(schema: SqlSchema) -> list[str]:
    """물리 스키마와 구분되는 사용자 용어 alias 영역을 만든다."""
    lines: list[str] = []

    for table_name, table in schema.tables.items():
        if table.aliases:
            lines.append(f"Table {table_name}: {', '.join(table.aliases)}")

        for column_name, column in table.columns.items():
            if column.aliases:
                lines.append(
                    f"Column {table_name}.{column_name}: "
                    f"{', '.join(column.aliases)}"
                )

    return lines


def serialize_sql_schema(schema: SqlSchema) -> str:
    """검증된 SQL 스키마를 프롬프트용 텍스트로 직렬화한다."""
    lines = ["Table schemas:"]
    lines.extend(
        _format_table(table_name, table) for table_name, table in schema.tables.items()
    )

    lines.append("Joins:")
    lines.extend(f"{join.from_column} -> {join.to_column}" for join in schema.joins)

    alias_lines = _format_aliases(schema)
    if alias_lines:
        lines.append("Aliases:")
        lines.extend(alias_lines)

    return "\n".join(lines)
