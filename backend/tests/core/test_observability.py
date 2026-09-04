from core.observability.context import (
    get_request_context,
    new_request_context,
    reset_request_context,
    set_request_context,
)
from core.observability.events import _event_summary, normalize_route, normalize_tool
from core.observability.privacy import (
    question_fingerprint,
    redact_query,
    redact_question,
)


def test_question_fingerprint_is_stable_and_secret_dependent(monkeypatch) -> None:
    monkeypatch.setenv("QUESTION_FINGERPRINT_SECRET", "a" * 32)
    first = question_fingerprint("  제품   수량  ")
    assert first == question_fingerprint("제품 수량")
    monkeypatch.setenv("QUESTION_FINGERPRINT_SECRET", "b" * 32)
    assert first != question_fingerprint("제품 수량")


def test_question_preview_redacts_sensitive_values(monkeypatch) -> None:
    monkeypatch.setenv("OBS_LOG_QUESTION_PREVIEW", "true")
    result = redact_question("test@example.com 010-1234-5678 장비 ABC-1", ["ABC-1"])
    assert result is not None
    assert "test@example.com" not in result
    assert "010-1234-5678" not in result
    assert "ABC-1" not in result


def test_failed_query_keeps_structure_and_redacts_literals(monkeypatch) -> None:
    monkeypatch.setenv("OBS_LOG_FAILED_QUERY", "true")
    result = redact_query(
        "MATCH (p:Product) WHERE p.name = '비밀 제품' AND p.stock > 120 "
        "RETURN p.name LIMIT 10"
    )

    assert result is not None
    assert "MATCH (p:Product)" in result
    assert "'비밀 제품'" not in result
    assert "120" not in result
    assert "10" not in result
    assert result.count("[VALUE]") == 3


def test_failed_query_logging_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("OBS_LOG_FAILED_QUERY", raising=False)
    assert redact_query("RETURN 1") is None


def test_request_context_is_set_and_reset() -> None:
    context = new_request_context("untrusted-client-id")
    token = set_request_context(context)
    assert get_request_context() is context
    assert context.client_request_id == "untrusted-client-id"
    reset_request_context(token)
    assert get_request_context() is None


def test_low_cardinality_values_are_normalized() -> None:
    assert normalize_route("hybrid") == "HYBRID"
    assert normalize_route("arbitrary") == "UNKNOWN"
    assert normalize_tool("SQL") == "sql"
    assert normalize_tool("arbitrary") == "none"


def test_failure_summary_explains_the_situation_in_korean() -> None:
    summary = _event_summary(
        "query.attempt.completed",
        {
            "route": "GRAPH",
            "tool": "graph",
            "outcome": "failure",
            "attempt": 2,
            "max_attempts": 3,
            "issue_code": "CYPHER_UNKNOWN_PROPERTY",
            "failure_reason": "스키마에 없는 속성을 참조했습니다.",
        },
    )

    assert summary == (
        "GRAPH 쿼리 실행에 실패했습니다 (2/3회) — "
        "스키마에 없는 속성을 참조했습니다."
    )


def test_pipeline_summary_reports_recovery_and_duration() -> None:
    summary = _event_summary(
        "query.pipeline.completed",
        {
            "route": "SQL",
            "tool": "none",
            "outcome": "success",
            "final_status": "recovered",
            "duration_ms": 2158.2,
        },
    )

    assert summary == "SQL 질문 처리를 자기수정 후 성공했습니다 (2.16초)"


def test_pipeline_summary_reports_final_failure_as_failure() -> None:
    summary = _event_summary(
        "query.pipeline.completed",
        {
            "route": "GRAPH",
            "tool": "none",
            "outcome": "failure",
            "final_status": "repair_exhausted",
            "duration_ms": 4881.9,
        },
    )

    assert summary == "GRAPH 질문 처리를 자기수정 횟수를 소진해 실패했습니다 (4.88초)"
