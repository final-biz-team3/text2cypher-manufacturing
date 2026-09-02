"""Generated Cypher structure contracts that are safe to check before execution."""

import re

from orchestrator.guards.shared import mask_query_text

_CLAUSE_BOUNDARY = re.compile(
    r"(?i)\b(?:OPTIONAL\s+MATCH|MATCH|WHERE|WITH|RETURN|UNWIND|ORDER\s+BY|"
    r"SKIP|LIMIT|UNION)\b"
)
_MATCH_START = re.compile(r"(?i)\b(?:OPTIONAL\s+MATCH|MATCH)\b")
_NODE_VARIABLE = re.compile(r"\(\s*([A-Za-z_]\w*)\b")
_VARIABLE_BOM_RELATIONSHIP = re.compile(
    r"(?is)\[[^\]]*\bREQUIRES_COMPONENT\b[^\]]*\*[^\]]*\]"
)
_VARIABLE_LENGTH_RELATIONSHIP_BINDING = re.compile(
    r"(?is)\[\s*([A-Za-z_]\w*)\s*(?::[^\]]*)?\*[^\]]*\]"
)
_PATH_ONLY_FUNCTION = re.compile(
    r"(?i)\b(?:relationships|nodes|length)\s*\(\s*([A-Za-z_]\w*)\s*\)"
)


def _match_pattern_regions(cypher: str) -> tuple[str, ...]:
    """Return only the graph-pattern region of each MATCH clause."""
    masked = mask_query_text(cypher)
    regions: list[str] = []
    for match in _MATCH_START.finditer(masked):
        boundary = _CLAUSE_BOUNDARY.search(masked, match.end())
        end = len(masked) if boundary is None else boundary.start()
        regions.append(masked[match.end() : end])
    return tuple(regions)


def _split_top_level_patterns(region: str) -> tuple[str, ...]:
    """Split comma-separated MATCH patterns without splitting nested expressions."""
    patterns: list[str] = []
    start = 0
    round_depth = 0
    square_depth = 0
    curly_depth = 0
    for index, char in enumerate(region):
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth = max(0, round_depth - 1)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "{":
            curly_depth += 1
        elif char == "}":
            curly_depth = max(0, curly_depth - 1)
        elif (
            char == "," and round_depth == 0 and square_depth == 0 and curly_depth == 0
        ):
            patterns.append(region[start:index])
            start = index + 1
    patterns.append(region[start:])
    return tuple(pattern for pattern in patterns if pattern.strip())


def _variable_bom_endpoints(pattern: str) -> tuple[str, str] | None:
    if _VARIABLE_BOM_RELATIONSHIP.search(pattern) is None:
        return None
    node_variables = _NODE_VARIABLE.findall(pattern)
    if len(node_variables) < 2:
        return None
    return node_variables[0], node_variables[-1]


def has_coupled_independent_bom_paths(cypher: str) -> bool:
    """Detect independent BOM traversals coupled in one Neo4j graph pattern.

    Neo4j 5 applies relationship uniqueness across comma-separated path patterns in
    one MATCH result. Two variable-length BOM paths that converge on the same end
    node (or fan out from the same start node) can therefore suppress valid rows
    when the traversals share relationships. Chained paths, whose shared endpoint
    is the end of one path and the start of the next, are intentionally allowed.
    """
    for region in _match_pattern_regions(cypher):
        endpoints = [
            endpoints
            for pattern in _split_top_level_patterns(region)
            if (endpoints := _variable_bom_endpoints(pattern)) is not None
        ]
        for index, (left_start, left_end) in enumerate(endpoints):
            for right_start, right_end in endpoints[index + 1 :]:
                same_start = left_start == right_start and left_end != right_end
                same_end = left_end == right_end and left_start != right_start
                if same_start or same_end:
                    return True
    return False


def has_relationship_list_used_as_path(cypher: str) -> bool:
    """Detect a variable-length relationship list passed to a path-only function.

    In ``()-[rels:TYPE*]->()`` Neo4j binds ``rels`` to a relationship list, not a
    Path. Functions such as relationships(), nodes(), and length() require the
    full path variable created by ``path = ()-[:TYPE*]->()``.
    """
    masked = mask_query_text(cypher)
    relationship_lists = set(_VARIABLE_LENGTH_RELATIONSHIP_BINDING.findall(masked))
    return any(
        argument in relationship_lists
        for argument in _PATH_ONLY_FUNCTION.findall(masked)
    )
