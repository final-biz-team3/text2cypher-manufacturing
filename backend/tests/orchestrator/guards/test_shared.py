"""orchestrator/guards와 evaluation/safety.py가 금지 키워드 목록을 실제로
공유하는지 검증한다. 과거 이 목록이 한쪽에만 반영돼(EXECUTE/ANALYZE 등)
어긋난 적이 있어, 같은 객체를 참조하는지 자체를 회귀 테스트로 고정해둔다."""

from evaluation import safety
from orchestrator.guards import shared


def test_evaluation_safety_reuses_shared_sql_write_keywords() -> None:
    assert safety.SQL_WRITE_KEYWORDS is shared.SQL_WRITE_KEYWORDS


def test_evaluation_safety_reuses_shared_cypher_write_keywords() -> None:
    assert safety.CYPHER_WRITE_KEYWORDS is shared.CYPHER_WRITE_KEYWORDS


def test_evaluation_safety_reuses_shared_mask_query_text() -> None:
    assert safety.mask_query_text is shared.mask_query_text
