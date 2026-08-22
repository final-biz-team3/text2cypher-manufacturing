"""생성된 Cypher의 BOM 경로 계약을 검사하는 테스트 헬퍼."""

import re

_CLAUSE_STARTS = (
    "call",
    "match",
    "optionalmatch",
    "return",
    "unwind",
    "with",
)


def _compact(query: str) -> str:
    unquoted = query.lower().replace('"', "").replace("`", "")
    return re.sub(r"\s+", "", unquoted)


def _is_where_expression(query: str, expression_start: int) -> bool:
    """표현식이 가장 가까운 WHERE 절 안에 있는지 확인한다."""
    last_where = query.rfind("where", 0, expression_start)
    last_clause = max(
        (query.rfind(clause, 0, expression_start) for clause in _CLAUSE_STARTS),
        default=-1,
    )
    return last_where > last_clause


def _product_id_list_names(query: str) -> set[str]:
    """경로 노드의 productId로 만든 목록 변수명을 찾는다."""
    pattern = re.compile(
        r"\[(?P<node>[a-z_]\w*)innodes\([^)]+\)\|"
        r"(?P=node)\.productid\]as(?P<name>[a-z_]\w*?)"
        r"(?=,|where|return|order|$)"
    )
    return {match.group("name") for match in pattern.finditer(query)}


def _uses_one_node_per_product_id(query: str) -> bool:
    single_pattern = re.compile(
        r"all\((?P<node>[a-z_]\w*)innodes\((?P<path>[a-z_]\w*)\)where"
        r"single\((?P<other>[a-z_]\w*)innodes\((?P=path)\)where(?:"
        r"(?P=other)\.productid=(?P=node)\.productid|"
        r"(?P=node)\.productid=(?P=other)\.productid)\)\)"
    )
    count_pattern = re.compile(
        r"all\((?P<node>[a-z_]\w*)innodes\((?P<path>[a-z_]\w*)\)where"
        r"size\(\[(?P<other>[a-z_]\w*)innodes\((?P=path)\)where(?:"
        r"(?P=other)\.productid=(?P=node)\.productid|"
        r"(?P=node)\.productid=(?P=other)\.productid)\]\)=1\)"
    )
    return any(
        _is_where_expression(query, match.start())
        for pattern in (single_pattern, count_pattern)
        for match in pattern.finditer(query)
    )


def _uses_single_node_index_per_product_id(query: str) -> bool:
    pattern = re.compile(
        r"all\((?P<index>[a-z_]\w*)inrange\(0,size\(nodes\("
        r"(?P<path>[a-z_]\w*)\)\)-1\)wheresingle\((?P<other>[a-z_]\w*)"
        r"inrange\(0,size\(nodes\((?P=path)\)\)-1\)where(?:"
        r"nodes\((?P=path)\)\[(?P=other)\]\.productid="
        r"nodes\((?P=path)\)\[(?P=index)\]\.productid|"
        r"nodes\((?P=path)\)\[(?P=index)\]\.productid="
        r"nodes\((?P=path)\)\[(?P=other)\]\.productid)\)\)"
    )
    return any(
        _is_where_expression(query, match.start()) for match in pattern.finditer(query)
    )


def _compares_with_unique_product_ids(query: str, list_name: str) -> bool:
    escaped_name = re.escape(list_name)
    direct = rf"size\({escaped_name}\)=" rf"size\(apoc\.coll\.toset\({escaped_name}\)\)"
    reverse = (
        rf"size\(apoc\.coll\.toset\({escaped_name}\)\)=" rf"size\({escaped_name}\)"
    )
    pattern = re.compile(rf"(?:{direct}|{reverse})")
    return any(
        _is_where_expression(query, match.start()) for match in pattern.finditer(query)
    )


def _excludes_prior_product_ids(query: str, list_name: str) -> bool:
    escaped_name = re.escape(list_name)
    pattern = re.compile(
        r"all\((?P<index>[a-z_]\w*)inrange\(0,size\("
        rf"{escaped_name}\)-1\)wherenot{escaped_name}\[(?P=index)\]"
        rf"in{escaped_name}\[(?:0)?\.\.(?P=index)\]\)"
    )
    return any(
        _is_where_expression(query, match.start()) for match in pattern.finditer(query)
    )


def _uses_single_index_per_product_id(query: str, list_name: str) -> bool:
    escaped_name = re.escape(list_name)
    pattern = re.compile(
        r"all\((?P<index>[a-z_]\w*)inrange\(0,size\("
        rf"{escaped_name}\)-1\)wheresingle\((?P<other>[a-z_]\w*)inrange\(0,"
        rf"size\({escaped_name}\)-1\)where(?:{escaped_name}\[(?P=other)\]="
        rf"{escaped_name}\[(?P=index)\]|{escaped_name}\[(?P=index)\]="
        rf"{escaped_name}\[(?P=other)\])\)\)"
    )
    return any(
        _is_where_expression(query, match.start()) for match in pattern.finditer(query)
    )


def _compares_product_id_pairs(query: str, list_name: str) -> bool:
    escaped_name = re.escape(list_name)
    later_indices = re.compile(
        r"all\((?P<index>[a-z_]\w*)inrange\(0,size\("
        rf"{escaped_name}\)-1\)whereall\((?P<other>[a-z_]\w*)inrange\("
        rf"(?P=index)\+1,size\({escaped_name}\)-1\)where(?:"
        rf"{escaped_name}\[(?P=index)\]<>{escaped_name}\[(?P=other)\]|"
        rf"{escaped_name}\[(?P=other)\]<>{escaped_name}\[(?P=index)\])\)\)"
    )
    earlier_indices = re.compile(
        r"all\((?P<index>[a-z_]\w*)inrange\(0,size\("
        rf"{escaped_name}\)-1\)whereall\((?P<other>[a-z_]\w*)inrange\(0,"
        rf"(?P=index)-1\)where(?:"
        rf"{escaped_name}\[(?P=index)\]<>{escaped_name}\[(?P=other)\]|"
        rf"{escaped_name}\[(?P=other)\]<>{escaped_name}\[(?P=index)\])\)\)"
    )
    all_indices = re.compile(
        r"all\((?P<index>[a-z_]\w*)inrange\(0,size\("
        rf"{escaped_name}\)-1\)whereall\((?P<other>[a-z_]\w*)inrange\(0,"
        rf"size\({escaped_name}\)-1\)where(?:"
        rf"(?:(?P=index)=(?P=other)|(?P=other)=(?P=index))or(?:"
        rf"{escaped_name}\[(?P=index)\]<>{escaped_name}\[(?P=other)\]|"
        rf"{escaped_name}\[(?P=other)\]<>{escaped_name}\[(?P=index)\])|"
        rf"(?:{escaped_name}\[(?P=index)\]<>{escaped_name}\[(?P=other)\]|"
        rf"{escaped_name}\[(?P=other)\]<>{escaped_name}\[(?P=index)\])or"
        rf"(?:(?P=index)=(?P=other)|(?P=other)=(?P=index)))\)\)"
    )
    return any(
        _is_where_expression(query, match.start())
        for pattern in (later_indices, earlier_indices, all_indices)
        for match in pattern.finditer(query)
    )


def has_product_id_path_uniqueness_predicate(query: str) -> bool:
    """경로의 productId 중복을 막는 WHERE 조건이 있는지 확인한다."""
    compact = _compact(query)
    if _uses_one_node_per_product_id(compact) or _uses_single_node_index_per_product_id(
        compact
    ):
        return True

    return any(
        _compares_with_unique_product_ids(compact, list_name)
        or _excludes_prior_product_ids(compact, list_name)
        or _uses_single_index_per_product_id(compact, list_name)
        or _compares_product_id_pairs(compact, list_name)
        for list_name in _product_id_list_names(compact)
    )
