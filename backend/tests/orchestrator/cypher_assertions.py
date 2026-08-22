"""생성된 Cypher가 BOM 경로 중복 방지 계약을 표현하는지 검사한다."""

import re


def _normalized(query: str) -> str:
    unquoted = query.lower().replace('"', "").replace("`", "")
    return " ".join(unquoted.split())


def _where_region(query: str) -> str:
    """첫 WHERE부터 RETURN 전까지의 조건 영역을 반환한다."""
    start = query.find("where ")
    if start < 0:
        return ""

    end = query.find(" return ", start)
    return query[start:] if end < 0 else query[start:end]


def _product_id_list_names(query: str) -> set[str]:
    """경로 노드의 productId로 만든 목록 변수명을 찾는다."""
    compact = re.sub(r"\s+", "", query)
    pattern = re.compile(
        r"\[(?P<node>[a-z_]\w*)innodes\([^)]+\)\|"
        r"(?P=node)\.productid\]as(?P<name>[a-z_]\w*?)"
        r"(?=,|where|return|order|$)"
    )
    return {match.group("name") for match in pattern.finditer(compact)}


def _uses_product_id_list_guard(where: str, list_name: str) -> bool:
    compact = re.sub(r"\s+", "", where)
    escaped_name = re.escape(list_name)

    compares_set_size = bool(
        re.search(
            rf"size\({escaped_name}\)=size\(apoc\.coll\.toset\({escaped_name}\)\)|"
            rf"size\(apoc\.coll\.toset\({escaped_name}\)\)=size\({escaped_name}\)",
            compact,
        )
    )
    excludes_prior_value = (
        f"not{list_name}[" in compact and f"in{list_name}[" in compact
    )
    indexed_values = re.findall(rf"{escaped_name}\[[^]]+\]", compact)
    compares_indexed_values = len(indexed_values) >= 2 and (
        "<>" in compact or "single(" in compact
    )
    return compares_set_size or excludes_prior_value or compares_indexed_values


def has_product_id_path_uniqueness_guard(query: str) -> bool:
    """WHERE에 경로 productId 중복 방지 구조가 있는지 확인한다."""
    normalized = _normalized(query)
    where = _where_region(normalized)
    if not where or "productid" not in normalized:
        return False

    checks_nodes_directly = (
        "nodes(" in where
        and ".productid" in where
        and ("single(" in where or "size([" in where)
    )
    if checks_nodes_directly:
        return True

    return any(
        _uses_product_id_list_guard(where, list_name)
        for list_name in _product_id_list_names(normalized)
    )
