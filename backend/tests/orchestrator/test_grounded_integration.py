"""Real read-only database boundary with synthetic model responses, never Gold."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

from core.postgres import configure_connection, postgres_conninfo
from orchestrator.graph import build_orchestrator_graph
from orchestrator.grounded.models import CandidateReview, QueryIntent


@pytest.mark.integration
async def test_enabled_pipeline_reads_real_db_and_preserves_answer_references(
    monkeypatch,
):
    settings = Path(__file__).resolve().parents[4] / "text2cypher-llm-answer/.env"
    load_dotenv(settings)
    monkeypatch.setenv("GROUNDED_QUERY_ENABLED", "true")
    monkeypatch.setenv("GROUNDED_PLAN_VERSION", "1")
    query = 'SELECT COUNT(*) AS "observedCount" FROM production.product'
    intent = QueryIntent.model_validate(
        {
            "action": "read",
            "requirements": [
                {
                    "id": "count",
                    "kind": "aggregation",
                    "description": "count all products",
                    "evidence": {"text": "제품 수", "source": "question"},
                }
            ],
            "entities": [],
            "outputs": [
                {
                    "alias": "observedCount",
                    "label": "제품 수",
                    "tool": "sql",
                    "source_fields": ["sql:production.product.productid"],
                    "expression": "COUNT(*)",
                    "value_type": "number",
                    "nullable": False,
                    "unit": None,
                    "evidence": {"text": "제품 수", "source": "question"},
                }
            ],
            "assumptions": [],
            "clarification": None,
            "reason": "",
        }
    )
    monkeypatch.setattr(
        "orchestrator.grounded.interpret.typed_call", AsyncMock(return_value=intent)
    )
    monkeypatch.setattr(
        "orchestrator.grounded.validation.typed_call",
        AsyncMock(
            return_value=CandidateReview.model_validate(
                {
                    "interpretation_complete": True,
                    "checks": [
                        {
                            "requirement_id": "count",
                            "verdict": "supported",
                            "tool": "sql",
                            "query_excerpt": "COUNT(*)",
                            "explanation": "all rows counted",
                        }
                    ],
                    "clarification": None,
                }
            )
        ),
    )

    def strict_builder(_client, _pool, **kwargs):
        async def run(inputs):
            result = await kwargs["execute_sql_fn"](query)
            rows = result["rows"]
            return {
                "sql_query": query,
                "tool_plan": ["sql"],
                "subqueries": [],
                "composed_result": {
                    "mode": "single",
                    "rows": rows,
                    "sections": {},
                    "error": None,
                    "truncated": False,
                },
            }

        return run

    monkeypatch.setattr(
        "orchestrator.grounded.pipeline.build_strict_query", strict_builder
    )
    async with AsyncConnectionPool(
        postgres_conninfo(), configure=configure_connection, open=False
    ) as pool:
        graph = build_orchestrator_graph(object(), pool)
        result = await graph.ainvoke({"query": "제품 수"})
    assert result["query_strategy"] == "strict"
    assert result["execution_evidence"]["sql"]["columns"] == ["observedCount"]
    count = result["composed_result"]["rows"][0]["observedCount"]
    assert isinstance(count, int) and count >= 0
    assert str(count) in result["final_answer"]
    assert result["validation_report"]["candidates"][0]["accepted"]


@pytest.mark.integration
async def test_synthetic_postgres_variants_do_not_relax_empty_conditions():
    import psycopg

    settings = Path(__file__).resolve().parents[4] / "text2cypher-llm-answer/.env"
    load_dotenv(settings)
    # Session-local table; no changes to manufacturing tables or schema.
    async with await psycopg.AsyncConnection.connect(postgres_conninfo()) as conn:
        await conn.execute(
            "CREATE TEMP TABLE grounded_sample(id int, qty int, happened date)"
        )
        await conn.execute(
            "INSERT INTO grounded_sample VALUES (1,5,'2024-01-01'),(2,6,'2024-01-02'),(3,NULL,'2024-01-03')"
        )
        for threshold, expected in ((5, [1, 2]), (6, [2]), (7, [])):
            cursor = await conn.execute(
                "SELECT id FROM grounded_sample WHERE qty >= %s AND happened >= DATE '2024-01-01' ORDER BY id",
                (threshold,),
            )
            assert [row[0] for row in await cursor.fetchall()] == expected
        await conn.execute("INSERT INTO grounded_sample VALUES (4,5,'2023-12-31')")
        cursor = await conn.execute(
            "SELECT COUNT(qty),COUNT(*) FROM grounded_sample WHERE happened >= DATE '2024-01-01'"
        )
        assert await cursor.fetchone() == (2, 3)
        await conn.rollback()


@pytest.mark.integration
async def test_graph_reader_plan_metadata_and_verified_empty():
    from orchestrator.execution.cypher_executor import close_reader_driver
    from orchestrator.grounded.execution import graph_candidate

    settings = Path(__file__).resolve().parents[4] / "text2cypher-llm-answer/.env"
    load_dotenv(settings)
    evidence: dict[str, Any] = {}
    try:
        result = await graph_candidate(
            "MATCH (p:Product) RETURN count(p) AS observedCount", evidence=evidence
        )
        assert result["rows"][0]["observedCount"] > 0
        assert evidence["graph"]["columns"] == ["observedCount"]
        result = await graph_candidate(
            "MATCH (p:Product) WHERE false RETURN p.productId AS observedId",
            evidence=evidence,
        )
        assert result["rows"] == []
        assert evidence["graph"]["columns"] == ["observedId"]
    finally:
        await close_reader_driver()


@pytest.mark.integration
async def test_v2_parameter_rows_preserve_null_and_duplicate_pairs():
    from agents.cypher.schema.loader import load_graph_schema
    from agents.sql.schema.loader import load_sql_schema
    from orchestrator.execution.cypher_executor import close_reader_driver
    from orchestrator.grounded.plan_execution import make_step_executor
    from orchestrator.grounded.plan_models import SourceStep
    from orchestrator.guards.cypher_guard import make_cypher_guard
    from orchestrator.guards.sql_guard import make_sql_guard

    root = Path(__file__).resolve().parents[3]
    load_dotenv(root.parent / "text2cypher-llm-answer/.env")
    rows = [{"id": 1, "qty": None}, {"id": 1, "qty": 5}, {"id": 2, "qty": 8}]
    async with AsyncConnectionPool(
        postgres_conninfo(), configure=configure_connection, open=False
    ) as pool:
        execute = make_step_executor(
            pool,
            make_sql_guard(load_sql_schema(root / "schema/sql_schema.yaml")),
            make_cypher_guard(load_graph_schema(root / "schema/graph_schema.yaml")),
        )
        try:
            for tool, query in (
                (
                    "sql",
                    "SELECT r.id, r.qty FROM jsonb_to_recordset(%(input)s) AS r(id int, qty int) ORDER BY r.id, r.qty NULLS FIRST",
                ),
                (
                    "graph",
                    "UNWIND $input AS r RETURN r.id AS id, r.qty AS qty ORDER BY id, CASE WHEN qty IS NULL THEN 0 ELSE 1 END, qty",
                ),
            ):
                step = SourceStep.model_validate(
                    dict(
                        id="bound",
                        tool=tool,
                        query=query,
                        parameters=[],
                        bindings=[],
                        depends_on=[],
                        requirement_ids=[],
                        projections=[],
                        relationships=[],
                    )
                )
                actual = await execute(step, {"input": rows})
                assert actual.rows == rows
                assert actual.columns == ["id", "qty"]
                assert not actual.truncated
                assert (await execute(step, {"input": []})).rows == []
        finally:
            await close_reader_driver()
