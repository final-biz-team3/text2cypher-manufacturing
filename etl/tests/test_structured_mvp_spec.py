"""노드/관계 스펙 테이블의 형태를 검증한다 - 실제 SQL/Cypher 실행은 통합
검증(로컬 docker)에서 확인하고, 여기서는 스펙 개수·필수 필드만 확인한다."""

from structured_mvp_spec import NODE_SPECS, RELATIONSHIP_SPECS

NODE_LABELS = {spec.label for spec in NODE_SPECS}


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


def test_relationship_endpoint_labels_are_known_node_labels() -> None:
    # from_label/to_label이 NODE_SPECS에 없는 라벨이면 참조 무결성 검사가
    # node_id_sets[label]에서 KeyError로 터진다 - 스펙 단계에서 막는다.
    for spec in RELATIONSHIP_SPECS:
        assert spec.from_label in NODE_LABELS
        assert spec.to_label in NODE_LABELS


def test_relationship_endpoint_keys_are_extract_sql_aliases() -> None:
    # from_key/to_key는 추출 행의 컬럼명이어야 무결성 검사가 row[key]로 읽을 수
    # 있다 - extract_sql이 그 이름으로 별칭을 만드는지 확인한다.
    for spec in RELATIONSHIP_SPECS:
        assert f'AS "{spec.from_key}"' in spec.extract_sql
        assert f'AS "{spec.to_key}"' in spec.extract_sql
