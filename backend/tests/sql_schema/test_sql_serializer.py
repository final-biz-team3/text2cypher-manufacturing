"""SQL 스키마의 프롬프트 텍스트 직렬화 동작을 테스트한다."""

from pathlib import Path

from sql_schema.loader import load_sql_schema
from sql_schema.models import SqlSchema
from sql_schema.serializer import serialize_sql_schema

PROJECT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schema" / "sql_schema.yaml"


def test_serialize_sql_schema_formats_tables_primary_keys_and_joins() -> None:
    """단일·복합 PK를 테이블 단위 제약조건으로 직렬화한다."""
    schema = SqlSchema.model_validate(
        {
            "tables": {
                "production.product": {
                    "columns": {
                        "productid": {
                            "type": "INTEGER",
                            "primaryKey": True,
                            "nullable": False,
                        },
                        "name": {"type": "VARCHAR", "nullable": False},
                        "listprice": {"type": "NUMERIC"},
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
                    },
                },
            },
            "joins": [
                {
                    "from": "production.productinventory.productid",
                    "to": "production.product.productid",
                }
            ],
        }
    )

    assert serialize_sql_schema(schema) == """Table schemas:
production.product {productid: INTEGER | NOT NULL, name: VARCHAR | NOT NULL, listprice: NUMERIC, PRIMARY KEY (productid)}
production.productinventory {productid: INTEGER | NOT NULL, locationid: SMALLINT | NOT NULL, PRIMARY KEY (productid, locationid)}
Joins:
production.productinventory.productid -> production.product.productid"""


def test_serialize_sql_schema_appends_aliases_in_a_separate_section() -> None:
    """테이블과 컬럼 alias는 물리 스키마와 분리된 영역에 출력한다."""
    schema = SqlSchema.model_validate(
        {
            "tables": {
                "production.product": {
                    "aliases": ["제품"],
                    "columns": {
                        "listprice": {
                            "type": "NUMERIC",
                            "aliases": ["정가"],
                        },
                        "safetystocklevel": {
                            "type": "SMALLINT",
                            "aliases": ["안전재고", "안전재고 수준"],
                        },
                    },
                },
            },
            "joins": [],
        }
    )

    assert serialize_sql_schema(schema) == """Table schemas:
production.product {listprice: NUMERIC, safetystocklevel: SMALLINT}
Joins:
Aliases:
Table production.product: 제품
Column production.product.listprice: 정가
Column production.product.safetystocklevel: 안전재고, 안전재고 수준"""


def test_serialize_sql_schema_omits_alias_section_when_no_alias_exists() -> None:
    """alias가 없는 입력도 물리 스키마와 Joins 영역을 그대로 유지한다."""
    schema = SqlSchema.model_validate(
        {
            "tables": {
                "production.location": {
                    "columns": {"name": {"type": "VARCHAR"}},
                },
            },
            "joins": [],
        }
    )

    assert serialize_sql_schema(schema) == """Table schemas:
production.location {name: VARCHAR}
Joins:"""


def test_serialize_sql_schema_serializes_project_yaml() -> None:
    """프로젝트 SQL YAML의 최소 스키마와 핵심 한글 alias를 모두 출력한다."""
    schema_text = serialize_sql_schema(load_sql_schema(PROJECT_SCHEMA_PATH))

    assert schema_text.startswith(
        "Table schemas:\nproduction.product " "{productid: INTEGER | NOT NULL"
    )
    assert "production.productinventory {" in schema_text
    assert "production.location {" in schema_text
    assert "PRIMARY KEY (productid)" in schema_text
    assert "PRIMARY KEY (productid, locationid)" in schema_text
    assert "productid: INTEGER | PRIMARY KEY" not in schema_text
    assert "locationid: SMALLINT | PRIMARY KEY" not in schema_text
    assert schema_text.count(" -> ") == 2
    assert (
        "production.productinventory.productid -> production.product.productid"
        in schema_text
    )
    assert (
        "production.productinventory.locationid -> " "production.location.locationid"
    ) in schema_text
    assert "Table production.product: 제품" in schema_text
    assert "Table production.location: 재고 위치" in schema_text
    assert "Column production.product.listprice: 정가" in schema_text
    assert "Column production.product.standardcost: 표준원가" in schema_text
    assert (
        "Column production.product.safetystocklevel: 안전재고, 안전재고 수준"
        in schema_text
    )
    assert (
        "Column production.productinventory.quantity: " "위치별 재고 수량, 재고 수량"
    ) in schema_text
    assert "실제 재고" not in schema_text
    assert "부족 수량" not in schema_text
    assert not schema_text.endswith("\n")
