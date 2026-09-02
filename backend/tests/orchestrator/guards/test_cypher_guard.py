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


def test_cypher_guard_blocks_unknown_label_hidden_behind_ampersand_conjunction() -> (
    None
):
    """Neo4j 5 label-expression의 '&'(AND) 결합은 '|'만 처리하던 예전 코드가
    못 봤다 - 코드 리뷰로 발견된 우회. (:Product&Secret)에서 Secret이
    화이트리스트에 없어도 Product만 보고 통과시켰었다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (n:Product&Secret) RETURN n")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"
    assert "Secret" in (result.reason_detail or "")


def test_cypher_guard_allows_ampersand_conjunction_when_all_whitelisted() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (n:Product&Supplier) RETURN n")

    assert result.allowed is True


def test_cypher_guard_blocks_unsupported_label_expression_negation() -> None:
    """'!'(부정) 같은 아직 지원하지 않는 label-expression 연산자는 조용히
    통과시키지 않고 fail-closed 한다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("MATCH (n:!Product) RETURN n")

    assert result.allowed is False
    assert result.reason_code == "UNRECOGNIZED_LABEL_SYNTAX"


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


def test_cypher_guard_blocks_unknown_label_hidden_inside_exists_subquery() -> None:
    """EXISTS { ... }(Neo4j 5 서브쿼리)는 '{...}' 맵 리터럴과 같은 중괄호를 쓰지만
    내용물이 맵이 아니라 중첩 쿼리다. 이걸 맵으로 취급해 안의 콜론을 통째로
    건너뛰면 그 안의 미허가 Label/RelationshipType이 검사를 완전히 우회한다 -
    y-dev에 병합된 실제 gold 쿼리(HQ06/HQ09)의 EXISTS{} 패턴을 검증하다가
    발견됨."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard(
        "MATCH (p:Product) WHERE EXISTS { " "MATCH (p)-[:OWNS]->(s:Secret) } RETURN p"
    )

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"
    assert "Secret" in (result.reason_detail or "")


def test_cypher_guard_blocks_unknown_label_hidden_inside_count_subquery() -> None:
    guard = make_cypher_guard(_SCHEMA)

    result = guard(
        "MATCH (p:Product) WHERE COUNT { MATCH (p)-[:OWNS]->(s:Secret) } > 0 "
        "RETURN p"
    )

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_blocks_unknown_label_hidden_inside_collect_subquery() -> None:
    """COLLECT { ... }도 EXISTS/COUNT와 같은 Neo4j 5 서브쿼리 표현식 계열이라
    같은 우회가 가능했다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard(
        "MATCH (p:Product) RETURN COLLECT { "
        "MATCH (p)-[:OWNS]->(s:Secret) RETURN s } AS r"
    )

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"


def test_cypher_guard_allows_exists_subquery_with_only_whitelisted_names() -> None:
    """실제 gold 쿼리(HQ06/HQ09)와 같은 형태 - EXISTS{} 안에서 화이트리스트
    Label/RelationshipType만 쓰면 정상 허용된다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard(
        "MATCH (p:Product) WHERE NOT EXISTS { "
        "MATCH (p)<-[:SUPPLIES]-(s:Supplier) } RETURN p"
    )

    assert result.allowed is True


def test_cypher_guard_still_suppresses_real_map_literal_inside_exists_block() -> None:
    """EXISTS{} 블록 안에 진짜 맵 리터럴이 다시 나와도(중첩) 그 맵의 콜론은
    여전히 레이블로 오인하지 않는다."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard(
        "MATCH (p:Product) WHERE EXISTS { "
        "MATCH (n:Product {productId: 1}) } RETURN p"
    )

    assert result.allowed is True


def test_cypher_guard_blocks_unknown_label_in_exists_nested_inside_map_value() -> None:
    """맵 리터럴의 값 위치에 EXISTS{}가 중첩돼도(예: RETURN {result: EXISTS
    {...}}) 그 안의 레이블은 계속 스캔해야 한다 - 스택 top만 보고 판단하는지
    확인."""
    guard = make_cypher_guard(_SCHEMA)

    result = guard("RETURN {result: EXISTS { MATCH (n:Secret) RETURN n }} AS r")

    assert result.allowed is False
    assert result.reason_code == "UNKNOWN_LABEL_OR_RELATIONSHIP"
