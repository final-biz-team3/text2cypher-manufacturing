"""generate_answer의 LLM 호출·안전 분기 계약을 테스트한다."""

import json
from typing import Any, cast

import pytest

import orchestrator.guards.audit as audit_module
from orchestrator.errors import AnswerGenerationError, QueryInfrastructureError
from orchestrator.nodes.generate_answer import (
    generate_failure_answer,
    make_generate_answer_node,
)
from orchestrator.query_failures import make_query_failure
from orchestrator.state import ComposedResult, QueryFailure
from tests.mocks.openai import (
    MockChatCompletion,
    MockOpenAIClient,
    make_content_response,
)


def _composed_result(**overrides: Any) -> ComposedResult:
    result: ComposedResult = {
        "mode": "joined",
        "rows": [{"id": 1, "stock": 10}],
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": 1,
        "truncated": False,
    }
    return cast(ComposedResult, {**result, **overrides})


def _query_failure(**overrides: Any) -> QueryFailure:
    failure = make_query_failure(
        code="QUERY_EXECUTION_FAILED",
        stage="execution",
        category="QUERY_INVALID",
        kind="user_correctable",
        retryable=True,
        user_safe_reason="생성된 조회를 정상적으로 실행하지 못했습니다.",
        suggested_action="조회 조건을 더 구체적으로 지정해 주세요.",
        failed_tool="sql",
    )
    return cast(QueryFailure, {**failure, **overrides})


async def test_generate_answer_uses_only_query_and_composed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "query-model")
    monkeypatch.setenv("ANSWER_MODEL", "answer-model")
    monkeypatch.setenv("ANSWER_MAX_OUTPUT_TOKENS", "900")
    client = MockOpenAIClient(make_content_response("**재고는 10개입니다.**"))
    node = make_generate_answer_node(client)

    result = await node(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(),
            "sql_query": "SECRET SQL",
            "cypher_query": "SECRET CYPHER",
            "sql_result": {"result": [{"secret": "RAW SQL RESULT"}]},
            "graph_result": {"result": [{"secret": "RAW GRAPH RESULT"}]},
        }
    )

    assert result == {"final_answer": "**재고는 10개입니다.**"}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "answer-model"
    assert call["max_completion_tokens"] == 900
    assert [message["role"] for message in call["messages"]] == [
        "developer",
        "user",
    ]
    serialized_messages = str(call["messages"])
    assert "재고를 알려줘" in serialized_messages
    assert '"stock":10' in serialized_messages
    assert "SECRET SQL" not in serialized_messages
    assert "SECRET CYPHER" not in serialized_messages
    assert "RAW SQL RESULT" not in serialized_messages
    assert "RAW GRAPH RESULT" not in serialized_messages


async def test_generate_answer_falls_back_to_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "shared-model")
    monkeypatch.setenv("ANSWER_MODEL", "   ")
    client = MockOpenAIClient(make_content_response("답변"))

    await make_generate_answer_node(client)(
        {"query": "질의", "composed_result": _composed_result()}
    )

    assert client.calls[0]["model"] == "shared-model"


@pytest.mark.parametrize(
    ("empty_reason", "expected"),
    [
        ("NO_DATA", "조회 결과가 없습니다"),
        ("INCONCLUSIVE", "답을 확정할 수 없습니다"),
    ],
)
async def test_generate_answer_uses_deterministic_empty_messages_without_llm(
    empty_reason: str,
    expected: str,
) -> None:
    client = MockOpenAIClient()
    node = make_generate_answer_node(client)

    result = await node(
        {
            "query": "질의",
            "composed_result": _composed_result(
                rows=[], empty_reason=empty_reason, total_count=0
            ),
        }
    )

    assert expected in result["final_answer"]
    assert client.calls == []


async def test_generate_answer_hides_internal_composition_error_without_llm() -> None:
    client = MockOpenAIClient()
    internal_error = "sql_followup의 join key가 바인딩 범위를 벗어났습니다."

    result = await make_generate_answer_node(client)(
        {
            "query": "질의",
            "composed_result": _composed_result(
                rows=[], error=internal_error, total_count=0
            ),
        }
    )

    assert internal_error not in result["final_answer"]
    assert "다시 시도" in result["final_answer"]
    assert client.calls == []


async def test_generate_answer_treats_missing_composed_result_as_safe_error() -> None:
    client = MockOpenAIClient()

    result = await make_generate_answer_node(client)({"query": "질의"})

    assert "다시 시도" in result["final_answer"]
    assert client.calls == []


@pytest.mark.parametrize(
    "response",
    [
        MockChatCompletion(choices=[]),
        make_content_response("잘린 답변", finish_reason="length"),
        make_content_response("   "),
    ],
)
async def test_generate_answer_rejects_invalid_llm_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: MockChatCompletion,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(response)

    with pytest.raises(AnswerGenerationError) as exc_info:
        await make_generate_answer_node(client)(
            {"query": "질의", "composed_result": _composed_result()}
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "ANSWER_GENERATION_FAILED"


class _FailingCompletions:
    async def create(self, **kwargs: Any) -> Any:
        raise RuntimeError("provider secret")


class _FailingClient:
    class _Chat:
        completions = _FailingCompletions()

    chat = _Chat()


async def test_generate_answer_wraps_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    with pytest.raises(AnswerGenerationError) as exc_info:
        await make_generate_answer_node(_FailingClient())(
            {"query": "질의", "composed_result": _composed_result()}
        )

    assert "provider secret" not in exc_info.value.message


def test_generate_failure_answer_formats_reason_and_action() -> None:
    """LLM 호출 없이 안전 정보(user_safe_reason + suggested_action)만 이어붙인다."""
    failure = _query_failure(
        user_safe_reason="생성된 조회를 정상적으로 실행하지 못했습니다.",
        suggested_action="조회 조건을 더 구체적으로 지정해 주세요.",
    )

    answer = generate_failure_answer(failure)

    assert answer == (
        "생성된 조회를 정상적으로 실행하지 못했습니다. 조회 조건을 더 구체적으로 지정해 주세요."
    )


def test_generate_failure_answer_rejects_non_user_correctable_kind() -> None:
    failure = _query_failure(kind="infrastructure")

    with pytest.raises(ValueError):
        generate_failure_answer(failure)


async def test_generate_answer_node_naturalizes_user_correctable_failure() -> None:
    """user_correctable 실패는 LLM 호출 없이 안전 정보를 그대로 반환한다."""
    client = MockOpenAIClient()

    result = await make_generate_answer_node(client)(
        {"query": "질의", "query_failure": _query_failure()}
    )

    assert result == {
        "final_answer": (
            "생성된 조회를 정상적으로 실행하지 못했습니다. 조회 조건을 더 구체적으로 지정해 주세요."
        )
    }
    assert len(client.calls) == 0


async def test_generate_answer_node_rejects_infrastructure_without_llm() -> None:
    client = MockOpenAIClient()

    with pytest.raises(QueryInfrastructureError):
        await make_generate_answer_node(client)(
            {
                "query": "질의",
                "query_failure": _query_failure(
                    kind="infrastructure", code="INFRASTRUCTURE_UNAVAILABLE"
                ),
            }
        )

    assert client.calls == []


async def test_generate_answer_rejects_number_not_present_in_evidence() -> None:
    client = MockOpenAIClient(make_content_response("재고는 999입니다."))

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {"query": "재고를 알려줘", "composed_result": _composed_result()}
        )


async def test_generate_answer_does_not_treat_question_number_as_evidence() -> None:
    client = MockOpenAIClient(make_content_response("정가는 0원입니다."))

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {
                "query": "정가가 0원인 제품의 정가를 알려줘",
                "composed_result": _composed_result(
                    rows=[{"productName": "Touring", "listPrice": 2384.07}]
                ),
            }
        )


async def test_generate_answer_keeps_first_row_when_it_exceeds_prompt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSWER_MAX_CHARS", "1")
    client = MockOpenAIClient(make_content_response("재고는 10입니다."))

    result = await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    assert result["final_answer"] == "재고는 10입니다."
    assert len(client.calls) == 1


async def test_generate_answer_uses_internal_failure_message_without_llm() -> None:
    client = MockOpenAIClient()

    result = await make_generate_answer_node(client)(
        {
            "query": "가격이 100 이상인 제품",
            "query_failure": _query_failure(kind="internal"),
        }
    )

    assert "일시적인 문제" in result["final_answer"]
    assert "질문을 바꾸" in result["final_answer"]
    assert client.calls == []


async def test_generate_answer_rejects_unknown_latin_identifier() -> None:
    client = MockOpenAIClient(
        make_content_response("제품 SECRET-PRODUCT의 재고는 10입니다.")
    )

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {"query": "재고를 알려줘", "composed_result": _composed_result()}
        )


@pytest.mark.parametrize("unknown_name", ["브레이크패드", "가상제품"])
async def test_generate_answer_rejects_unknown_korean_entity_name(
    unknown_name: str,
) -> None:
    """ "가상제품"은 "제품"을 포함하지만 그 사실만으로 근거 있다고 보면
    안 된다 - 부분 문자열 완화는 만들어낸 엔티티명까지 통과시켜 PR #53
    리뷰에서 지적됐다. 정확 일치만 허용해야 이 두 이름을 잡는다."""
    client = MockOpenAIClient(
        make_content_response(f"{unknown_name}의 재고는 10입니다.")
    )

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {
                "query": "재고를 알려줘",
                "composed_result": _composed_result(
                    rows=[{"productName": "프레임", "stock": 10}]
                ),
            }
        )


async def test_generate_answer_accepts_grounded_korean_entity_name() -> None:
    client = MockOpenAIClient(make_content_response("프레임의 재고는 10입니다."))

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(
                rows=[{"productName": "프레임", "stock": 10}]
            ),
        }
    )

    assert result["final_answer"] == "프레임의 재고는 10입니다."


async def test_generate_answer_allows_markdown_ordered_list_numbers() -> None:
    client = MockOpenAIClient(
        make_content_response("1. 재고는 10입니다.\n2. 재고는 10입니다.")
    )

    result = await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    assert result["final_answer"].startswith("1. 재고는 10")


async def test_generate_answer_accepts_grounded_korean_scaled_number() -> None:
    client = MockOpenAIClient(make_content_response("재고는 1만입니다."))

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(rows=[{"stock": 10000}]),
        }
    )

    assert result["final_answer"] == "재고는 1만입니다."


async def test_generate_answer_keeps_generic_counter_units() -> None:
    """원본 데이터는 순수 JSON 숫자(예: 10)라 "10건"처럼 숫자+단위 조합이
    문자 그대로 있을 수 없다. "개"/"건"은 수량의 종류(화폐·시간·비율 등)를
    새로 주장하지 않는 일반 분류사라, 근거 대조 없이 그대로 남겨야
    "재고는 10개입니다"가 어색하게 "재고는 10입니다"로 잘리지 않는다."""
    client = MockOpenAIClient(make_content_response("재고는 10건입니다."))

    result = await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    assert result["final_answer"] == "재고는 10건입니다."


async def test_generate_answer_still_strips_ungrounded_specific_units() -> None:
    """ "개"/"건"과 달리 "원"(화폐)처럼 수량의 종류를 새로 주장하는 단위는
    여전히 원본과 대조해, 근거 없으면 단위를 뗀다."""
    client = MockOpenAIClient(make_content_response("재고는 10원입니다."))

    result = await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    assert result["final_answer"] == "재고는 10입니다."


async def test_generate_answer_accepts_korean_only_particle_misread_as_scale_unit() -> (
    None
):
    """ "73만 포함"은 730000(73만)이 아니라 "73개만"(only 73)이라는 뜻일 수
    있다 - 한국어는 배율 단위 "만"과 "~만"(only) 조사가 표기상 구분되지
    않는다. 73이 근거 데이터에 있으면 730000으로 오인식해 거부하지 않는다."""
    client = MockOpenAIClient(
        make_content_response("전체 141건 중 73만 포함되어 있습니다.")
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(
                mode="single",
                rows=[{"id": i, "stock": 10} for i in range(73)],
                total_count=141,
            ),
        }
    )

    assert "73만 포함" in result["final_answer"]


async def test_generate_answer_rejects_scale_claim_misread_as_only_particle() -> None:
    """ "73만 포함"과 달리 "73만입니다"는 "포함" 문맥이 없어 배율 주장
    (73만=730000)으로만 읽힌다. source에는 73만 있으므로 730000은 근거가
    없는 값이라 거부해야 한다 - 문맥 확인 없이 조사로 해석하면 근거 없는
    배율 값이 그대로 통과한다(PR #53 리뷰 코멘트)."""
    client = MockOpenAIClient(make_content_response("재고는 73만입니다."))

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {
                "query": "재고를 알려줘",
                "composed_result": _composed_result(
                    mode="single", rows=[{"id": i, "stock": 10} for i in range(73)]
                ),
            }
        )


async def test_generate_answer_logs_accepted_validation_to_audit_trail(
    tmp_path, monkeypatch
) -> None:
    """모니터링 지표(PR #53 리뷰 권장사항)를 위해 통과 건도 stage/outcome을
    남겨야 사후에 오탐률(거부/전체)을 계산할 수 있다."""
    log_path = tmp_path / "answer_validation_audit.jsonl"
    monkeypatch.setenv("ANSWER_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("ANSWER_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_answer_audit_for_tests()
    client = MockOpenAIClient(make_content_response("재고는 10입니다."))

    await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "stage": "generate_answer",
        "outcome": "accepted",
        "reason": None,
        "detail": None,
    }


async def test_generate_answer_logs_rejected_korean_entity_to_audit_trail(
    tmp_path, monkeypatch
) -> None:
    """근거 없는 한국어 엔티티 거부 시 실패 사유와 실제 토큰을 감사 로그에
    남겨야, 나중에 "가상제품"류 진짜 환각과 "안전재고"류 정당한 스키마
    의역 오탐을 구분해 온톨로지 투자 여부를 데이터로 판단할 수 있다."""
    log_path = tmp_path / "answer_validation_audit.jsonl"
    monkeypatch.setenv("ANSWER_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("ANSWER_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_answer_audit_for_tests()
    client = MockOpenAIClient(make_content_response("가상제품의 재고는 10입니다."))

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {
                "query": "재고를 알려줘",
                "composed_result": _composed_result(
                    rows=[{"productName": "프레임", "stock": 10}]
                ),
            }
        )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "stage": "generate_answer",
        "outcome": "rejected",
        "reason": "ungrounded_korean_entity",
        "detail": ["가상제품"],
    }
