from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata

_PATTERNS = (
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "[EMAIL]"),
    (
        re.compile(
            r"(?i)\b(?:bearer\s+)?eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
        ),
        "[TOKEN]",
    ),
    (re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*\S+"), "[SECRET]"),
    (re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@"), r"\1[USER]:[PASSWORD]@"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}\b"), "[UUID]"),
    (re.compile(r"(?<!\d)(?:01[016789][ -]?\d{3,4}[ -]?\d{4})(?!\d)"), "[PHONE]"),
    (re.compile(r"(['\"]).*?\1"), "[VALUE]"),
    (re.compile(r"(?<!\w)\d{4,}(?!\w)"), "[NUMBER]"),
)

_QUERY_LITERAL_PATTERNS = (
    # SQL과 Cypher 문자열 리터럴. 식별자용 backtick은 보존한다.
    re.compile(r"'(?:''|\\.|[^'])*'"),
    re.compile(r'"(?:""|\\.|[^"])*"'),
    # 정수·실수 리터럴. 파라미터명과 식별자에 포함된 숫자는 보존한다.
    re.compile(r"(?<![\w$])[-+]?\d+(?:\.\d+)?(?!\w)"),
)


def normalize_question(question: str) -> str:
    value = unicodedata.normalize("NFC", question).strip()
    return re.sub(r"\s+", " ", value).lower()


def question_fingerprint(question: str) -> str:
    secret = os.getenv(
        "QUESTION_FINGERPRINT_SECRET", "development-only-question-fingerprint-key"
    )
    return hmac.new(
        secret.encode(),
        f"qfp_v1:{normalize_question(question)}".encode(),
        hashlib.sha256,
    ).hexdigest()


def query_hash(query: str) -> str:
    secret = os.getenv(
        "QUESTION_FINGERPRINT_SECRET", "development-only-question-fingerprint-key"
    )
    return hmac.new(
        secret.encode(), f"query_v1:{query.strip()}".encode(), hashlib.sha256
    ).hexdigest()


def redact_query(query: str) -> str | None:
    """성공·실패 분석용 쿼리 구조만 남기고 값은 제거한다.

    Loki label이 아닌 JSON 상세 필드에만 넣는 것을 전제로 하며, 운영에서는
    OBS_LOG_FAILED_QUERY=false로 비활성화할 수 있다. 기존 환경변수 이름은
    하위 호환을 위해 유지한다.
    """
    if os.getenv("OBS_LOG_FAILED_QUERY", "false").lower() != "true":
        return None
    try:
        value = unicodedata.normalize("NFC", query).strip()
        for pattern, replacement in _PATTERNS[:-3]:
            value = pattern.sub(replacement, value)
        for pattern in _QUERY_LITERAL_PATTERNS:
            value = pattern.sub("[VALUE]", value)
        maximum = max(160, int(os.getenv("OBS_LOG_FAILED_QUERY_MAX_CHARS", "4000")))
        if len(value) > maximum:
            return f"{value[:maximum]}\n… [TRUNCATED]"
        return value
    except Exception:
        return None


def redact_question(question: str, entity_names: list[str] | None = None) -> str | None:
    if os.getenv("OBS_LOG_QUESTION_PREVIEW", "true").lower() != "true":
        return None
    try:
        value = unicodedata.normalize("NFC", question)
        for name in entity_names or []:
            if name:
                value = re.sub(re.escape(name), "[ENTITY]", value, flags=re.IGNORECASE)
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value[:160]
    except Exception:
        return None
