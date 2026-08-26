"""후보 및 Gold 쿼리의 단일 읽기 문장 정책."""

import re

from evaluation.errors import QuerySafetyError

_SQL_START = {"SELECT", "WITH"}
_SQL_WRITE = {
    "ALTER",
    "ANALYZE",
    "CALL",
    "CLUSTER",
    "COMMENT",
    "COPY",
    "CREATE",
    "DELETE",
    "DO",
    "DROP",
    "GRANT",
    "INSERT",
    "LOCK",
    "MERGE",
    "REFRESH",
    "REINDEX",
    "REVOKE",
    "TRUNCATE",
    "UPDATE",
    "VACUUM",
}
_CYPHER_START = {"MATCH", "OPTIONAL", "RETURN", "UNWIND", "WITH"}
_CYPHER_WRITE = {
    "CALL",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "FOREACH",
    "LOAD",
    "MERGE",
    "REMOVE",
    "SET",
}


def _masked(query: str) -> str:
    """문자열·식별자·주석 안의 키워드와 세미콜론을 공백으로 가린다."""
    output: list[str] = []
    index = 0
    length = len(query)
    while index < length:
        char = query[index]
        next_char = query[index + 1] if index + 1 < length else ""
        if char == "-" and next_char == "-":
            index += 2
            while index < length and query[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and next_char in {"/", "*"}:
            terminator = "\n" if next_char == "/" else "*/"
            index += 2
            while index < length:
                if terminator == "\n" and query[index] in "\r\n":
                    break
                if terminator == "*/" and query[index : index + 2] == "*/":
                    output.extend((" ", " "))
                    index += 2
                    break
                output.append(" ")
                index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            output.append(" ")
            index += 1
            while index < length:
                output.append(" ")
                if query[index] == quote:
                    if index + 1 < length and query[index + 1] == quote:
                        output.append(" ")
                        index += 2
                        continue
                    index += 1
                    break
                if query[index] == "\\" and index + 1 < length:
                    output.append(" ")
                    index += 2
                    continue
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _validate_single_statement(query: str) -> tuple[str, set[str]]:
    if not isinstance(query, str) or not query.strip():
        raise QuerySafetyError("쿼리가 비어 있습니다.")
    if "\x00" in query:
        raise QuerySafetyError("쿼리에 NUL 문자가 있습니다.")
    masked = _masked(query).strip()
    if masked.endswith(";"):
        masked = masked[:-1].rstrip()
    if ";" in masked:
        raise QuerySafetyError("다중 문장은 실행할 수 없습니다.")
    tokens = re.findall(r"[A-Za-z_]+", masked.upper())
    if not tokens:
        raise QuerySafetyError("쿼리에서 문장을 찾을 수 없습니다.")
    return tokens[0], set(tokens)


def validate_read_only_sql(query: str) -> None:
    """SQL이 SELECT/읽기 전용 WITH 단일 문장인지 확인한다."""
    first, tokens = _validate_single_statement(query)
    if first not in _SQL_START:
        raise QuerySafetyError("SQL은 SELECT 또는 WITH로 시작해야 합니다.")
    forbidden = sorted(tokens & _SQL_WRITE)
    if forbidden:
        raise QuerySafetyError(f"SQL 쓰기/관리 키워드가 포함됐습니다: {forbidden[0]}")


def validate_read_only_cypher(query: str) -> None:
    """Cypher가 허용된 읽기 clause로 시작하고 쓰기 clause가 없는지 확인한다."""
    first, tokens = _validate_single_statement(query)
    if first not in _CYPHER_START:
        raise QuerySafetyError("Cypher는 읽기 clause로 시작해야 합니다.")
    forbidden = sorted(tokens & _CYPHER_WRITE)
    if forbidden:
        raise QuerySafetyError(
            f"Cypher 쓰기/프로시저 키워드가 포함됐습니다: {forbidden[0]}"
        )
