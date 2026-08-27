"""Cypher 쿼리 가드가 쓰기 절과 미허가 Label/RelationshipType을 차단하는지 검증한다."""

from agents.cypher.schema.models import GraphSchema
from orchestrator.guards.cypher_guard import make_cypher_guard

_SCHEMA = GraphSchema.model_validate(
    {
        "nodes": {
            "Product": {"properties": {"productId": {"type": "INTEGER"}}},
            "Supplier": {"properties": {"supplierId": {"type": "INTEGER"}}},
        },
        "relationships": {
            "SUPPLIES": {
                "from": "Supplier",
                "to": "Product",
                "properties": {},
            },
        },
    }
)


def test_cypher_guard_allows_plain_match() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product)<-[:SUPPLIES]-(s:Supplier) RETURN p, s")

    assert result.allowed is True


def test_cypher_guard_blocks_create() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("CREATE (p:Product {productId: 1}) RETURN p")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_cypher_guard_blocks_delete_and_detach_delete() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product) DETACH DELETE p")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"


def test_cypher_guard_blocks_unknown_label() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (u:User) RETURN u")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_blocks_unknown_relationship_type() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product)-[:OWNS]->(s:Supplier) RETURN p, s")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_does_not_false_positive_on_property_named_set() -> None:
    """SET이라는 단어가 속성명 등 다른 문맥에 있어도(단어 경계 밖) 오탐하지 않는다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product) WHERE p.name = 'Toolset' RETURN p")

    assert result.allowed is True


def test_cypher_guard_does_not_false_positive_on_write_keyword_in_string_literal() -> (
    None
):
    """문자열 리터럴 안의 쓰기 키워드는 마스킹 후 검사해 오탐하지 않는다 -
    코드 리뷰로 발견된 오탐(문자열 값이 CREATE/SET 등이면 매번 재시도 3회 소진 후
    실패했음)."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product) WHERE p.name = 'CREATE' RETURN p")

    assert result.allowed is True


def test_cypher_guard_blocks_multi_label_chain_bypass() -> None:
    """다중 레이블 체이닝(:Product:Secret)에서 두 번째 이후 레이블은 예전
    정규식이 못 봤다 - 코드 리뷰로 발견된 우회."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (n:Product:Secret) RETURN n")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"
    assert "Secret" in (result.reason_detail or "")


def test_cypher_guard_blocks_backtick_quoted_label_bypass() -> None:
    """백틱으로 감싼 레이블도 예전 정규식(\\w+ 전제)이 못 봤다 - 코드 리뷰로
    발견된 우회."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (n:`Secret`) RETURN n")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_blocks_where_clause_label_predicate_bypass() -> None:
    """WHERE절의 레이블 predicate(n:Secret)는 노드 패턴(...) 밖이라 예전
    정규식이 못 봤다 - 코드 리뷰로 발견된 우회. reader 계정이 다른 Label도
    읽을 수 있어 실제 미허가 데이터 조회로 이어질 수 있었다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (n:Product) WHERE n:Secret RETURN n")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_allows_multi_label_when_all_whitelisted() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (n:Product:Supplier) RETURN n")

    assert result.allowed is True


def test_cypher_guard_does_not_false_positive_on_property_map_colon() -> None:
    """'{key: value}' 맵 리터럴의 콜론은 레이블이 아니므로 오탐하면 안 된다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (p:Product {productId: 1}) RETURN p")

    assert result.allowed is True


def test_cypher_guard_blocks_load_csv_split_across_whitespace() -> None:
    """'LOAD CSV'가 리터럴 공백 1칸으로만 매치되던 예전 정규식은 개행 등으로
    쪼개면 통과됐다 - 코드 리뷰로 발견된 우회(임의 파일 읽기/SSRF로 이어질 수 있음).
    이제는 LOAD 단어 자체를 막아 우회 불가능하다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("LOAD\nCSV FROM 'file:///etc/passwd' AS row RETURN row")

    assert result.allowed is False
    assert result.reason_code == "WRITE_KEYWORD_DETECTED"
