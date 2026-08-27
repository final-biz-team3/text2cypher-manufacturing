"""LLM이 생성한 Cypher를 실행 직전에 파싱해 쓰기 절·미허가 Label/RelationshipType을
차단한다. 성숙한 Cypher 파서가 없어 키워드/토큰 정규식 매칭으로 구현하고, Neo4j
reader 계정(execute_cypher 쪽)과 이중 방어를 이룬다. 스키마 화이트리스트는
schema/graph_schema.yaml(GraphSchema)을 그대로 재사용한다."""

import re
from collections.abc import Callable

from agents.cypher.schema.models import GraphSchema
from orchestrator.guards.result import GuardResult

_FORBIDDEN_KEYWORDS = (
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "DETACH DELETE",
    "REMOVE",
    "DROP",
    "LOAD CSV",
    "FOREACH",
    "CALL",
)
_FORBIDDEN_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(keyword) for keyword in _FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_NODE_LABEL_PATTERN = re.compile(r"\(\s*\w*\s*:\s*(\w+)")
_RELATIONSHIP_TYPE_PATTERN = re.compile(r"\[\s*\w*\s*:\s*(\w+)")


def make_cypher_guard(graph_schema: GraphSchema) -> Callable[[str], GuardResult]:
    """graph_schema로 초기화된 쿼리 가드 함수를 만든다."""
    allowed_labels = set(graph_schema.nodes)
    allowed_relationship_types = set(graph_schema.relationships)

    def guard(cypher: str) -> GuardResult:
        match = _FORBIDDEN_PATTERN.search(cypher)
        if match:
            return GuardResult(
                False, "WRITE_KEYWORD_DETECTED", f"쓰기 키워드 감지: {match.group(1)}"
            )

        used_labels = set(_NODE_LABEL_PATTERN.findall(cypher))
        unknown_labels = used_labels - allowed_labels

        used_relationship_types = set(_RELATIONSHIP_TYPE_PATTERN.findall(cypher))
        unknown_relationship_types = (
            used_relationship_types - allowed_relationship_types
        )

        unknown = unknown_labels | unknown_relationship_types
        if unknown:
            return GuardResult(
                False,
                "UNKNOWN_LABEL_OR_RELATIONSHIP",
                f"스키마에 없는 Label/RelationshipType: {', '.join(sorted(unknown))}",
            )

        return GuardResult(True)

    return guard
