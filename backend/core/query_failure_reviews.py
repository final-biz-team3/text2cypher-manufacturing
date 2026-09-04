from __future__ import annotations

from datetime import datetime
from typing import Any

from core.observability.events import emit_event
from core.observability.metrics import FAILURE_REVIEWS

STATUSES = {
    "NEW",
    "TRIAGED",
    "REPRODUCED",
    "FIX_PLANNED",
    "FIXED",
    "WONT_FIX",
    "DUPLICATE",
}
CLASSIFICATIONS = {
    "QUESTION_FILTER",
    "ENTITY_RESOLUTION",
    "ROUTING",
    "PLANNING",
    "SQL_GENERATION",
    "CYPHER_GENERATION",
    "SCHEMA_CONTEXT",
    "REPAIR_POLICY",
    "INFRASTRUCTURE",
    "EVALUATION_DATA",
    "OTHER",
}
RESOLVED = {"FIXED", "WONT_FIX", "DUPLICATE"}


async def create_failure_review(
    conn: Any,
    *,
    conversation_id: int,
    request_id: str,
    question_fingerprint: str,
    route: str,
    failed_stage: str,
    failed_tool: str | None,
    issue_code: str,
    sql_attempt_count: int,
    graph_attempt_count: int,
) -> int:
    cursor = await conn.execute(
        "INSERT INTO app.query_failure_reviews (conversation_id,request_id,question_fingerprint,route,failed_stage,failed_tool,issue_code,sql_attempt_count,graph_attempt_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (conversation_id) DO UPDATE SET updated_at=now() RETURNING review_id",
        (
            conversation_id,
            request_id,
            question_fingerprint,
            route,
            failed_stage,
            failed_tool,
            issue_code,
            sql_attempt_count,
            graph_attempt_count,
        ),
    )
    row = await cursor.fetchone()
    review_id = int(row[0])
    FAILURE_REVIEWS.labels(route, failed_tool or "none", issue_code).inc()
    emit_event(
        "failure.review.created",
        "admin_review",
        force=True,
        route=route,
        tool=failed_tool,
        review_id=review_id,
        issue_code=issue_code,
    )
    return review_id


async def list_failure_reviews(
    pool: Any,
    *,
    status: str | None = None,
    classification: str | None = None,
    route: str | None = None,
    tool: str | None = None,
    issue_code: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("status", status),
        ("classification", classification),
        ("route", route),
        ("failed_tool", tool),
        ("issue_code", issue_code),
    ):
        if value is not None:
            clauses.append(f"{column} = %s")
            params.append(value)
    if date_from:
        clauses.append("created_at >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= %s")
        params.append(date_to)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    async with pool.connection() as conn:
        count_row = await (
            await conn.execute(
                "SELECT count(*) FROM app.query_failure_reviews" + where, tuple(params)
            )
        ).fetchone()
        rows = await (
            await conn.execute(
                "SELECT review_id,conversation_id,request_id,question_fingerprint,route,failed_stage,failed_tool,issue_code,sql_attempt_count,graph_attempt_count,status,classification,assignee,version,created_at,updated_at,resolved_at FROM app.query_failure_reviews"
                + where
                + " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                tuple([*params, page_size, (page - 1) * page_size]),
            )
        ).fetchall()
    keys = (
        "review_id",
        "conversation_id",
        "request_id",
        "question_fingerprint",
        "route",
        "failed_stage",
        "failed_tool",
        "issue_code",
        "sql_attempt_count",
        "graph_attempt_count",
        "status",
        "classification",
        "assignee",
        "version",
        "created_at",
        "updated_at",
        "resolved_at",
    )
    return {
        "items": [dict(zip(keys, row, strict=True)) for row in rows],
        "total": int(count_row[0]),
        "page": page,
        "page_size": page_size,
    }


async def get_failure_review(pool: Any, review_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT r.review_id,r.conversation_id,r.request_id,r.question_fingerprint,r.route,r.failed_stage,r.failed_tool,r.issue_code,r.sql_attempt_count,r.graph_attempt_count,r.status,r.classification,r.assignee,r.notes,r.fixture_id,r.issue_url,r.pr_url,r.version,r.created_at,r.updated_at,r.resolved_at,c.username,c.query,c.final_answer,c.sql_query,c.cypher_query,c.sql_result,c.graph_result FROM app.query_failure_reviews r JOIN app.conversation_history c ON c.id=r.conversation_id WHERE r.review_id=%s",
                (review_id,),
            )
        ).fetchone()
    if row is None:
        return None
    keys = (
        "review_id",
        "conversation_id",
        "request_id",
        "question_fingerprint",
        "route",
        "failed_stage",
        "failed_tool",
        "issue_code",
        "sql_attempt_count",
        "graph_attempt_count",
        "status",
        "classification",
        "assignee",
        "notes",
        "fixture_id",
        "issue_url",
        "pr_url",
        "version",
        "created_at",
        "updated_at",
        "resolved_at",
        "username",
        "query",
        "final_answer",
        "sql_query",
        "cypher_query",
        "sql_result",
        "graph_result",
    )
    return dict(zip(keys, row, strict=True))


async def update_failure_review(
    pool: Any, review_id: int, version: int, changes: dict[str, Any]
) -> dict[str, Any] | None:
    clean = {
        k: v
        for k, v in changes.items()
        if k
        in {
            "status",
            "classification",
            "assignee",
            "notes",
            "fixture_id",
            "issue_url",
            "pr_url",
        }
    }
    if not clean:
        return await get_failure_review(pool, review_id)
    sets = [f"{key}=%s" for key in clean]
    params = list(clean.values())
    if "status" in clean:
        sets.append(
            "resolved_at=" + ("now()" if clean["status"] in RESOLVED else "NULL")
        )
    params.extend([review_id, version])
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "UPDATE app.query_failure_reviews SET "
                + ",".join(sets)
                + ",updated_at=now(),version=version+1 WHERE review_id=%s AND version=%s RETURNING review_id",
                tuple(params),
            )
        ).fetchone()
        await conn.commit()
    if row is None:
        return None
    emit_event(
        "admin.review.updated",
        "admin_review",
        force=True,
        review_id=review_id,
        status=clean.get("status"),
    )
    return await get_failure_review(pool, review_id)
