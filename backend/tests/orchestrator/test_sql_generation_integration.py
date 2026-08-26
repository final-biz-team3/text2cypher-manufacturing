"""생성 SQL을 기준 PostgreSQL 결과와 비교한다."""

import re
from collections.abc import Sequence
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()

from core.openai_client import get_openai_client  # noqa: E402
from core.postgres import get_connection  # noqa: E402
from orchestrator.graph import build_orchestrator_graph  # noqa: E402

pytestmark = pytest.mark.integration


def _execute_read_only(
    postgres_connection: Any, query: str
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """단일 조회문을 제한된 읽기 전용 트랜잭션에서 실행한다."""
    statement = query.strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()

    assert re.match(r"(?is)^(select|with)\b", statement)
    assert ";" not in statement

    postgres_connection.rollback()
    try:
        postgres_connection.execute("BEGIN TRANSACTION READ ONLY")
        postgres_connection.execute("SET LOCAL statement_timeout = '3000ms'")
        cursor = postgres_connection.execute(statement)
        columns = [column.name for column in cursor.description or ()]
        rows = cursor.fetchall()
    finally:
        postgres_connection.rollback()

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
def graph_and_postgres() -> tuple[Any, Any]:
    postgres_connection = get_connection()
    graph = build_orchestrator_graph(get_openai_client(), postgres_connection)
    return graph, postgres_connection


def test_rq03_active_supplier_count_matches_reference_sql(graph_and_postgres) -> None:
    """활성 공급업체 수가 기준 SQL의 단일 집계값과 일치한다."""
    graph, postgres_connection = graph_and_postgres
    result = graph.invoke({"query": "현재 활성 상태인 공급업체 수를 알려줘."})

    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    _, generated_rows = _execute_read_only(postgres_connection, result["sql_query"])
    _, reference_rows = _execute_read_only(
        postgres_connection,
        "SELECT COUNT(*) FROM purchasing.vendor WHERE activeflag = true",
    )

    assert generated_rows == reference_rows


def test_rq04_purchased_product_count_matches_reference_sql(
    graph_and_postgres,
) -> None:
    """외부 구매 부품 수가 makeflag 기준 집계값과 일치한다."""
    graph, postgres_connection = graph_and_postgres
    result = graph.invoke({"query": "외부에서 구매하는 부품 수를 알려줘."})

    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    _, generated_rows = _execute_read_only(postgres_connection, result["sql_query"])
    _, reference_rows = _execute_read_only(
        postgres_connection,
        "SELECT COUNT(*) FROM production.product WHERE makeflag = false",
    )

    assert generated_rows == reference_rows


def test_rq05_products_without_sell_end_date_match_reference_sql(
    graph_and_postgres,
) -> None:
    """판매 종료일이 없는 첫 10개 제품이 기준 SQL 결과와 일치한다."""
    graph, postgres_connection = graph_and_postgres
    result = graph.invoke(
        {"query": "판매 종료일이 등록되지 않은 제품을 10개만 보여줘."}
    )

    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    generated_columns, generated_rows = _execute_read_only(
        postgres_connection, result["sql_query"]
    )
    _, reference_rows = _execute_read_only(
        postgres_connection,
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


def test_rq07_product_category_count_matches_reference_sql(
    graph_and_postgres,
) -> None:
    """제품 분류명을 확정하고 해당 분류의 제품 수를 정확히 반환한다."""
    graph, postgres_connection = graph_and_postgres
    result = graph.invoke({"query": "Components에 포함된 제품 수를 알려줘."})

    assert result["entity"] == {
        "productCategoryId": 2,
        "productCategoryName": "Components",
    }
    assert result["tool_plan"] == ["sql"]
    assert result["cypher_query"] is None
    assert result["sql_query"] is not None

    generated_columns, generated_rows = _execute_read_only(
        postgres_connection, result["sql_query"]
    )
    _, reference_rows = _execute_read_only(
        postgres_connection,
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
