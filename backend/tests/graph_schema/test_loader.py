"""그래프 스키마 YAML 로더의 동작을 테스트한다."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from graph_schema.loader import load_graph_schema
from graph_schema.models import GraphSchema

PROJECT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "schema" / "graph_schema.yaml"
)


def test_load_graph_schema_returns_validated_model(tmp_path: Path) -> None:
    """정상 YAML 파일을 검증된 그래프 스키마 모델로 반환한다."""
    schema_path = tmp_path / "graph_schema.yaml"
    schema_path.write_text(
        """
nodes:
  Supplier:
    properties:
      name: {type: STRING}
  Product:
    properties:
      productId: {type: INTEGER}
relationships:
  SUPPLIES:
    from: Supplier
    to: Product
    properties: {}
""".lstrip(),
        encoding="utf-8",
    )

    schema = load_graph_schema(schema_path)

    assert isinstance(schema, GraphSchema)
    assert schema.nodes["Product"].properties["productId"].data_type == "INTEGER"
    assert schema.relationships["SUPPLIES"].from_node == "Supplier"
    assert schema.relationships["SUPPLIES"].to_node == "Product"


def test_load_graph_schema_loads_project_schema() -> None:
    """프로젝트의 기준 YAML 파일을 그래프 스키마 모델로 읽는다."""
    schema = load_graph_schema(PROJECT_SCHEMA_PATH)

    assert schema.nodes["Product"].properties["name"].data_type == "STRING"
    assert schema.relationships["SUPPLIES"].from_node == "Supplier"
    assert schema.relationships["SUPPLIES"].to_node == "Product"


def test_load_graph_schema_raises_when_file_does_not_exist(tmp_path: Path) -> None:
    """지정한 YAML 파일이 없으면 FileNotFoundError를 그대로 전달한다."""
    missing_schema_path = tmp_path / "missing_schema.yaml"

    with pytest.raises(FileNotFoundError):
        load_graph_schema(missing_schema_path)


def test_load_graph_schema_raises_for_malformed_yaml(tmp_path: Path) -> None:
    """YAML 문법이 잘못된 파일은 YAMLError를 그대로 전달한다."""
    malformed_yaml_path = tmp_path / "malformed.yaml"
    malformed_yaml_path.write_text("nodes: [", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_graph_schema(malformed_yaml_path)


@pytest.mark.parametrize(
    "yaml_content",
    [
        pytest.param("", id="empty-document"),
        pytest.param("- Product\n", id="non-mapping-root"),
        pytest.param(
            "nodes: {}\nrelationships: {}\n",
            id="empty-nodes",
        ),
    ],
)
def test_load_graph_schema_rejects_schema_with_invalid_structure(
    tmp_path: Path,
    yaml_content: str,
) -> None:
    """YAML 문법은 유효하지만 입력 구조가 잘못되면 검증 오류를 전달한다."""
    invalid_schema_path = tmp_path / "invalid_schema_structure.yaml"
    invalid_schema_path.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(ValidationError):
        load_graph_schema(invalid_schema_path)
