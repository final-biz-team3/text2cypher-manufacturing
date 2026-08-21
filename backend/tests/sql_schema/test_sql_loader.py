"""SQL 스키마 YAML 로더의 입력 처리 동작을 테스트한다."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sql_schema.loader import load_sql_schema
from sql_schema.models import SqlSchema

PROJECT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schema" / "sql_schema.yaml"


def test_load_sql_schema_returns_validated_model(tmp_path: Path) -> None:
    """정상 YAML 파일을 검증된 SQL 스키마 모델로 반환한다."""
    schema_path = tmp_path / "sql_schema.yaml"
    schema_path.write_text(
        """
tables:
  production.product:
    aliases: [제품]
    columns:
      productid: {type: INTEGER, primaryKey: true, nullable: false}
      listprice: {type: NUMERIC, aliases: [정가]}
joins: []
""".lstrip(),
        encoding="utf-8",
    )

    schema = load_sql_schema(schema_path)

    assert isinstance(schema, SqlSchema)
    assert schema.tables["production.product"].aliases == ["제품"]
    product_columns = schema.tables["production.product"].columns
    assert product_columns["productid"].primary_key is True
    assert product_columns["productid"].nullable is False
    assert product_columns["listprice"].data_type == "NUMERIC"
    assert product_columns["listprice"].aliases == ["정가"]


def test_load_sql_schema_loads_project_schema() -> None:
    """프로젝트의 기준 YAML 파일을 SQL 스키마 모델로 읽는다."""
    schema = load_sql_schema(PROJECT_SCHEMA_PATH)

    assert len(schema.tables) == 3
    assert sum(len(table.columns) for table in schema.tables.values()) == 12
    assert len(schema.joins) == 2
    inventory_columns = schema.tables["production.productinventory"].columns
    assert inventory_columns["productid"].primary_key is True
    assert inventory_columns["locationid"].primary_key is True
    assert schema.tables["production.location"].aliases == ["재고 위치"]


def test_load_sql_schema_raises_when_file_does_not_exist(tmp_path: Path) -> None:
    """지정한 YAML 파일이 없으면 FileNotFoundError를 그대로 전달한다."""
    with pytest.raises(FileNotFoundError):
        load_sql_schema(tmp_path / "missing_schema.yaml")


def test_load_sql_schema_raises_for_malformed_yaml(tmp_path: Path) -> None:
    """YAML 문법이 잘못된 파일은 YAMLError를 그대로 전달한다."""
    malformed_yaml_path = tmp_path / "malformed.yaml"
    malformed_yaml_path.write_text("tables: [", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_sql_schema(malformed_yaml_path)


@pytest.mark.parametrize(
    "yaml_content",
    [
        pytest.param("", id="empty-document"),
        pytest.param("- production.product\n", id="non-mapping-root"),
        pytest.param("tables: {}\njoins: []\n", id="empty-tables"),
    ],
)
def test_load_sql_schema_rejects_schema_with_invalid_structure(
    tmp_path: Path,
    yaml_content: str,
) -> None:
    """YAML 문법은 유효하지만 입력 구조가 잘못되면 검증 오류를 전달한다."""
    invalid_schema_path = tmp_path / "invalid_schema_structure.yaml"
    invalid_schema_path.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(ValidationError):
        load_sql_schema(invalid_schema_path)
