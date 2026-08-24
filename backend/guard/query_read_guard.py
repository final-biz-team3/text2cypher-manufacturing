"""생성된 PostgreSQL SQL과 Neo4j Cypher의 읽기 전용 여부를 검사한다."""

import re

from orchestrator.state import GuardViolation

_SQL_WRITE_TOKENS = {
    "ALTER",
    "CALL",
    "COPY",
    "CREATE",
    "DELETE",
    "DO",
    "DROP",
    "GRANT",
    "INSERT",
    "MERGE",
    "REVOKE",
    "SET",
    "TRUNCATE",
    "UPDATE",
}
_CYPHER_WRITE_TOKENS = {
    "CALL",
    "CREATE",
    "DELETE",
    "DROP",
    "FOREACH",
    "LOAD",
    "MERGE",
    "REMOVE",
    "SET",
}


def _strip_literals_and_comments(query: str) -> str:
    query = re.sub(r"/\*.*?\*/", " ", query, flags=re.S)
    query = re.sub(r"--[^\r\n]*", " ", query)
    query = re.sub(r"//[^\r\n]*", " ", query)
    query = re.sub(r"'(?:''|\\.|[^'])*'", "''", query)
    query = re.sub(r'"(?:""|\\.|[^"])*"', '""', query)
    query = re.sub(r"`(?:``|[^`])*`", "``", query)
    return query


def _has_multiple_statements(query: str) -> bool:
    body = _strip_literals_and_comments(query).strip()
    if body.endswith(";"):
        body = body[:-1]
    return ";" in body


def _tokens(query: str) -> list[str]:
    clean = _strip_literals_and_comments(query)
    return re.findall(r"[A-Za-z_]+", clean.upper())


def validate_sql_read_only(query: str | None) -> list[GuardViolation]:
    if not query or not query.strip():
        return [{"database": "postgresql", "code": "EMPTY_QUERY", "message": "SQL이 비어 있습니다."}]
    if _has_multiple_statements(query):
        return [{"database": "postgresql", "code": "MULTIPLE_STATEMENTS", "message": "SQL은 한 문장만 허용됩니다."}]
    tokens = _tokens(query)
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        return [{"database": "postgresql", "code": "NOT_READ_QUERY", "message": "SELECT 조회문만 허용됩니다."}]
    forbidden = sorted(set(tokens) & _SQL_WRITE_TOKENS)
    if forbidden:
        return [{"database": "postgresql", "code": "WRITE_CLAUSE", "message": f"쓰기 구문은 허용되지 않습니다: {', '.join(forbidden)}"}]
    if re.search(r"\bFOR\s+(UPDATE|SHARE)\b", _strip_literals_and_comments(query), re.I):
        return [{"database": "postgresql", "code": "LOCKING_READ", "message": "행 잠금 조회는 허용되지 않습니다."}]
    return []


def validate_cypher_read_only(query: str | None) -> list[GuardViolation]:
    if not query or not query.strip():
        return [{"database": "neo4j", "code": "EMPTY_QUERY", "message": "Cypher가 비어 있습니다."}]
    if _has_multiple_statements(query):
        return [{"database": "neo4j", "code": "MULTIPLE_STATEMENTS", "message": "Cypher는 한 문장만 허용됩니다."}]
    tokens = _tokens(query)
    if "RETURN" not in tokens or not ({"MATCH", "OPTIONAL", "WITH", "UNWIND"} & set(tokens)):
        return [{"database": "neo4j", "code": "NOT_READ_QUERY", "message": "결과를 반환하는 조회 Cypher만 허용됩니다."}]
    forbidden = sorted(set(tokens) & _CYPHER_WRITE_TOKENS)
    if forbidden:
        return [{"database": "neo4j", "code": "WRITE_CLAUSE", "message": f"쓰기 구문은 허용되지 않습니다: {', '.join(forbidden)}"}]
    return []
