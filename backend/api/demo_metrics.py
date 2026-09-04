import hmac
import os

from fastapi import APIRouter, Header, HTTPException

from core.observability.metrics import (
    CHAT_DURATION,
    CHAT_REQUESTS,
    DB_QUERY_DURATION,
    DROPPED_EVENTS,
    EMPTY_RESULTS,
    FAILURE_REVIEWS,
    MODEL_CACHE_WRITE_TOKENS,
    MODEL_CACHED_INPUT_TOKENS,
    MODEL_CALL_DURATION,
    MODEL_CALLS,
    MODEL_ESTIMATED_COST,
    MODEL_INPUT_TOKENS,
    MODEL_OUTPUT_TOKENS,
    MODEL_REASONING_TOKENS,
    PIPELINE_NODE_DURATION,
    PLANNED_TOOLS,
    QUERY_ATTEMPT_DURATION,
    QUERY_ATTEMPTS,
    REPAIR_EXHAUSTED,
    REPAIRS,
    ROUTING,
    TOOL_EXECUTIONS,
    TOOL_SKIPS,
)

router = APIRouter()


def _check_demo_access(token: str) -> None:
    if os.getenv("APP_ENV", "development") != "development":
        raise HTTPException(status_code=404)
    expected = os.getenv("METRICS_SCRAPE_TOKEN", "")
    if not expected or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=404)


@router.post("/internal/demo/metrics", include_in_schema=False)
async def seed_demo_metrics(x_metrics_token: str = Header(default="")) -> dict[str, int]:
    """개발용 Grafana 화면 검증을 위해 실제 메트릭 라벨 조합을 증가시킨다."""
    _check_demo_access(x_metrics_token)

    scenarios = (
        ("GRAPH", "first_attempt_success", 1.24),
        ("SQL", "recovered", 2.16),
        ("GRAPH", "repair_exhausted", 4.88),
        ("SQL", "policy_blocked", 0.10),
        ("HYBRID", "partial_success", 3.41),
        ("GRAPH", "infrastructure_failure", 1.77),
        ("SQL", "internal_failure", 0.82),
    )
    for route, status, duration in scenarios:
        CHAT_REQUESTS.labels(route, status).inc()
        CHAT_DURATION.labels(route, status).observe(duration)
        ROUTING.labels(route, "success").inc()

    for route, tool in (("SQL", "sql"), ("GRAPH", "graph"), ("HYBRID", "sql"), ("HYBRID", "graph")):
        PLANNED_TOOLS.labels(route, tool).inc()
        TOOL_EXECUTIONS.labels(route, tool, "success").inc()
    TOOL_EXECUTIONS.labels("GRAPH", "graph", "failure").inc()
    TOOL_SKIPS.labels("HYBRID", "graph", "DEPENDENCY_FAILED").inc()
    TOOL_SKIPS.labels("HYBRID", "sql", "DEPENDENCY_EMPTY").inc()

    attempts = (
        ("sql", "none", "success", 0.09),
        ("sql", "SQL_UNDEFINED_COLUMN", "failure", 0.04),
        ("graph", "none", "success", 0.18),
        ("graph", "CYPHER_SYNTAX_ERROR", "failure", 0.07),
        ("graph", "CYPHER_OUTPUT_CONTRACT_FAILED", "failure", 0.12),
    )
    for tool, code, outcome, duration in attempts:
        QUERY_ATTEMPTS.labels(tool, code, outcome).inc()
        QUERY_ATTEMPT_DURATION.labels(tool, outcome).observe(duration)

    REPAIRS.labels("sql", "SQL_UNDEFINED_COLUMN", "success", "v1").inc()
    REPAIRS.labels("graph", "CYPHER_OUTPUT_CONTRACT_FAILED", "failure", "v1").inc()
    REPAIR_EXHAUSTED.labels("graph", "CYPHER_OUTPUT_CONTRACT_FAILED").inc()
    FAILURE_REVIEWS.labels("GRAPH", "graph", "CYPHER_OUTPUT_CONTRACT_FAILED").inc()

    EMPTY_RESULTS.labels("sql", "NO_DATA").inc()
    EMPTY_RESULTS.labels("graph", "INCONCLUSIVE").inc()
    for node, duration in (("resolve_entity", 0.12), ("route_query", 0.08), ("plan_outputs", 0.15), ("execute_plan", 0.44), ("generate_answer", 0.62)):
        PIPELINE_NODE_DURATION.labels(node).observe(duration)
    for tool, outcome, duration in (("sql", "success", 0.09), ("sql", "failure", 0.04), ("graph", "success", 0.18), ("graph", "failure", 0.12)):
        DB_QUERY_DURATION.labels(tool, outcome).observe(duration)

    for purpose, outcome, duration, input_tokens, output_tokens in (
        ("route_query", "success", 0.31, 820, 96),
        ("generate_sql", "success", 0.74, 1450, 180),
        ("generate_cypher", "success", 0.81, 1580, 205),
        ("generate_answer", "success", 0.66, 1120, 240),
        ("generate_answer", "failure", 2.01, 740, 0),
    ):
        MODEL_CALLS.labels(purpose, outcome).inc()
        MODEL_CALL_DURATION.labels(purpose, outcome).observe(duration)
        MODEL_INPUT_TOKENS.labels(purpose).inc(input_tokens)
        MODEL_OUTPUT_TOKENS.labels(purpose).inc(output_tokens)
        MODEL_CACHED_INPUT_TOKENS.labels(purpose).inc(input_tokens // 3)
        MODEL_CACHE_WRITE_TOKENS.labels(purpose).inc(input_tokens // 10)
        MODEL_REASONING_TOKENS.labels(purpose).inc(output_tokens // 4)
        MODEL_ESTIMATED_COST.labels(purpose).inc((input_tokens + output_tokens) * 0.000001)

    DROPPED_EVENTS.labels("logging", "queue_full").inc()
    return {"seeded_scenarios": len(scenarios)}
