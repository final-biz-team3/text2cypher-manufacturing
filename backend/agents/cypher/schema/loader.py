"""그래프 스키마 YAML 로드 기능을 제공한다."""

from pathlib import Path

import yaml

from agents.cypher.schema.models import GraphSchema


def load_graph_schema(path: str | Path) -> GraphSchema:
    """YAML 파일을 읽어 검증된 그래프 스키마로 반환한다."""
    schema_path = Path(path)

    with schema_path.open(encoding="utf-8") as schema_file:
        schema_data = yaml.safe_load(schema_file)

    return GraphSchema.model_validate(schema_data)
