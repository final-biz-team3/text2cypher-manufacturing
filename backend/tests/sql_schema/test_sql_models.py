"""SQL 스키마 모델의 입력 검증 동작을 테스트한다."""

import pytest
from pydantic import ValidationError

from sql_schema.models import SqlSchema


def test_sql_schema_maps_valid_tables_columns_joins_and_aliases() -> None:
    """정상 입력의 컬럼 제약조건, 복합 PK, 조인과 alias를 매핑한다."""
    schema = SqlSchema.model_validate(
        {
            "tables": {
                "production.product": {
                    "aliases": ["제품"],
                    "columns": {
                        "productid": {
                            "type": "INTEGER",
                            "primaryKey": True,
                            "nullable": False,
                        },
                    },
                },
                "production.productinventory": {
                    "columns": {
                        "productid": {
                            "type": "INTEGER",
                            "primaryKey": True,
                            "nullable": False,
                        },
                        "locationid": {
                            "type": "SMALLINT",
                            "primaryKey": True,
                            "nullable": False,
                        },
                        "quantity": {
                            "type": "SMALLINT",
                            "aliases": ["위치별 재고 수량", "재고 수량"],
                        },
                    },
                },
            },
            "joins": [
                {
                    "from": "production.productinventory.productid",
                    "to": "production.product.productid",
                },
            ],
        }
    )

    inventory_columns = schema.tables["production.productinventory"].columns
    join = schema.joins[0]

    assert schema.tables["production.product"].aliases == ["제품"]
    assert inventory_columns["productid"].primary_key is True
    assert inventory_columns["locationid"].primary_key is True
    assert inventory_columns["quantity"].data_type == "SMALLINT"
    assert inventory_columns["quantity"].nullable is True
    assert inventory_columns["quantity"].aliases == [
        "위치별 재고 수량",
        "재고 수량",
    ]
    assert join.from_column == "production.productinventory.productid"
    assert join.to_column == "production.product.productid"


def test_sql_schema_ignores_metadata_outside_prompt_contract() -> None:
    """프롬프트 계약에 포함되지 않는 스키마 메타데이터는 무시한다."""
    schema = SqlSchema.model_validate(
        {
            "meta": {"revision": 1},
            "tables": {
                "production.product": {
                    "source": "AdventureWorks",
                    "columns": {
                        "productid": {
                            "type": "INTEGER",
                            "primaryKey": True,
                            "nullable": False,
                            "description": "surrogate key",
                        },
                    },
                },
            },
            "joins": [],
        }
    )

    assert schema.model_dump() == {
        "tables": {
            "production.product": {
                "aliases": [],
                "columns": {
                    "productid": {
                        "data_type": "INTEGER",
                        "primary_key": True,
                        "nullable": False,
                        "aliases": [],
                    },
                },
            },
        },
        "joins": [],
    }


def test_sql_schema_rejects_empty_tables() -> None:
    """테이블이 하나도 없는 SQL 스키마는 거부한다."""
    with pytest.raises(ValidationError):
        SqlSchema.model_validate({"tables": {}, "joins": []})


def test_column_schema_rejects_unsupported_data_type() -> None:
    """프로젝트 범위에서 지원하지 않는 SQL 타입은 거부한다."""
    with pytest.raises(ValidationError):
        SqlSchema.model_validate(
            {
                "tables": {
                    "production.product": {
                        "columns": {"name": {"type": "TEXT"}},
                    },
                },
                "joins": [],
            }
        )


@pytest.mark.parametrize(
    ("table_aliases", "column_aliases"),
    [
        pytest.param("제품", [], id="table-alias-string"),
        pytest.param([], "제품명", id="column-alias-string"),
    ],
)
def test_sql_schema_rejects_aliases_that_are_not_a_list(
    table_aliases: object,
    column_aliases: object,
) -> None:
    """alias는 하나뿐이어도 문자열 목록으로만 입력받는다."""
    with pytest.raises(ValidationError):
        SqlSchema.model_validate(
            {
                "tables": {
                    "production.product": {
                        "aliases": table_aliases,
                        "columns": {
                            "name": {
                                "type": "VARCHAR",
                                "aliases": column_aliases,
                            },
                        },
                    },
                },
                "joins": [],
            }
        )


@pytest.mark.parametrize(
    ("endpoint", "error_message"),
    [
        pytest.param(
            "warehouse.stock.productid",
            "unknown table 'warehouse.stock'",
            id="unknown-table",
        ),
        pytest.param(
            "production.productinventory.locationid",
            "unknown column 'production.productinventory.locationid'",
            id="unknown-column",
        ),
        pytest.param(
            "productinventory.productid",
            "expected 'schema.table.column'",
            id="invalid-endpoint-format",
        ),
    ],
)
def test_sql_schema_rejects_join_with_invalid_source_endpoint(
    endpoint: str,
    error_message: str,
) -> None:
    """조인 시작점의 형식 또는 존재하지 않는 테이블·컬럼 참조를 거부한다."""
    with pytest.raises(ValidationError, match=error_message):
        SqlSchema.model_validate(
            {
                "tables": {
                    "production.product": {
                        "columns": {"productid": {"type": "INTEGER"}},
                    },
                    "production.productinventory": {
                        "columns": {"productid": {"type": "INTEGER"}},
                    },
                },
                "joins": [
                    {
                        "from": endpoint,
                        "to": "production.product.productid",
                    }
                ],
            }
        )


def test_sql_schema_rejects_join_with_unknown_target_column() -> None:
    """조인 도착점도 시작점과 동일하게 실제 컬럼인지 검사한다."""
    with pytest.raises(
        ValidationError,
        match="unknown column 'production.product.missingid'",
    ):
        SqlSchema.model_validate(
            {
                "tables": {
                    "production.product": {
                        "columns": {"productid": {"type": "INTEGER"}},
                    },
                    "production.productinventory": {
                        "columns": {"productid": {"type": "INTEGER"}},
                    },
                },
                "joins": [
                    {
                        "from": "production.productinventory.productid",
                        "to": "production.product.missingid",
                    }
                ],
            }
        )
