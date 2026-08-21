"""노드/관계 스펙 테이블의 형태를 검증한다 - 실제 SQL/Cypher 실행은 통합
검증(로컬 docker)에서 확인하고, 여기서는 스펙 개수·필수 필드만 확인한다."""

from structured_mvp_spec import NODE_SPECS, RELATIONSHIP_SPECS


def test_six_node_specs_defined() -> None:
    assert len(NODE_SPECS) == 6
    labels = {spec.label for spec in NODE_SPECS}
    assert labels == {
        "Product",
        "Supplier",
        "WorkOrder",
        "RoutingOperation",
        "Location",
        "ScrapReason",
    }


def test_six_relationship_specs_defined() -> None:
    assert len(RELATIONSHIP_SPECS) == 6
    types_ = {spec.rel_type for spec in RELATIONSHIP_SPECS}
    assert types_ == {
        "SUPPLIES",
        "REQUIRES_COMPONENT",
        "PRODUCES",
        "HAS_OPERATION",
        "PERFORMED_AT",
        "SCRAPPED_DUE_TO",
    }


def test_every_node_spec_has_extract_sql_and_merge_cypher() -> None:
    for spec in NODE_SPECS:
        assert spec.extract_sql.strip().upper().startswith("SELECT")
        assert "MERGE" in spec.merge_cypher
        assert spec.unique_key in spec.merge_cypher


def test_every_relationship_spec_has_match_and_merge() -> None:
    for spec in RELATIONSHIP_SPECS:
        assert spec.extract_sql.strip().upper().startswith("SELECT")
        assert spec.merge_cypher.count("MATCH") == 2
        assert "MERGE" in spec.merge_cypher
