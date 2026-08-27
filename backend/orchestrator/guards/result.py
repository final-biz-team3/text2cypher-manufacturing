"""쿼리 가드(SQL/Cypher 공용)의 판정 결과 타입."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardResult:
    """쿼리 가드 판정 결과. reason_code는 감사 로그·재시도 피드백에 쓰이는
    코드값(예: WRITE_KEYWORD_DETECTED)이고, reason_detail은 사람이 읽는 설명이다."""

    allowed: bool
    reason_code: str | None = None
    reason_detail: str | None = None
