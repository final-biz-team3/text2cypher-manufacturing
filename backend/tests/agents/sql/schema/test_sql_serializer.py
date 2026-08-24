"""SQL 스키마의 프롬프트 텍스트 직렬화 동작을 테스트한다."""

from pathlib import Path

from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.models import SqlSchema
from agents.sql.schema.serializer import serialize_sql_schema

PROJECT_SCHEMA_PATH = Path(__file__).resolve().parents[5] / "schema" / "sql_schema.yaml"


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
    """프로젝트 SQL YAML의 테이블·조인과 핵심 한글 alias를 출력한다."""
    schema_text = serialize_sql_schema(load_sql_schema(PROJECT_SCHEMA_PATH))

    assert schema_text.startswith(
        "Table schemas:\nproduction.product " "{productid: INTEGER | NOT NULL"
    )
    assert "production.productinventory {" in schema_text
    assert "production.location {" in schema_text
    assert "purchasing.vendor {" in schema_text
    assert "purchasing.productvendor {" in schema_text
    assert "production.productcategory {" in schema_text
    assert "production.productsubcategory {" in schema_text
    assert "production.billofmaterials {" in schema_text
    assert "sales.salesorderdetail {" in schema_text
    assert "purchasing.purchaseorderdetail {" in schema_text
    assert "purchasing.purchaseorderheader {" in schema_text
    assert "production.workorder {" in schema_text
    assert "production.workorderrouting {" in schema_text
    assert "production.scrapreason {" in schema_text
    assert "PRIMARY KEY (productid)" in schema_text
    assert "PRIMARY KEY (productid, locationid)" in schema_text
    assert "PRIMARY KEY (salesorderid, salesorderdetailid)" in schema_text
    assert "PRIMARY KEY (purchaseorderid, purchaseorderdetailid)" in schema_text
    assert "PRIMARY KEY (productid, businessentityid)" in schema_text
    assert "PRIMARY KEY (workorderid, productid, operationsequence)" in schema_text
    assert "productid: INTEGER | PRIMARY KEY" not in schema_text
    assert "locationid: SMALLINT | PRIMARY KEY" not in schema_text
    assert "makeflag: BOOLEAN | NOT NULL" in schema_text
    assert "sellenddate: TIMESTAMP" in schema_text
    assert "production.location {locationid: INTEGER | NOT NULL" in schema_text
    assert schema_text.count(" -> ") == 15
    assert (
        "production.productinventory.productid -> production.product.productid"
        in schema_text
    )
    assert (
        "production.productinventory.locationid -> " "production.location.locationid"
    ) in schema_text
    assert (
        "sales.salesorderdetail.productid -> production.product.productid"
        in schema_text
    )
    assert (
        "production.billofmaterials.productassemblyid -> "
        "production.product.productid"
    ) in schema_text
    assert (
        "production.billofmaterials.componentid -> production.product.productid"
        in schema_text
    )
    assert (
        "purchasing.productvendor.businessentityid -> "
        "purchasing.vendor.businessentityid"
    ) in schema_text
    assert (
        "purchasing.purchaseorderheader.vendorid -> "
        "purchasing.vendor.businessentityid"
    ) in schema_text
    assert (
        "production.workorder.scrapreasonid -> " "production.scrapreason.scrapreasonid"
    ) in schema_text
    assert (
        "production.workorderrouting.workorderid -> " "production.workorder.workorderid"
    ) in schema_text
    assert (
        "production.workorderrouting.locationid -> production.location.locationid"
        in schema_text
    )
    assert "Table production.product: 제품" in schema_text
    assert "Table production.location: 재고 위치, 작업장" in schema_text
    assert "Table purchasing.vendor: 공급업체" in schema_text
    assert "Table purchasing.productvendor: 제품별 공급업체" in schema_text
    assert "Table production.productcategory: 제품 분류" in schema_text
    assert "Table production.billofmaterials: BOM" in schema_text
    assert "Table production.workorder: 작업지시" in schema_text
    assert "Table production.workorderrouting: 작업지시 공정" in schema_text
    assert "Column production.product.listprice: 정가" in schema_text
    assert "Column production.product.standardcost: 표준원가" in schema_text
    assert "Column production.product.makeflag: 자체 생산 여부" in schema_text
    assert "Column production.product.sellenddate: 판매 종료일" in schema_text
    assert "Column production.location.name: 재고 위치명, 작업장명" in schema_text
    assert "Column purchasing.vendor.activeflag: 활성 여부" in schema_text
    assert (
        "Column sales.salesorderdetail.orderqty: 판매 주문 수량, 주문 수량"
        in schema_text
    )
    assert "Column purchasing.purchaseorderdetail.rejectedqty: 반려 수량" in schema_text
    assert "Column production.workorder.scrappedqty: 폐기 수량" in schema_text
    assert (
        "Column production.billofmaterials.perassemblyqty: "
        "조립품 1개당 필요 수량, 단위당 필요 수량"
    ) in schema_text
    assert (
        "Column production.workorderrouting.operationsequence: 공정 순서, 작업 순서"
        in schema_text
    )
    assert (
        "Column production.product.safetystocklevel: 안전재고, 안전재고 수준"
        in schema_text
    )
    assert (
        "Column production.productinventory.quantity: " "위치별 재고 수량, 재고 수량"
    ) in schema_text
    assert "실제 재고" not in schema_text
    assert "부족 수량" not in schema_text
    assert "외부 구매 부품" not in schema_text
    assert "판매량" not in schema_text
    assert "총 반려 수량" not in schema_text
    assert "순위" not in schema_text
    assert not schema_text.endswith("\n")
