"""쿼리 가드 감사 로그가 별도 파일에 JSON Lines로 쌓이는지 검증한다."""

import json
import logging

import orchestrator.guards.audit as audit_module
from orchestrator.guards.audit import log_guard_decision


def test_log_guard_decision_writes_json_line_to_dedicated_file(
    tmp_path, monkeypatch
) -> None:
    """호출할 때마다 지정 경로에 JSON 한 줄이 append된다."""
    log_path = tmp_path / "query_guard_audit.jsonl"
    monkeypatch.setenv("GUARD_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("GUARD_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_for_tests()

    log_guard_decision(
        query_type="sql_agent",
        intent="제품 수를 알려줘.",
        decision="BLOCK",
        stage="pre_execution_guard",
        reason="WRITE_KEYWORD_DETECTED",
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record == {
        "query_type": "sql_agent",
        "intent": "제품 수를 알려줘.",
        "decision": "BLOCK",
        "stage": "pre_execution_guard",
        "reason": "WRITE_KEYWORD_DETECTED",
    }


def test_log_guard_decision_does_not_propagate_to_root_logger_by_default(
    tmp_path, monkeypatch
) -> None:
    """GUARD_AUDIT_ALSO_CONSOLE이 꺼져 있으면 루트 로거(콘솔)로 전파되지 않는다.

    pytest의 caplog는 propagate=False인 로거에도 자체 캡처 핸들러를 직접
    붙여버린다(_pytest.logging.catching_logs 참고 - "non-propagating
    loggers"까지 일부러 챙겨서 캡처하는 게 의도된 동작이다). 그래서
    caplog.records로는 propagate=False가 실제로 걸렸는지 검증할 수
    없다 - 대신 이 함수가 통제하는 실제 속성(logger.propagate)을
    직접 확인한다."""
    log_path = tmp_path / "query_guard_audit.jsonl"
    monkeypatch.setenv("GUARD_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("GUARD_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_for_tests()

    log_guard_decision(
        query_type="sql_agent",
        intent="질의",
        decision="ALLOW",
        stage="pre_execution_guard",
        reason=None,
    )

    assert logging.getLogger("orchestrator.guard_audit").propagate is False


def test_log_guard_decision_propagates_when_console_enabled(
    tmp_path, monkeypatch
) -> None:
    """GUARD_AUDIT_ALSO_CONSOLE=true면 루트 로거로도 전파된다."""
    log_path = tmp_path / "query_guard_audit.jsonl"
    monkeypatch.setenv("GUARD_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("GUARD_AUDIT_ALSO_CONSOLE", "true")
    audit_module.reset_for_tests()

    log_guard_decision(
        query_type="sql_agent",
        intent="질의",
        decision="ALLOW",
        stage="pre_execution_guard",
        reason=None,
    )

    assert logging.getLogger("orchestrator.guard_audit").propagate is True
