"""이전 생성기도 현재 브랜치의 스키마와 읽기 전용 가드로 검증한다."""

from pathlib import Path
from typing import Any

from agents.cypher.schema.loader import load_graph_schema
from agents.sql.schema.loader import load_sql_schema
from orchestrator.guards.cypher_guard import make_cypher_guard as current_cypher_guard
from orchestrator.guards.sql_guard import make_sql_guard as current_sql_guard

_ROOT = Path(__file__).resolve().parents[2]


def make_sql_guard(_legacy_schema: Any) -> Any:
    return current_sql_guard(load_sql_schema(_ROOT / "schema/sql_schema.yaml"))


def make_cypher_guard(_legacy_schema: Any) -> Any:
    return current_cypher_guard(load_graph_schema(_ROOT / "schema/graph_schema.yaml"))
