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


def _answer_json(
    summary: str,
    *,
    highlighted: list[dict[str, Any]] | None = None,
    sections: list[dict[str, Any]] | None = None,
    caveat: str | None = None,
) -> str:
    """generate_answer가 기대하는 구조화 출력(JSON) 응답 본문을 만든다."""
    return json.dumps(
        {
            "summary": summary,
            "highlighted": highlighted or [],
            "sections": sections or [],
            "caveat": caveat,
        }
    )


def _answer_response(summary: str, **kwargs: Any) -> MockChatCompletion:
    return make_content_response(_answer_json(summary, **kwargs))


async def test_generate_answer_uses_only_query_and_composed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "query-model")
    monkeypatch.setenv("ANSWER_MODEL", "answer-model")
    monkeypatch.setenv("ANSWER_MAX_OUTPUT_TOKENS", "900")
    client = MockOpenAIClient(_answer_response("재고는 10개입니다."))
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

    assert result == {"final_answer": "재고는 10개입니다."}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "answer-model"
    assert call["max_completion_tokens"] == 900
    assert call["response_format"]["json_schema"]["name"] == "manufacturing_answer"
    assert call["response_format"]["json_schema"]["strict"] is True
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
    client = MockOpenAIClient(_answer_response("답변"))

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


async def test_generate_answer_rejects_non_json_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(make_content_response("이건 JSON이 아닙니다."))

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {"query": "질의", "composed_result": _composed_result()}
        )


async def test_generate_answer_rejects_json_array_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(
        make_content_response(json.dumps(["not", "an", "object"]))
    )

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {"query": "질의", "composed_result": _composed_result()}
        )


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
    # 근거 검증 실패는 1회 재시도되므로, 두 시도 모두 같은 근거 없는 값을
    # 내는 응답을 준비해야 최종 거부까지 재현된다.
    client = MockOpenAIClient(
        _answer_response("재고는 999입니다."), _answer_response("재고는 999입니다.")
    )

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {"query": "재고를 알려줘", "composed_result": _composed_result()}
        )


async def test_generate_answer_does_not_treat_question_number_as_evidence() -> None:
    client = MockOpenAIClient(
        _answer_response("정가는 0원입니다."), _answer_response("정가는 0원입니다.")
    )

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
    client = MockOpenAIClient(_answer_response("재고는 10입니다."))

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
        _answer_response("제품 SECRET-PRODUCT의 재고는 10입니다."),
        _answer_response("제품 SECRET-PRODUCT의 재고는 10입니다."),
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
        _answer_response(f"{unknown_name}의 재고는 10입니다."),
        _answer_response(f"{unknown_name}의 재고는 10입니다."),
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
    client = MockOpenAIClient(_answer_response("프레임의 재고는 10입니다."))

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
        _answer_response("1. 재고는 10입니다.\n2. 재고는 10입니다.")
    )

    result = await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    assert result["final_answer"].startswith("1. 재고는 10")


async def test_generate_answer_accepts_grounded_korean_scaled_number() -> None:
    client = MockOpenAIClient(_answer_response("재고는 1만입니다."))

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
    client = MockOpenAIClient(_answer_response("재고는 10건입니다."))

    result = await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    assert result["final_answer"] == "재고는 10건입니다."


async def test_generate_answer_still_strips_ungrounded_specific_units() -> None:
    """ "개"/"건"과 달리 "원"(화폐)처럼 수량의 종류를 새로 주장하는 단위는
    여전히 원본과 대조해, 근거 없으면 단위를 뗀다."""
    client = MockOpenAIClient(_answer_response("재고는 10원입니다."))

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
        _answer_response(
            "재고가 부족한 제품이 있습니다.",
            caveat="전체 141건 중 73만 포함되어 있습니다.",
        )
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
    client = MockOpenAIClient(
        _answer_response("재고는 73만입니다."), _answer_response("재고는 73만입니다.")
    )

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
    client = MockOpenAIClient(_answer_response("재고는 10입니다."))

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


async def test_generate_answer_logs_accepted_validation_once_with_caveat(
    tmp_path, monkeypatch
) -> None:
    """summary와 caveat 둘 다 있는 답변도 감사 로그는 답변 1건당 1줄만
    남겨야 한다 - 필드별로 로깅하면 caveat가 있는 답변만 accepted 건수가
    두 배로 잡혀 오탐률 지표가 왜곡된다."""
    log_path = tmp_path / "answer_validation_audit.jsonl"
    monkeypatch.setenv("ANSWER_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("ANSWER_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_answer_audit_for_tests()
    client = MockOpenAIClient(
        _answer_response("재고는 10입니다.", caveat="일부 결과만 포함되어 있습니다.")
    )

    await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


async def test_generate_answer_logs_rejected_korean_entity_to_audit_trail(
    tmp_path, monkeypatch
) -> None:
    """근거 없는 한국어 엔티티 거부 시 실패 사유와 실제 토큰을 감사 로그에
    남겨야, 나중에 "가상제품"류 진짜 환각과 "안전재고"류 정당한 스키마
    의역 오탐을 구분해 온톨로지 투자 여부를 데이터로 판단할 수 있다.

    재시도(1회) 때문에 같은 실패를 두 번 재현해야 최종 거부까지 도달한다 -
    중간 시도 실패는 로깅하지 않고 최종 실패만 감사 로그에 남기므로, 여기선
    두 시도 모두 같은 근거 없는 표현을 내는 응답 2개를 준비한다."""
    log_path = tmp_path / "answer_validation_audit.jsonl"
    monkeypatch.setenv("ANSWER_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("ANSWER_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_answer_audit_for_tests()
    client = MockOpenAIClient(
        _answer_response("가상제품의 재고는 10입니다."),
        _answer_response("가상제품의 재고는 10입니다."),
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

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "stage": "generate_answer",
        "outcome": "rejected",
        "reason": "ungrounded_korean_entity",
        "detail": ["가상제품"],
    }


async def test_generate_answer_renders_highlighted_items_as_bullet_list() -> None:
    """summary 다음 빈 줄, 그다음 "- **title**: label value" 형식의 목록을
    항상 같은 순서로 조립한다 - LLM의 자유 서식 재량이 아니라 렌더러가
    형식을 고정한다."""
    client = MockOpenAIClient(
        _answer_response(
            "재고 부족 제품은 다음과 같습니다.",
            highlighted=[
                {
                    "title": "프레임",
                    "metrics": [
                        {"label": "재고", "value": 0},
                        {"label": "부족량", "value": 500},
                    ],
                }
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "재고 부족 제품 알려줘",
            "composed_result": _composed_result(
                rows=[{"productName": "프레임", "stock": 0, "shortage": 500}]
            ),
        }
    )

    assert result["final_answer"] == (
        "재고 부족 제품은 다음과 같습니다.\n\n- **프레임**: 재고 0, 부족량 500"
    )


async def test_generate_answer_rejects_highlighted_title_not_in_rows() -> None:
    response = _answer_response(
        "재고 부족 제품은 다음과 같습니다.",
        highlighted=[{"title": "가상제품", "metrics": [{"label": "재고", "value": 0}]}],
    )
    client = MockOpenAIClient(response, response)

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {
                "query": "재고 부족 제품 알려줘",
                "composed_result": _composed_result(
                    rows=[{"productName": "프레임", "stock": 0}]
                ),
            }
        )


async def test_generate_answer_rejects_highlighted_metric_value_not_in_rows() -> None:
    response = _answer_response(
        "재고 부족 제품은 다음과 같습니다.",
        highlighted=[
            {"title": "프레임", "metrics": [{"label": "부족량", "value": 9999}]}
        ],
    )
    client = MockOpenAIClient(response, response)

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {
                "query": "재고 부족 제품 알려줘",
                "composed_result": _composed_result(
                    rows=[{"productName": "프레임", "stock": 0, "shortage": 500}]
                ),
            }
        )


async def test_generate_answer_renders_sections_with_subheadings() -> None:
    """mode="separate"(hybrid 분리 결과)는 섹션마다 소제목 + 목록으로
    조립되고, 근거 대조는 모든 섹션의 rows를 합친 범위에서 이뤄진다."""
    client = MockOpenAIClient(
        _answer_response(
            "두 출처에서 결과를 찾았습니다.",
            sections=[
                {
                    "title": "재고 현황",
                    "highlighted": [
                        {
                            "title": "프레임",
                            "metrics": [{"label": "재고", "value": 5}],
                        }
                    ],
                }
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "재고랑 관련 부품 알려줘",
            "composed_result": _composed_result(
                mode="separate",
                rows=[],
                sections={
                    "sql_stock": {
                        "tool": "sql",
                        "rows": [{"productName": "프레임", "stock": 5}],
                        "empty_reason": None,
                    }
                },
            ),
        }
    )

    assert result["final_answer"] == (
        "두 출처에서 결과를 찾았습니다.\n\n### 재고 현황\n- **프레임**: 재고 5"
    )


async def test_generate_answer_accepts_field_concept_label_not_in_source_text() -> None:
    """ "표준원가"처럼 실제 데이터엔 없는(영문 필드명 standardCost만 있는)
    한국어 개념어라도, summary가 아니라 highlighted.metrics[].label로 오면
    그라운딩 검사 대상이 아니라 통과해야 한다 - 값(1912.42)만 실제로 맞으면
    라벨 표현은 자유. 이게 자유 문장 그라운딩의 근본 한계(§근본 원인 논의)를
    구조적으로 없애는 지점이다."""
    client = MockOpenAIClient(
        _answer_response(
            "요청하신 제품의 가격 정보입니다.",
            highlighted=[
                {
                    "title": "Touring-1000 Yellow, 54",
                    "metrics": [
                        {"label": "정가", "value": 2384.07},
                        {"label": "표준원가", "value": 1912.42},
                    ],
                }
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘",
            "composed_result": _composed_result(
                rows=[
                    {
                        "productId": 956,
                        "productName": "Touring-1000 Yellow, 54",
                        "listPrice": 2384.07,
                        "standardCost": 1912.42,
                    }
                ]
            ),
        }
    )

    assert result["final_answer"] == (
        "요청하신 제품의 가격 정보입니다.\n\n"
        "- **Touring-1000 Yellow, 54**: 정가 2384.07, 표준원가 1912.42"
    )


async def test_generate_answer_renders_null_title_for_pure_aggregate() -> None:
    """대표할 이름이 없는 순수 집계 결과(예: 활성 공급업체 수)는 title을
    null로 두고도 highlighted를 채울 수 있다 - summary가 구체적 수치를
    반복하지 않게 강제하다 보니 이름 붙일 대상이 없는 경우를 위해 필요하다."""
    client = MockOpenAIClient(
        _answer_response(
            "현재 활성 공급업체 현황입니다.",
            highlighted=[
                {
                    "title": None,
                    "metrics": [{"label": "활성 공급업체 수", "value": 12}],
                }
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "활성 공급업체 수를 알려줘",
            "composed_result": _composed_result(rows=[{"activeSupplierCount": 12}]),
        }
    )

    assert result["final_answer"] == (
        "현재 활성 공급업체 현황입니다.\n\n- 활성 공급업체 수 12"
    )


async def test_generate_answer_rejects_ungrounded_value_even_with_null_title() -> None:
    response = _answer_response(
        "현재 활성 공급업체 현황입니다.",
        highlighted=[
            {"title": None, "metrics": [{"label": "활성 공급업체 수", "value": 999}]}
        ],
    )
    client = MockOpenAIClient(response, response)

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {
                "query": "활성 공급업체 수를 알려줘",
                "composed_result": _composed_result(rows=[{"activeSupplierCount": 12}]),
            }
        )


async def test_generate_answer_accepts_field_label_restated_in_summary() -> None:
    """ "표준원가"가 highlighted.metrics.label이 아니라 summary 문장 자체에
    나와도, standardCost 필드가 이번 답변 데이터에 실제로 있으면 통과해야
    한다 - _field_label_terms가 스키마 라벨을 동적 허용목록으로 추가하는
    안전망이, 프롬프트 지시를 LLM이 안 지킨 경우까지 커버하는지 확인한다."""
    client = MockOpenAIClient(
        _answer_response(
            "이 제품의 표준원가는 실제 데이터 기준으로 확인됩니다.",
            highlighted=[
                {
                    "title": "Touring-1000 Yellow, 54",
                    "metrics": [{"label": "표준원가", "value": 1912.42}],
                }
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "이 제품의 표준원가를 알려줘",
            "composed_result": _composed_result(
                rows=[
                    {
                        "productName": "Touring-1000 Yellow, 54",
                        "standardCost": 1912.42,
                    }
                ]
            ),
        }
    )

    assert "표준원가" in result["final_answer"]
    assert len(client.calls) == 1


async def test_generate_answer_retries_once_after_grounding_rejection() -> None:
    """근거 검증에 실패하면 실패 사유를 알려주고 한 번 더 시도하며, 두
    번째 응답이 통과하면 그 결과를 최종 답변으로 쓴다."""
    client = MockOpenAIClient(
        _answer_response("재고는 999입니다."),
        _answer_response("재고는 10입니다."),
    )

    result = await make_generate_answer_node(client)(
        {"query": "재고를 알려줘", "composed_result": _composed_result()}
    )

    assert result["final_answer"] == "재고는 10입니다."
    assert len(client.calls) == 2
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[0]["role"] == "developer"
    assert retry_messages[1]["role"] == "user"
    assert retry_messages[2]["role"] == "assistant"
    assert retry_messages[3]["role"] == "user"
    assert "999" in retry_messages[3]["content"]


async def test_generate_answer_gives_up_after_one_retry() -> None:
    """재시도까지 실패하면 세 번째 호출은 하지 않고 그대로 거부한다."""
    client = MockOpenAIClient(
        _answer_response("재고는 999입니다."),
        _answer_response("재고는 888입니다."),
    )

    with pytest.raises(AnswerGenerationError):
        await make_generate_answer_node(client)(
            {"query": "재고를 알려줘", "composed_result": _composed_result()}
        )

    assert len(client.calls) == 2
