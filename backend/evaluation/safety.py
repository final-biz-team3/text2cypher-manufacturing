"""후보 및 Gold 쿼리에 운영 가드와 동일한 읽기 전용 정책을 적용한다."""

from evaluation.errors import QuerySafetyError
from guard.query_read_guard import validate_cypher_read_only, validate_sql_read_only
from orchestrator.state import GuardViolation


def _raise_if_unsafe(violations: list[GuardViolation]) -> None:
    if violations:
        raise QuerySafetyError(violations[0]["message"])


def validate_read_only_sql(query: str) -> None:
    """Gold SQL의 관례상 안전한 내장 함수는 비한정 이름도 허용한다."""
    _raise_if_unsafe(validate_sql_read_only(query, allow_unqualified_functions=True))


def validate_read_only_cypher(query: str) -> None:
    """운영 채팅과 같은 Cypher 검증기를 사용한다."""
    _raise_if_unsafe(validate_cypher_read_only(query))
