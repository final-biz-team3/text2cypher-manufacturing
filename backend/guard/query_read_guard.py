"""생성된 PostgreSQL SQL과 Neo4j Cypher의 읽기 전용 여부를 검사한다."""

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pglast.parser import ParseError, parse_sql_json

from agents.sql.schema.loader import load_sql_schema
from orchestrator.state import GuardViolation

_SQL_ALLOWED_FUNCTIONS = {
    "ABS",
    "AVG",
    "CEIL",
    "CEILING",
    "COALESCE",
    "CONCAT",
    "COUNT",
    "DATE_PART",
    "DATE_TRUNC",
    "DENSE_RANK",
    "FLOOR",
    "GREATEST",
    "LENGTH",
    "LEAST",
    "LOWER",
    "MAX",
    "MIN",
    "NULLIF",
    "RANK",
    "ROUND",
    "ROW_NUMBER",
    "SUBSTRING",
    "SUM",
    "TRIM",
    "UPPER",
}
_CYPHER_WRITE_TOKENS = {
    "CALL",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "FOREACH",
    "INSERT",
    "LOAD",
    "MERGE",
    "REMOVE",
    "SET",
}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _allowed_sql_relations() -> frozenset[str]:
    """기계 판독 SQL 스키마에 공개된 업무 테이블만 반환한다."""
    schema_dir = Path(os.environ.get("SCHEMA_DIR", _PROJECT_ROOT / "schema"))
    schema = load_sql_schema(schema_dir / "sql_schema.yaml")
    return frozenset(name.lower() for name in schema.tables)


def _mask_cypher_literals_and_comments(query: str) -> str:
    """Cypher 문법에 맞춰 문자열·백틱 식별자·주석을 공백으로 가린다.

    문자열은 백슬래시 이스케이프를 허용하지만 백틱 식별자는 연속 백틱(``)
    으로만 백틱을 표현한다. PostgreSQL의 dollar-quoted string과 ``--`` 주석은
    Cypher 문법이 아니므로 특별 취급하지 않는다.
    """
    clean: list[str] = []
    index = 0

    while index < len(query):
        character = query[index]

        if character in {"'", '"'}:
            quote = character
            clean.append(" ")
            index += 1
            while index < len(query):
                clean.append(" ")
                if query[index] == "\\" and index + 1 < len(query):
                    clean.append(" ")
                    index += 2
                    continue
                if query[index] == quote:
                    if index + 1 < len(query) and query[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        if character == "`":
            clean.append(" ")
            index += 1
            while index < len(query):
                clean.append(" ")
                if query[index] == "`":
                    if index + 1 < len(query) and query[index + 1] == "`":
                        clean.append(" ")
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        if query.startswith("//", index):
            newline = query.find("\n", index + 2)
            if newline < 0:
                break
            clean.append("\n")
            index = newline + 1
            continue

        if query.startswith("/*", index):
            depth = 1
            index += 2
            while index < len(query) and depth:
                if query.startswith("/*", index):
                    depth += 1
                    index += 2
                elif query.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            clean.append(" ")
            continue

        clean.append(character)
        index += 1

    return "".join(clean)


def _has_multiple_statements(masked_query: str) -> bool:
    body = masked_query.strip()
    if body.endswith(";"):
        body = body[:-1]
    return ";" in body


def _tokens(masked_query: str) -> list[str]:
    return re.findall(r"[A-Za-z_]+", masked_query.upper())


def _walk_ast(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_ast(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_ast(child)


def _function_name(func_call: dict[str, Any]) -> str:
    parts = []
    for part in func_call.get("funcname", []):
        value = part.get("String", {}).get("sval")
        if value:
            parts.append(str(value))
    return ".".join(parts).upper()


def _referenced_relations(statement: dict[str, Any]) -> set[str]:
    """CTE 이름을 제외한 실제 FROM 대상 테이블을 추출한다."""
    cte_names = {
        str(node["CommonTableExpr"].get("ctename", "")).lower()
        for node in _walk_ast(statement)
        if "CommonTableExpr" in node
    }
    relations: set[str] = set()
    for node in _walk_ast(statement):
        range_var = node.get("RangeVar")
        if not isinstance(range_var, dict):
            continue
        relation = str(range_var.get("relname", "")).lower()
        schema = str(range_var.get("schemaname", "")).lower()
        if not schema and relation in cte_names:
            continue
        relations.add(f"{schema}.{relation}" if schema else relation)
    return relations


def _is_allowed_function(
    function_name: str, *, allow_unqualified_functions: bool
) -> bool:
    schema, separator, name = function_name.rpartition(".")
    if not separator:
        return allow_unqualified_functions and function_name in _SQL_ALLOWED_FUNCTIONS
    return bool(separator and schema == "PG_CATALOG" and name in _SQL_ALLOWED_FUNCTIONS)


def validate_sql_read_only(
    query: str | None,
    *,
    allow_unqualified_functions: bool = False,
    enforce_relation_allowlist: bool = True,
) -> list[GuardViolation]:
    if not query or not query.strip():
        return [
            {
                "database": "postgresql",
                "code": "EMPTY_QUERY",
                "message": "SQL이 비어 있습니다.",
            }
        ]
    try:
        parsed = json.loads(parse_sql_json(query))
    except (ParseError, json.JSONDecodeError):
        return [
            {
                "database": "postgresql",
                "code": "INVALID_QUERY",
                "message": "유효한 PostgreSQL 조회문이 아닙니다.",
            }
        ]
    statements = parsed.get("stmts", [])
    if len(statements) != 1:
        return [
            {
                "database": "postgresql",
                "code": "MULTIPLE_STATEMENTS",
                "message": "SQL은 한 문장만 허용됩니다.",
            }
        ]
    statement = statements[0].get("stmt", {})
    if set(statement) != {"SelectStmt"}:
        return [
            {
                "database": "postgresql",
                "code": "NOT_READ_QUERY",
                "message": "SELECT 조회문만 허용됩니다.",
            }
        ]

    ast_nodes = list(_walk_ast(statement))
    write_nodes = sorted(
        key
        for node in ast_nodes
        for key in node
        if key.endswith("Stmt") and key != "SelectStmt"
    )
    if write_nodes:
        return [
            {
                "database": "postgresql",
                "code": "WRITE_CLAUSE",
                "message": f"쓰기 구문은 허용되지 않습니다: {', '.join(write_nodes)}",
            }
        ]
    select_nodes = [node["SelectStmt"] for node in ast_nodes if "SelectStmt" in node]
    if any(node.get("intoClause") for node in select_nodes):
        return [
            {
                "database": "postgresql",
                "code": "WRITE_CLAUSE",
                "message": "SELECT INTO는 허용되지 않습니다.",
            }
        ]
    if any(node.get("lockingClause") for node in select_nodes):
        return [
            {
                "database": "postgresql",
                "code": "LOCKING_READ",
                "message": "행 잠금 조회는 허용되지 않습니다.",
            }
        ]
    unauthorized_relations = (
        sorted(_referenced_relations(statement) - _allowed_sql_relations())
        if enforce_relation_allowlist
        else []
    )
    if unauthorized_relations:
        return [
            {
                "database": "postgresql",
                "code": "UNAUTHORIZED_RELATION",
                "message": (
                    "공개된 업무 스키마 외의 테이블은 조회할 수 없습니다: "
                    f"{', '.join(unauthorized_relations)}"
                ),
            }
        ]
    called_functions = {
        _function_name(node["FuncCall"]) for node in ast_nodes if "FuncCall" in node
    }
    unsafe_functions = sorted(
        function_name
        for function_name in called_functions
        if not _is_allowed_function(
            function_name,
            allow_unqualified_functions=allow_unqualified_functions,
        )
    )
    if unsafe_functions:
        return [
            {
                "database": "postgresql",
                "code": "UNSAFE_FUNCTION",
                "message": (
                    "허용 목록에 없는 함수는 실행할 수 없습니다: "
                    f"{', '.join(unsafe_functions)}"
                ),
            }
        ]
    return []


def validate_cypher_read_only(query: str | None) -> list[GuardViolation]:
    if not query or not query.strip():
        return [
            {
                "database": "neo4j",
                "code": "EMPTY_QUERY",
                "message": "Cypher가 비어 있습니다.",
            }
        ]
    masked_query = _mask_cypher_literals_and_comments(query)
    if _has_multiple_statements(masked_query):
        return [
            {
                "database": "neo4j",
                "code": "MULTIPLE_STATEMENTS",
                "message": "Cypher는 한 문장만 허용됩니다.",
            }
        ]
    tokens = _tokens(masked_query)
    if "RETURN" not in tokens or not (
        {"MATCH", "OPTIONAL", "WITH", "UNWIND"} & set(tokens)
    ):
        return [
            {
                "database": "neo4j",
                "code": "NOT_READ_QUERY",
                "message": "결과를 반환하는 조회 Cypher만 허용됩니다.",
            }
        ]
    forbidden = sorted(set(tokens) & _CYPHER_WRITE_TOKENS)
    if forbidden:
        return [
            {
                "database": "neo4j",
                "code": "WRITE_CLAUSE",
                "message": f"쓰기 구문은 허용되지 않습니다: {', '.join(forbidden)}",
            }
        ]
    return []
