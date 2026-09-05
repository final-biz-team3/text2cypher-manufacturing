"""선언형 query semantic catalog의 호환성 export를 제공한다."""

from pathlib import Path

from agents.cypher.schema.models import GraphSchema
from agents.sql.schema.models import SqlSchema
from orchestrator.semantic_catalog import (
    AliasSpec,
    EntityRoleSpec,
    IdentityProjection,
    OutputCatalog,
    QuerySemanticCatalog,
    ResultShapeSourceSpec,
    ToolName,
    build_query_semantic_catalog,
    load_manufacturing_ontology,
    load_query_semantic_catalog,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ONTOLOGY_PATH = _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"


def build_output_catalog(
    sql_schema: SqlSchema,
    graph_schema: GraphSchema,
) -> QuerySemanticCatalog:
    """physical schema와 패키지 ontology로 공유 catalog를 구성한다."""
    ontology = load_manufacturing_ontology(DEFAULT_ONTOLOGY_PATH)
    return build_query_semantic_catalog(sql_schema, graph_schema, ontology)


__all__ = [
    "AliasSpec",
    "DEFAULT_ONTOLOGY_PATH",
    "EntityRoleSpec",
    "IdentityProjection",
    "OutputCatalog",
    "QuerySemanticCatalog",
    "ResultShapeSourceSpec",
    "ToolName",
    "build_output_catalog",
    "build_query_semantic_catalog",
    "load_manufacturing_ontology",
    "load_query_semantic_catalog",
]
