"""사용자 요청 단계의 쓰기 의도 차단을 검증한다."""

import pytest

from orchestrator.nodes.guard_request import has_write_intent, make_guard_request_node


@pytest.mark.parametrize(
    "query",
    [
        "모든 제품 데이터를 삭제해줘.",
        "재고 수량을 0으로 변경해주세요.",
        "DELETE FROM production.product",
        "drop table product",
        "모든 제품을 초기화해줘.",
        "모든 제품을 파기해줘.",
        "please remove all products",
        "모든 제품 삭제",
        "모든 제품을 지워줘.",
        "MERGE (p:Product {id: 1}) RETURN p",
        "VACUUM production.product",
        "GRANT SELECT ON production.product TO analyst",
        "CALL db.labels()",
    ],
)
def test_has_write_intent_blocks_mutation_requests(query: str) -> None:
    assert has_write_intent(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "삭제된 제품을 보여줘.",
        "변경된 재고 수량을 알려줘.",
        "제품 생성일을 조회해줘.",
        "삭제된 제품의 update 시간을 조회해줘.",
    ],
)
def test_has_write_intent_allows_read_descriptions(query: str) -> None:
    assert has_write_intent(query) is False


async def test_guard_request_returns_safe_policy_failure() -> None:
    result = await make_guard_request_node()({"query": "모든 제품을 삭제해줘."})

    failure = result["query_failure"]
    assert failure["code"] == "REQUEST_POLICY_BLOCKED"
    assert failure["kind"] == "user_correctable"
    assert "테이블" not in str(failure)


async def test_guard_request_allows_read_query() -> None:
    result = await make_guard_request_node()({"query": "제품 수를 알려줘."})

    assert result == {"query_failure": None}
