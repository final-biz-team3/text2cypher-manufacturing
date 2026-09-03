"""LLM이 생성한 SQL을 실행 직전에 파싱해 쓰기 절·미허가 테이블·위험 함수 호출을
차단한다. 스키마 화이트리스트는 schema/sql_schema.yaml(SqlSchema)을 그대로
재사용한다."""

from collections.abc import Callable
from typing import Any

import sqlparse
from sqlparse import tokens as sql_tokens
from sqlparse.sql import IdentifierList, Parenthesis, Statement

from agents.sql.schema.models import SqlSchema
from orchestrator.guards.result import GuardResult
from orchestrator.guards.shared import SQL_WRITE_KEYWORDS

# 세션에 부작용을 남기거나(advisory lock은 rollback으로 안 풀림) 서버 파일
# 시스템/원격 접속에 접근하는 함수는 SELECT 형태를 유지한 채 쓰기 키워드
# 검사와 테이블 화이트리스트 검사를 모두 우회할 수 있어 이름으로 따로 막는다.
_FORBIDDEN_FUNCTION_PREFIXES = ("pg_advisory_", "pg_try_advisory_")
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_stat_file",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_connect",
        "dblink_exec",
    }
)


def _is_forbidden_function(name: str) -> bool:
    lowered = name.lower()
    return lowered in _FORBIDDEN_FUNCTIONS or lowered.startswith(
        _FORBIDDEN_FUNCTION_PREFIXES
    )


def _function_name_candidate(tok: Any) -> str | None:
    """함수 호출 이름 후보를 뽑는다. 쌍따옴표로 감싼 식별자
    (Token.Literal.String.Symbol, 예: "pg_advisory_lock"(42))도 PostgreSQL에서는
    동일 함수를 호출하는 유효한 인용 표기라 따옴표를 벗겨 함께 검사해야 한다 -
    Token.Name만 보면 이 형태로 검사를 우회할 수 있다."""
    if tok.ttype is sql_tokens.Name:
        return str(tok.value)
    if tok.ttype is sql_tokens.Literal.String.Symbol:
        raw = str(tok.value)
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            return raw[1:-1]
    return None


def _find_forbidden_function_call(statement: Statement) -> str | None:
    """평탄화한 토큰에서 '이름 바로 뒤에 (' 형태(함수 호출)를 찾아 위험 함수인지 본다.
    WHERE절/서브쿼리 등 어디에 있든(중첩 깊이 무관) 잡기 위해 flatten()을 쓴다."""
    tokens = [tok for tok in statement.flatten() if not tok.is_whitespace]
    for index, tok in enumerate(tokens):
        name = _function_name_candidate(tok)
        if name is not None and _is_forbidden_function(name):
            if index + 1 < len(tokens) and tokens[index + 1].value == "(":
                return name
    return None


def _find_parenthesis(token: Any) -> Parenthesis | None:
    if isinstance(token, Parenthesis):
        return token
    for child in getattr(token, "tokens", []):
        if isinstance(child, Parenthesis):
            return child
    return None


def _iter_table_candidates(token: Any) -> list[Any]:
    if isinstance(token, IdentifierList):
        return list(token.get_identifiers())
    return [token]


def _safe_call(obj: Any, method_name: str) -> Any:
    """sqlparse Identifier에만 있는 get_real_name()/get_parent_name()을
    그 메서드가 없는 토큰 타입에도 안전하게 호출한다(없으면 None)."""
    return getattr(obj, method_name, lambda: None)()


def _iter_nested_select_parens(token: Any) -> list[Parenthesis]:
    """FROM/JOIN 뒤가 아닌 위치(SELECT절 스칼라 서브쿼리, WHERE IN/EXISTS/ANY 등)에
    있는 서브쿼리까지 전부 찾는다. FROM/JOIN 기준 순회만으로는 이런 위치의
    서브쿼리를 방문하지 않아 그 안의 미허가 테이블 참조가 그대로 통과하던
    우회가 있었다(실제로 재현됨) - 문 전체를 위치 무관하게 전위 순회한다."""
    found: list[Parenthesis] = []
    for child in getattr(token, "tokens", []):
        if isinstance(child, Parenthesis):
            # Parenthesis.tokens[0]은 여는 괄호 '(' 자신이므로 그다음 토큰을 봐야 한다.
            inner = [tok for tok in child.tokens if not tok.is_whitespace][1:]
            if inner and inner[0].ttype is sql_tokens.DML:
                found.append(child)
        found.extend(_iter_nested_select_parens(child))
    return found


def _walk_for_tables(tokens: list[Any], cte_names: set[str], tables: set[str]) -> bool:
    """FROM/JOIN 뒤에 오는 실제 테이블 참조를 tables에 채운다. WITH로 정의된 CTE
    이름은 cte_names에 등록해 실테이블 검사에서 제외한다(그렇지 않으면 정상적인
    CTE 사용까지 미해석 참조로 차단됨). 서브쿼리/CTE 본문은 재귀적으로 같은
    검사를 적용한다. 스키마 없이 쓰인(unqualified) 참조나 해석 불가능한 참조를
    만나면 True(unresolved)를 반환해 fail-closed 시킨다 - 정규식 기반 추출이
    콤마로 나열된 테이블이나 스키마 생략 테이블을 놓치던 우회를 막기 위함이다."""
    non_ws = [tok for tok in tokens if not tok.is_whitespace]
    unresolved = False
    pending_from = False
    index = 0
    while index < len(non_ws):
        tok = non_ws[index]

        if tok.ttype is sql_tokens.Keyword.CTE and tok.normalized.upper() == "WITH":
            pending_from = False
            index += 1
            # "WITH RECURSIVE x AS (...)"에서 RECURSIVE는 CTE 이름이 아니라
            # 별도 키워드 토큰이다 - 건너뛰지 않으면 이걸 CTE 목록으로 오인해
            # 정상 재귀 CTE 쿼리를 오탐 차단한다(실제로 재현된 회귀).
            if (
                index < len(non_ws)
                and non_ws[index].ttype is sql_tokens.Keyword
                and non_ws[index].normalized.upper() == "RECURSIVE"
            ):
                index += 1
            if index < len(non_ws):
                for cte_ident in _iter_table_candidates(non_ws[index]):
                    name = _safe_call(cte_ident, "get_real_name")
                    if isinstance(name, str) and name:
                        cte_names.add(name.lower())
                    paren = _find_parenthesis(cte_ident)
                    if paren is not None and _walk_for_tables(
                        paren.tokens, cte_names, tables
                    ):
                        unresolved = True
                index += 1
            continue

        if tok.ttype is sql_tokens.Keyword:
            normalized = tok.normalized.upper()
            if normalized == "FROM" or "JOIN" in normalized:
                pending_from = True
                index += 1
                continue

        if pending_from:
            pending_from = False
            for candidate in _iter_table_candidates(tok):
                paren = _find_parenthesis(candidate)
                if paren is not None:
                    if _walk_for_tables(paren.tokens, cte_names, tables):
                        unresolved = True
                    continue
                real_name = _safe_call(candidate, "get_real_name")
                parent_name = _safe_call(candidate, "get_parent_name")
                if not isinstance(real_name, str) or not real_name:
                    unresolved = True
                    continue
                if parent_name is None:
                    if real_name.lower() in cte_names:
                        continue
                    unresolved = True
                    continue
                tables.add(f"{parent_name}.{real_name}".lower())
            index += 1
            continue

        index += 1

    return unresolved


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
                if normalized in SQL_WRITE_KEYWORDS:
                    return GuardResult(
                        False,
                        "WRITE_KEYWORD_DETECTED",
                        f"쓰기 키워드 감지: {normalized}",
                    )

        forbidden_function = _find_forbidden_function_call(statement)
        if forbidden_function is not None:
            return GuardResult(
                False,
                "FORBIDDEN_FUNCTION_CALL",
                f"위험 함수 호출 감지: {forbidden_function}",
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

        cte_names: set[str] = set()
        tables: set[str] = set()
        unresolved = _walk_for_tables(statement.tokens, cte_names, tables)
        for nested_paren in _iter_nested_select_parens(statement):
            if _walk_for_tables(nested_paren.tokens, cte_names, tables):
                unresolved = True
        if unresolved:
            return GuardResult(
                False,
                "UNRESOLVED_TABLE_REFERENCE",
                "테이블 참조를 해석할 수 없어(스키마 미기재 등) 차단합니다.",
            )

        unknown_tables = tables - allowed_tables
        if unknown_tables:
            return GuardResult(
                False,
                "UNKNOWN_TABLE",
                f"스키마에 없는 테이블 참조: {', '.join(sorted(unknown_tables))}",
            )

        return GuardResult(True)

    return guard
