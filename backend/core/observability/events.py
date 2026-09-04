from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from uuid_utils import uuid7

from core.observability.context import get_request_context, request_id

logger = logging.getLogger("itda.observability")

_ROUTES = {"UNKNOWN", "SQL", "GRAPH", "HYBRID"}
_TOOLS = {"none", "sql", "graph"}
_OUTCOMES = {"success", "failure", "skipped", "blocked"}

_EVENT_SUMMARIES = {
    "http.request.started": "HTTP 요청 시작",
    "http.request.completed": "HTTP 요청 완료",
    "http.request.failed": "HTTP 요청 실패",
    "model.call.started": "모델 호출 시작",
    "model.call.completed": "모델 호출 완료",
    "model.call.failed": "모델 호출 실패",
    "routing.completed": "실행 경로 선택",
    "planning.completed": "실행 계획 완료",
    "query.generated": "쿼리 생성",
    "query.attempt.started": "쿼리 실행 시작",
    "query.attempt.completed": "쿼리 실행 완료",
    "tool.execution.started": "도구 실행 시작",
    "tool.execution.completed": "도구 실행 완료",
    "tool.execution.failed": "도구 실행 실패",
    "tool.execution.skipped": "도구 실행 생략",
    "repair.decision.made": "자기수정 판단",
    "repair.completed": "자기수정 성공",
    "repair.exhausted": "자기수정 횟수 소진",
    "query.pipeline.completed": "질문 처리 종료",
    "audit.guard.decision": "쿼리 안전성 판단",
    "failure.review.created": "실패 검토 항목 생성",
    "admin.review.viewed": "관리자 검토 조회",
    "admin.review.updated": "관리자 검토 변경",
}

_FINAL_STATUS_KO = {
    "first_attempt_success": "첫 시도에 성공했습니다",
    "recovered": "자기수정 후 성공했습니다",
    "accepted_empty": "결과 없음으로 정상 종료했습니다",
    "partial_success": "일부 조회만 성공했습니다",
    "repair_exhausted": "자기수정 횟수를 소진해 실패했습니다",
    "policy_blocked": "안전 정책에 의해 차단됐습니다",
    "internal_failure": "내부 오류로 실패했습니다",
    "infrastructure_failure": "외부 시스템 연결 문제로 실패했습니다",
}


def _duration_ko(value: Any) -> str:
    try:
        milliseconds = float(value)
    except (TypeError, ValueError):
        return ""
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f}초"
    return f"{milliseconds:.0f}ms"


def _attempt_ko(record: dict[str, Any]) -> str:
    attempt = record.get("attempt")
    maximum = record.get("max_attempts")
    if attempt is None:
        return ""
    return f"{attempt}/{maximum}회" if maximum is not None else f"{attempt}회"


def _event_summary(event_name: str, record: dict[str, Any]) -> str:
    """검색용 코드는 유지하면서 발표·운영 화면용 한국어 한 줄을 만든다."""
    route = record.get("route", "UNKNOWN")
    tool = str(record.get("tool", "none")).upper()
    target = tool if tool != "NONE" else route
    outcome = record.get("outcome")
    reason = record.get("failure_reason")
    issue_code = record.get("issue_code")
    failure = reason or issue_code
    duration = _duration_ko(record.get("duration_ms"))
    attempt = _attempt_ko(record)

    if event_name == "http.request.started":
        return f"{record.get('method', 'HTTP')} {record.get('path', '')} 요청을 시작했습니다".replace("  ", " ")
    if event_name == "http.request.completed":
        status = record.get("status_code", "-")
        suffix = f" ({duration})" if duration else ""
        return f"{record.get('method', 'HTTP')} {record.get('path', '')} 요청이 HTTP {status} 상태로 완료됐습니다{suffix}".replace("  ", " ")
    if event_name == "http.request.failed":
        return f"{record.get('method', 'HTTP')} {record.get('path', '')} 요청 처리에 실패했습니다".replace("  ", " ")
    if event_name == "routing.completed":
        return f"질문을 {route} 경로로 분류했습니다"
    if event_name == "planning.completed":
        count = record.get("subquery_count")
        return f"{route} 실행 계획을 만들었습니다" + (f" (하위 질의 {count}개)" if count is not None else "")
    if event_name == "model.call.started":
        return f"{record.get('model_purpose', '응답')} 모델 호출을 시작했습니다"
    if event_name == "model.call.completed":
        return f"{record.get('model_purpose', '응답')} 모델 호출이 완료됐습니다" + (f" ({duration})" if duration else "")
    if event_name == "model.call.failed":
        return f"{record.get('model_purpose', '응답')} 모델 호출에 실패했습니다"
    if event_name == "query.generated":
        return f"{target} 쿼리를 생성했습니다" + (f" ({attempt})" if attempt else "")
    if event_name == "query.attempt.started":
        return f"{target} 쿼리 실행을 시작했습니다" + (f" ({attempt})" if attempt else "")
    if event_name == "query.attempt.completed":
        if outcome == "failure":
            return f"{target} 쿼리 실행에 실패했습니다" + (f" ({attempt})" if attempt else "") + (f" — {failure}" if failure else "")
        return f"{target} 쿼리 실행이 완료됐습니다" + (f" ({attempt})" if attempt else "") + (f" · {duration}" if duration else "")
    if event_name == "tool.execution.started":
        return f"{target} 조회를 시작했습니다"
    if event_name == "tool.execution.completed":
        return f"{target} 조회가 완료됐습니다" + (f" ({duration})" if duration else "")
    if event_name == "tool.execution.failed":
        return f"{target} 조회에 실패했습니다" + (f" — {failure}" if failure else "")
    if event_name == "tool.execution.skipped":
        skip_reason = record.get("skip_reason", "선행 조건 불충족")
        return f"{target} 조회를 생략했습니다 — {skip_reason}"
    if event_name == "repair.decision.made":
        action = "쿼리를 수정해 다시 시도합니다" if record.get("decision") == "retry" else "추가 시도를 중단합니다"
        return f"{target} {action}" + (f" ({attempt})" if attempt else "") + (f" — {failure}" if failure else "")
    if event_name == "repair.completed":
        return f"{target} 쿼리가 자기수정으로 복구됐습니다" + (f" ({attempt})" if attempt else "")
    if event_name == "repair.exhausted":
        return f"{target} 쿼리 자기수정에 실패해 처리를 중단했습니다" + (f" ({attempt})" if attempt else "") + (f" — {failure}" if failure else "")
    if event_name == "query.pipeline.completed":
        final_status = str(record.get("final_status") or "")
        result = _FINAL_STATUS_KO.get(final_status, "성공적으로 완료했습니다" if outcome == "success" else "실패했습니다")
        return f"{route} 질문 처리를 {result}" + (f" ({duration})" if duration else "")
    if event_name == "audit.guard.decision":
        decision = "허용했습니다" if record.get("decision") == "ALLOW" else "차단했습니다"
        return f"{target} 쿼리를 안전성 검사에서 {decision}" + (f" — {issue_code}" if issue_code else "")
    if event_name == "failure.review.created":
        return "운영자 확인이 필요한 실패를 검토 목록에 등록했습니다" + (f" — {issue_code}" if issue_code else "")
    return _EVENT_SUMMARIES.get(event_name, event_name)


def normalize_route(value: str | None) -> str:
    normalized = (value or "UNKNOWN").upper()
    return normalized if normalized in _ROUTES else "UNKNOWN"


def normalize_tool(value: str | None) -> str:
    normalized = (value or "none").lower()
    return normalized if normalized in _TOOLS else "none"


def emit_event(
    event_name: str,
    event_category: str,
    *,
    level: str = "INFO",
    force: bool = False,
    **fields: Any,
) -> None:
    if os.getenv("OBSERVABILITY_ENABLED", "true").lower() != "true":
        return
    try:
        context = get_request_context()
        outcome = fields.pop("outcome", "success")
        if outcome not in _OUTCOMES:
            outcome = "failure"
        if context and outcome == "failure":
            context.failed = True
        if context and not (
            force or context.sampled or context.failed or outcome != "success"
        ):
            return
        record: dict[str, Any] = {
            "@timestamp": datetime.now(UTC).isoformat(),
            "event_id": str(uuid7()),
            "event_version": 1,
            "event_name": event_name,
            "event_category": event_category,
            "level": level.upper(),
            "service_name": os.getenv("SERVICE_NAME", "itda-backend"),
            "deployment_environment": os.getenv(
                "DEPLOYMENT_ENVIRONMENT", os.getenv("APP_ENV", "dev")
            ),
            "service_version": os.getenv("SERVICE_VERSION", "dev"),
            "request_id": request_id(),
            "route": normalize_route(
                fields.pop("route", context.route if context else None)
            ),
            "tool": normalize_tool(fields.pop("tool", None)),
            "outcome": outcome,
        }
        if context:
            if context.question_fingerprint:
                record["question_fingerprint"] = context.question_fingerprint
                record["fingerprint_version"] = "qfp_v1"
            if context.question_redacted:
                record["question_redacted"] = context.question_redacted
        record.update(
            {
                key: value
                for key, value in fields.items()
                if value is not None and value != ""
            }
        )
        record["summary"] = _event_summary(event_name, record)
        logger.log(
            getattr(logging, level.upper(), logging.INFO),
            "observability_event",
            extra={"observability_event": record},
        )
    except Exception:
        # Observability is deliberately fail-open.
        return
