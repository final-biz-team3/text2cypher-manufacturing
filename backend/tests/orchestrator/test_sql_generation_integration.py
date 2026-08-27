"""생성 SQL을 기준 PostgreSQL 결과와 비교한다."""

import re
from collections.abc import Sequence
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()

from core.openai_client import get_openai_client  # noqa: E402
from core.postgres import bootstrap_postgres, get_pool, open_pool  # noqa: E402
from orchestrator.graph import build_orchestrator_graph  # noqa: E402

pytestmark = pytest.mark.integration


async def _execute_read_only(
    pool: Any, query: str
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """단일 조회문을 실행한다. get_pool()의 커넥션은 이미 항상 read_only이므로
    (core/postgres.py::configure_connection) 별도 BEGIN TRANSACTION READ ONLY가
    필요 없다 - 이 테스트 쿼리만 더 타이트한 타임아웃을 걸기 위해 SET LOCAL만 쓴다."""
    statement = query.strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()

    assert re.match(r"(?is)^(select|with)\b", statement)
    assert ";" not in statement

    async with pool.connection() as conn:
        await conn.execute("SET LOCAL statement_timeout = '3000ms'")
        cursor = await conn.execute(statement)
        columns = [column.name for column in cursor.description or ()]
        rows = await cursor.fetchall()

    return columns, rows


def _normalized_column_name(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def _column_index(columns: Sequence[str], *accepted_names: str) -> int:
    normalized_columns = [_normalized_column_name(column) for column in columns]
    for accepted_name in accepted_names:
        normalized_name = _normalized_column_name(accepted_name)
        if normalized_name in normalized_columns:
            return normalized_columns.index(normalized_name)
    raise AssertionError(
        f"Expected one of columns {accepted_names}, but generated columns were {columns}."
    )


@pytest.fixture(scope="module")
async def graph_and_postgres() -> tuple[Any, Any]:
    await bootstrap_postgres()
    await open_pool()
    pool = get_pool()
    graph = build_orchestrator_graph(get_openai_client(), pool)
    return graph, pool


async def test_rq03_active_supplier_count_matches_reference_sql(
    graph_and_postgres,
) -> None:
    """활성 공급업체 수가 기준 SQL의 단일 집계값과 일치한다."""
    graph, pool = graph_and_postgres
    result = await graph.ainvoke({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    _, generated_rows = await _execute_read_only(pool, result["sql_query"])
    _, reference_rows = await _execute_read_only(
        pool,
        "SELECT COUNT(*) FROM purchasing.vendor WHERE activeflag = true",
    )

    assert generated_rows == reference_rows


async def test_rq04_purchased_product_count_matches_reference_sql(
    graph_and_postgres,
) -> None:
    """외부 구매 부품 수가 makeflag 기준 집계값과 일치한다."""
    graph, pool = graph_and_postgres
    result = await graph.ainvoke({"query": "외부에서 구매하는 부품 수를 알려줘."})

    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    _, generated_rows = await _execute_read_only(pool, result["sql_query"])
    _, reference_rows = await _execute_read_only(
        pool,
        "SELECT COUNT(*) FROM production.product WHERE makeflag = false",
    )

    assert generated_rows == reference_rows


async def test_rq05_products_without_sell_end_date_match_reference_sql(
    graph_and_postgres,
) -> None:
    """판매 종료일이 없는 첫 10개 제품이 기준 SQL 결과와 일치한다."""
    graph, pool = graph_and_postgres
    result = await graph.ainvoke(
        {"query": "판매 종료일이 등록되지 않은 제품을 10개만 보여줘."}
    )

    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    generated_columns, generated_rows = await _execute_read_only(
        pool, result["sql_query"]
    )
    _, reference_rows = await _execute_read_only(
        pool,
        "SELECT productid, name, sellenddate "
        "FROM production.product "
        "WHERE sellenddate IS NULL "
        "ORDER BY productid ASC "
        "LIMIT 10",
    )

    product_id_index = _column_index(generated_columns, "productid", "제품 ID")
    product_name_index = _column_index(
        generated_columns, "name", "productname", "제품명"
    )
    sell_end_date_index = _column_index(generated_columns, "sellenddate", "판매 종료일")
    normalized_generated_rows = [
        (
            row[product_id_index],
            row[product_name_index],
            row[sell_end_date_index],
        )
        for row in generated_rows
    ]

    assert normalized_generated_rows == reference_rows


async def test_rq07_product_category_count_matches_reference_sql(
    graph_and_postgres,
) -> None:
    """제품 분류명을 확정하고 해당 분류의 제품 수를 정확히 반환한다."""
    graph, pool = graph_and_postgres
    result = await graph.ainvoke({"query": "Components에 포함된 제품 수를 알려줘."})

    assert result["entity"] == {
        "productCategoryId": 2,
        "productCategoryName": "Components",
    }
    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    generated_columns, generated_rows = await _execute_read_only(
        pool, result["sql_query"]
    )
    _, reference_rows = await _execute_read_only(
        pool,
        "SELECT c.productcategoryid, c.name, COUNT(p.productid) AS productcount "
        "FROM production.productcategory c "
        "JOIN production.productsubcategory s "
        "ON s.productcategoryid = c.productcategoryid "
        "JOIN production.product p "
        "ON p.productsubcategoryid = s.productsubcategoryid "
        "WHERE c.productcategoryid = 2 "
        "GROUP BY c.productcategoryid, c.name "
        "ORDER BY c.productcategoryid ASC",
    )

    category_id_index = _column_index(
        generated_columns,
        "productcategoryid",
        "categoryid",
        "제품분류ID",
    )
    category_name_index = _column_index(
        generated_columns,
        "name",
        "productcategoryname",
        "categoryname",
        "제품분류명",
    )
    product_count_index = _column_index(
        generated_columns,
        "productcount",
        "제품수",
    )
    normalized_generated_rows = [
        (
            row[category_id_index],
            row[category_name_index],
            row[product_count_index],
        )
        for row in generated_rows
    ]

    assert normalized_generated_rows == reference_rows
