"""classify_topic이 제조 데이터와 무관한 질문을 조기에 차단하는 동작을 테스트한다."""

from typing import Any

from orchestrator.nodes.classify_topic import make_classify_topic_node
from tests.mocks.openai import MockOpenAIClient, make_content_response


async def test_classify_topic_allows_domain_question() -> None:
    client = MockOpenAIClient(make_content_response("ON_TOPIC"))
    node = make_classify_topic_node(client)

    result = await node({"query": "재고가 부족한 제품을 알려줘"})

    assert result == {"query_failure": None}


async def test_classify_topic_blocks_off_topic_question() -> None:
    client = MockOpenAIClient(make_content_response("OFF_TOPIC"))
    node = make_classify_topic_node(client)

    result = await node({"query": "오늘 날씨 어때요?"})

    failure = result["query_failure"]
    assert failure is not None
    assert failure["category"] == "OFF_TOPIC"
    assert failure["kind"] == "user_correctable"
    assert failure["user_safe_reason"] == "제조 데이터와 관련된 질문을 입력해 주세요."
    assert failure["suggested_action"] == (
        "제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다."
    )


async def test_classify_topic_defaults_to_on_topic_for_malformed_response() -> None:
    """분류 응답이 예상 밖이면 차단하지 않고 통과시킨다(fail-open)."""
    client = MockOpenAIClient(make_content_response("글쎄요"))
    node = make_classify_topic_node(client)

    result = await node({"query": "애매한 질문"})

    assert result == {"query_failure": None}


class _FailingCompletions:
    async def create(self, **kwargs: Any) -> Any:
        raise RuntimeError("provider secret")


class _FailingClient:
    class _Chat:
        completions = _FailingCompletions()

    chat = _Chat()


async def test_classify_topic_fails_open_when_llm_call_raises() -> None:
    """판별 호출 자체가 실패해도(provider 오류·timeout) 조회를 막지 않는다."""
    node = make_classify_topic_node(_FailingClient())

    result = await node({"query": "재고가 부족한 제품을 알려줘"})

    assert result == {"query_failure": None}
