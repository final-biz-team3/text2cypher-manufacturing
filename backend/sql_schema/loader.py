"""SQL 스키마 YAML 로드 기능을 제공한다."""

from pathlib import Path

import yaml

from sql_schema.models import SqlSchema


def load_sql_schema(path: str | Path) -> SqlSchema:
    """YAML 파일을 읽어 검증된 SQL 스키마로 반환한다."""
    schema_path = Path(path)

    with schema_path.open(encoding="utf-8") as schema_file:
        schema_data = yaml.safe_load(schema_file)

    return SqlSchema.model_validate(schema_data)
