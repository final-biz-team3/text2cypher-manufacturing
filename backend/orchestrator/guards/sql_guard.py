"""LLM이 생성한 SQL을 실행 직전에 파싱해 쓰기 절·미허가 테이블을 차단한다.
스키마 화이트리스트는 schema/sql_schema.yaml(SqlSchema)을 그대로 재사용한다."""

import re
from collections.abc import Callable

import sqlparse
from sqlparse import tokens as sql_tokens

from agents.sql.schema.models import SqlSchema
from orchestrator.guards.result import GuardResult

_FORBIDDEN_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "CREATE",
    "MERGE",
    "CALL",
    "COPY",
    "VACUUM",
    "REINDEX",
    "REFRESH",
    "EXECUTE",
    # evaluation/safety.py의 _SQL_WRITE와 대조하다 빠져있는 걸 발견해 추가함
    # (2026-08-27) - 특히 DO는 익명 PL/pgSQL 블록이라 그 안에서 사실상
    # 임의 코드를 실행할 수 있어 가장 우려됐던 항목.
    "ANALYZE",
    "CLUSTER",
    "COMMENT",
    "DO",
    "LOCK",
}

_TABLE_REF_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+([\"a-zA-Z_][\"a-zA-Z0-9_]*\.[\"a-zA-Z_][\"a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


def _extract_referenced_tables(sql: str) -> set[str]:
    """FROM/JOIN 뒤에 오는 schema.table 식별자를 최선 노력으로 추출한다."""
    return {
        match.group(1).replace('"', "").lower()
        for match in _TABLE_REF_PATTERN.finditer(sql)
    }


def make_sql_guard(sql_schema: SqlSchema) -> Callable[[str], GuardResult]:
    """sql_schema로 초기화된 쿼리 가드 함수를 만든다."""
    allowed_tables = {name.lower() for name in sql_schema.tables}

    def guard(sql: str) -> GuardResult:
        statements = [
            statement for statement in sqlparse.parse(sql) if str(statement).strip()
        ]
        if len(statements) != 1:
            return GuardResult(
                False, "MULTIPLE_STATEMENTS", f"SQL 문이 {len(statements)}개 감지됨"
            )
        statement = statements[0]

        for token in statement.flatten():
            if token.ttype in (
                sql_tokens.Keyword,
                sql_tokens.Keyword.DDL,
                sql_tokens.Keyword.DML,
            ):
                normalized = token.normalized.upper()
                if normalized in _FORBIDDEN_KEYWORDS:
                    return GuardResult(
                        False,
                        "WRITE_KEYWORD_DETECTED",
                        f"쓰기 키워드 감지: {normalized}",
                    )

        statement_type = statement.get_type()
        stripped_upper = sql.strip().upper()
        is_read_only_shape = statement_type == "SELECT" or (
            statement_type == "UNKNOWN" and stripped_upper.startswith("WITH")
        )
        if not is_read_only_shape:
            return GuardResult(
                False,
                "NOT_READ_ONLY_STATEMENT",
                f"최상위 statement 타입={statement_type}",
            )

        referenced_tables = _extract_referenced_tables(sql)
        unknown_tables = referenced_tables - allowed_tables
        if unknown_tables:
            return GuardResult(
                False,
                "UNKNOWN_TABLE",
                f"스키마에 없는 테이블 참조: {', '.join(sorted(unknown_tables))}",
            )

        return GuardResult(True)

    return guard
