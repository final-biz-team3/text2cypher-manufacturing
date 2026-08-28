"""후보 및 Gold 쿼리의 단일 읽기 문장 정책.

금지 키워드 목록과 마스킹 로직은 orchestrator/guards/shared.py와 공유한다 -
과거 이 파일과 orchestrator 쪽 가드가 각자 목록을 들고 있다가 서로 어긋난 적이
있어(EXECUTE/ANALYZE 등), 같은 소스를 쓰도록 통합했다."""

import re

from evaluation.errors import QuerySafetyError
from orchestrator.guards.shared import (
    CYPHER_WRITE_KEYWORDS,
    SQL_WRITE_KEYWORDS,
    mask_query_text,
)

_SQL_START = {"SELECT", "WITH"}
_CYPHER_START = {"MATCH", "OPTIONAL", "RETURN", "UNWIND", "WITH"}


def _validate_single_statement(query: str) -> tuple[str, set[str]]:
    if not isinstance(query, str) or not query.strip():
        raise QuerySafetyError("쿼리가 비어 있습니다.")
    if "\x00" in query:
        raise QuerySafetyError("쿼리에 NUL 문자가 있습니다.")
    masked = mask_query_text(query).strip()
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
    forbidden = sorted(tokens & SQL_WRITE_KEYWORDS)
    if forbidden:
        raise QuerySafetyError(f"SQL 쓰기/관리 키워드가 포함됐습니다: {forbidden[0]}")


def validate_read_only_cypher(query: str) -> None:
    """Cypher가 허용된 읽기 clause로 시작하고 쓰기 clause가 없는지 확인한다."""
    first, tokens = _validate_single_statement(query)
    if first not in _CYPHER_START:
        raise QuerySafetyError("Cypher는 읽기 clause로 시작해야 합니다.")
    forbidden = sorted(tokens & CYPHER_WRITE_KEYWORDS)
    if forbidden:
        raise QuerySafetyError(
            f"Cypher 쓰기/프로시저 키워드가 포함됐습니다: {forbidden[0]}"
        )
