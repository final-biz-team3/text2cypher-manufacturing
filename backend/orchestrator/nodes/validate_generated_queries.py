"""라우팅 결과에 따라 생성된 SQL과 Cypher를 읽기 전용으로 검증한다."""

import logging

from guard.query_read_guard import validate_cypher_read_only, validate_sql_read_only
from orchestrator.state import (
    GuardViolation,
    OrchestratorState,
    QueryGuardNodeResult,
    QueryGuardResult,
)

logger = logging.getLogger(__name__)


def validate_generated_queries(state: OrchestratorState) -> QueryGuardNodeResult:
    tool_plan = state.get("tool_plan", [])
    violations: list[GuardViolation] = []
    sql_read_only: bool | None = None
    cypher_read_only: bool | None = None

    if "sql" in tool_plan:
        sql_violations = validate_sql_read_only(state.get("sql_query"))
        violations.extend(sql_violations)
        sql_read_only = not sql_violations

    if "graph" in tool_plan:
        cypher_violations = validate_cypher_read_only(state.get("cypher_query"))
        violations.extend(cypher_violations)
        cypher_read_only = not cypher_violations

    if not tool_plan:
        violations.append(
            {
                "database": "postgresql",
                "code": "MISSING_TOOL_PLAN",
                "message": "검사할 실행 계획이 없습니다.",
            }
        )

    allowed = not violations
    result: QueryGuardResult = {
        "decision": "PASSED" if allowed else "BLOCKED",
        "sql_read_only": sql_read_only,
        "cypher_read_only": cypher_read_only,
        "violations": violations,
    }
    response: QueryGuardNodeResult = {
        "query_guard": result,
        "execution_allowed": allowed,
    }
    log = logger.info if allowed else logger.warning
    log(
        "query guard: decision=%s violations=%s",
        result["decision"],
        result["violations"],
    )
    if not allowed:
        response["error"] = "생성된 쿼리가 읽기 전용 정책을 통과하지 못했습니다."
    return response
