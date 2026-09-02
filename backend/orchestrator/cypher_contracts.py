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
_PATH_VARIABLE_BINDING = re.compile(r"(?is)^\s*([A-Za-z_]\w*)\s*=\s*\(")
_PATH_ONLY_FUNCTION = re.compile(
    r"(?i)\b(?:relationships|nodes|length)\s*\(\s*([A-Za-z_]\w*)\s*\)"
)
_WITH_VARIABLE_PROJECTION = re.compile(
    r"(?is)^\s*([A-Za-z_]\w*)(?:\s+AS\s+([A-Za-z_]\w*))?\s*$"
)


def _find_clause_boundary(masked: str, start: int) -> int:
    """Find the next clause keyword at the current syntactic nesting depth."""
    round_depth = 0
    square_depth = 0
    curly_depth = 0
    index = start
    while index < len(masked):
        if round_depth == 0 and square_depth == 0 and curly_depth == 0:
            if _CLAUSE_BOUNDARY.match(masked, index) is not None:
                return index

        char = masked[index]
        if char == "(":
            round_depth += 1
        elif char == ")":
            if round_depth == 0:
                return index
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            if square_depth == 0:
                return index
            square_depth -= 1
        elif char == "{":
            curly_depth += 1
        elif char == "}":
            if curly_depth == 0:
                return index
            curly_depth -= 1
        index += 1
    return len(masked)


def _match_pattern_regions(cypher: str) -> tuple[str, ...]:
    """Return only the graph-pattern region of each MATCH clause."""
    masked = mask_query_text(cypher)
    regions: list[str] = []
    for match in _MATCH_START.finditer(masked):
        end = _find_clause_boundary(masked, match.end())
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


def _top_level_clause_regions(masked: str) -> tuple[tuple[str, str], ...]:
    """Return top-level clause names and their bodies in source order."""
    clauses: list[tuple[str, int, int]] = []
    round_depth = 0
    square_depth = 0
    curly_depth = 0
    index = 0
    while index < len(masked):
        if round_depth == 0 and square_depth == 0 and curly_depth == 0:
            clause = _CLAUSE_BOUNDARY.match(masked, index)
            if clause is not None:
                name = " ".join(clause.group(0).upper().split())
                clauses.append((name, clause.start(), clause.end()))
                index = clause.end()
                continue

        char = masked[index]
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
        index += 1

    regions: list[tuple[str, str]] = []
    for clause_index, (name, _, body_start) in enumerate(clauses):
        body_end = (
            len(masked)
            if clause_index + 1 == len(clauses)
            else clauses[clause_index + 1][1]
        )
        regions.append((name, masked[body_start:body_end]))
    return tuple(regions)


def _project_relationship_lists(
    projection: str, relationship_lists: set[str]
) -> set[str]:
    """Apply a WITH projection to the known relationship-list variables."""
    projection = re.sub(r"(?is)^\s*DISTINCT\b", "", projection, count=1)
    projected: set[str] = set()
    for item in _split_top_level_patterns(projection):
        if item.strip() == "*":
            projected.update(relationship_lists)
            continue
        variable = _WITH_VARIABLE_PROJECTION.fullmatch(item)
        if variable is None:
            continue
        source, alias = variable.groups()
        if source in relationship_lists:
            projected.add(alias or source)
    return projected


def _uses_relationship_list_as_path(region: str, relationship_lists: set[str]) -> bool:
    return any(
        function.group(1) in relationship_lists
        for function in _PATH_ONLY_FUNCTION.finditer(region)
    )


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
    relationship_lists: set[str] = set()
    for clause, region in _top_level_clause_regions(masked):
        if clause in {"MATCH", "OPTIONAL MATCH"}:
            path_variables = {
                path.group(1)
                for pattern in _split_top_level_patterns(region)
                if (path := _PATH_VARIABLE_BINDING.match(pattern)) is not None
            }
            relationship_lists.difference_update(path_variables)
            relationship_lists.update(
                _VARIABLE_LENGTH_RELATIONSHIP_BINDING.findall(region)
            )
        elif clause == "WITH":
            if _uses_relationship_list_as_path(region, relationship_lists):
                return True
            relationship_lists = _project_relationship_lists(region, relationship_lists)
            continue
        elif clause == "UNION":
            relationship_lists.clear()

        if _uses_relationship_list_as_path(region, relationship_lists):
            return True
    return False
