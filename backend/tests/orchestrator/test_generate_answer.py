"""generate_answer의 LLM 호출·안전 분기 계약을 테스트한다."""

import json
from typing import Any, cast

import pytest

import orchestrator.guards.audit as audit_module
from orchestrator.errors import QueryInfrastructureError
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
    *,
    highlighted: list[dict[str, Any]] | None = None,
    sections: list[dict[str, Any]] | None = None,
) -> str:
    """generate_answer가 기대하는 구조화 출력(JSON) 응답 본문을 만든다.

    summary/caveat는 더 이상 LLM이 쓰지 않는다(_summary_template/
    _caveat_template이 결정론적으로 만든다) - 스키마에서도 빠졌으므로
    mock 응답도 highlighted/sections만 담는다."""
    return json.dumps({"highlighted": highlighted or [], "sections": sections or []})


def _answer_response(**kwargs: Any) -> MockChatCompletion:
    return make_content_response(_answer_json(**kwargs))


_SINGLE_STOCK_HIGHLIGHTED = [
    {"title": None, "metrics": [{"label": "재고", "value": 10}]}
]


async def test_generate_answer_uses_only_query_and_composed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "query-model")
    monkeypatch.setenv("ANSWER_MODEL", "answer-model")
    monkeypatch.setenv("ANSWER_MAX_OUTPUT_TOKENS", "900")
    client = MockOpenAIClient(_answer_response(highlighted=_SINGLE_STOCK_HIGHLIGHTED))
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

    assert result == {
        "final_answer": "요청하신 집계 결과를 확인했습니다.\n\n재고는 10입니다.",
        "visualization": {
            "type": "kpi",
            "title": None,
            "items": [{"label": "stock", "value": 10}],
        },
    }
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
    client = MockOpenAIClient(_answer_response(highlighted=_SINGLE_STOCK_HIGHLIGHTED))

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
async def test_generate_answer_falls_back_on_invalid_llm_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: MockChatCompletion,
) -> None:
    """LLM 응답 자체가 못 쓰게 망가져도(choices 없음/토큰 소진/빈 응답)
    사용자에게는 502 대신 rows 값을 그대로 옮긴 대체 답변이 나가야 한다 -
    이 실패군은 콘텐츠 품질이 아니라 가용성 문제라 재시도 대상도 아니다."""
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(response)

    result = await make_generate_answer_node(client)(
        {
            "query": "질의",
            "composed_result": _composed_result(
                rows=[{"productName": "프레임", "stock": 10}]
            ),
        }
    )

    assert result["final_answer"] == (
        "요청하신 조회 결과입니다.\n\n프레임의 stock는 10입니다."
    )
    assert len(client.calls) == 1


async def test_generate_answer_falls_back_on_non_json_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(make_content_response("이건 JSON이 아닙니다."))

    result = await make_generate_answer_node(client)(
        {"query": "질의", "composed_result": _composed_result()}
    )

    assert result["final_answer"] == (
        "요청하신 조회 결과입니다.\n\nid는 1이고, stock는 10입니다."
    )


async def test_generate_answer_falls_back_on_json_array_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = MockOpenAIClient(
        make_content_response(json.dumps(["not", "an", "object"]))
    )

    result = await make_generate_answer_node(client)(
        {"query": "질의", "composed_result": _composed_result()}
    )

    assert result["final_answer"] == (
        "요청하신 조회 결과입니다.\n\nid는 1이고, stock는 10입니다."
    )


class _FailingCompletions:
    async def create(self, **kwargs: Any) -> Any:
        raise RuntimeError("provider secret")


class _FailingClient:
    class _Chat:
        completions = _FailingCompletions()

    chat = _Chat()


async def test_generate_answer_falls_back_on_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    result = await make_generate_answer_node(_FailingClient())(
        {"query": "질의", "composed_result": _composed_result()}
    )

    assert "provider secret" not in result["final_answer"]
    assert result["final_answer"] == (
        "요청하신 조회 결과입니다.\n\nid는 1이고, stock는 10입니다."
    )


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
    """인프라 장애는 보여줄 실제 데이터 자체가 없으므로, 답변 생성 실패와
    달리 대체 답변으로 감추지 않고 그대로 에러를 전파한다."""
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


async def test_generate_answer_renders_multiple_items_as_bullet_list_of_sentences() -> (
    None
):
    """항목이 여러 개면 문장마다 글머리표를 붙인 목록으로 조립한다 - 항목이
    하나뿐일 때(글머리표 없이 문장 하나)와 달리, 여러 개면 스캔하기 쉽게
    목록 형태를 유지해야 한다. 형식은 LLM의 자유 서식 재량이 아니라
    렌더러가 고정한다."""
    client = MockOpenAIClient(
        _answer_response(
            highlighted=[
                {
                    "title": "프레임",
                    "metrics": [
                        {"label": "재고", "value": 0},
                        {"label": "부족량", "value": 500},
                    ],
                },
                {
                    "title": "체인",
                    "metrics": [{"label": "재고", "value": 2}],
                },
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "재고 부족 제품 알려줘",
            "composed_result": _composed_result(
                rows=[
                    {"productName": "프레임", "stock": 0, "shortage": 500},
                    {"productName": "체인", "stock": 2},
                ]
            ),
        }
    )

    assert result["final_answer"] == (
        "요청하신 조건에 맞는 항목을 확인했습니다.\n\n"
        "- 프레임의 재고는 0이고, 부족량은 500입니다.\n"
        "- 체인의 재고는 2입니다."
    )


async def test_generate_answer_hoists_shared_metric_and_renders_table() -> None:
    """모든 항목에 값이 똑같은 메트릭(완제품명)은 목록 위에 한 줄로 빼내고,
    항목마다 실제로 다른 값(부품명·공급업체)만 표로 렌더링한다 - 같은 값을
    항목마다 반복하면 가독성이 떨어진다."""
    client = MockOpenAIClient(
        _answer_response(
            highlighted=[
                {
                    "title": "프레임",
                    "metrics": [
                        {"label": "완제품명", "value": "HL Road Frame"},
                        {"label": "공급업체명", "value": "Acme"},
                    ],
                },
                {
                    "title": "체인",
                    "metrics": [
                        {"label": "완제품명", "value": "HL Road Frame"},
                        {"label": "공급업체명", "value": "Globex"},
                    ],
                },
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "부품과 공급업체를 알려줘",
            "composed_result": _composed_result(
                rows=[
                    {
                        "componentName": "프레임",
                        "finishedProductName": "HL Road Frame",
                        "supplierName": "Acme",
                    },
                    {
                        "componentName": "체인",
                        "finishedProductName": "HL Road Frame",
                        "supplierName": "Globex",
                    },
                ]
            ),
        }
    )

    assert result["final_answer"] == (
        "요청하신 조건에 맞는 항목을 확인했습니다.\n\n"
        "**완제품명**: HL Road Frame\n\n"
        "| 항목 | 공급업체명 |\n"
        "| --- | --- |\n"
        "| 프레임 | Acme |\n"
        "| 체인 | Globex |"
    )


async def test_generate_answer_renders_table_without_shared_metric_when_shape_matches() -> (
    None
):
    """공통 값이 없어도, 항목마다 메트릭 모양(라벨 구성)이 똑같으면 표로
    렌더링한다."""
    client = MockOpenAIClient(
        _answer_response(
            highlighted=[
                {"title": "프레임", "metrics": [{"label": "재고", "value": 0}]},
                {"title": "체인", "metrics": [{"label": "재고", "value": 2}]},
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(
                rows=[
                    {"productName": "프레임", "stock": 0},
                    {"productName": "체인", "stock": 2},
                ]
            ),
        }
    )

    assert result["final_answer"] == (
        "요청하신 조건에 맞는 항목을 확인했습니다.\n\n"
        "| 항목 | 재고 |\n"
        "| --- | --- |\n"
        "| 프레임 | 0 |\n"
        "| 체인 | 2 |"
    )


async def test_generate_answer_lists_titles_only_when_every_metric_is_shared() -> None:
    """항목의 메트릭이 전부 공통 값으로 빠지고 남는 게 없으면(표로 그릴 열이
    없으면), 표 대신 제목만 나열한 목록으로 대체한다."""
    client = MockOpenAIClient(
        _answer_response(
            highlighted=[
                {
                    "title": "프레임",
                    "metrics": [{"label": "완제품명", "value": "HL Road Frame"}],
                },
                {
                    "title": "체인",
                    "metrics": [{"label": "완제품명", "value": "HL Road Frame"}],
                },
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "부품을 알려줘",
            "composed_result": _composed_result(
                rows=[
                    {"componentName": "프레임", "finishedProductName": "HL Road Frame"},
                    {"componentName": "체인", "finishedProductName": "HL Road Frame"},
                ]
            ),
        }
    )

    assert result["final_answer"] == (
        "요청하신 조건에 맞는 항목을 확인했습니다.\n\n"
        "**완제품명**: HL Road Frame\n\n"
        "- 프레임\n"
        "- 체인"
    )


async def test_generate_answer_falls_back_when_highlighted_title_not_in_rows() -> None:
    """rows에 없는 title(예: 만들어낸 제품명)은 재시도까지 실패하면 502로
    새지 않고, rows 값 그대로인 대체 답변으로 감춰진다."""
    response = _answer_response(
        highlighted=[{"title": "가상제품", "metrics": [{"label": "재고", "value": 0}]}],
    )
    client = MockOpenAIClient(response, response)

    result = await make_generate_answer_node(client)(
        {
            "query": "재고 부족 제품 알려줘",
            "composed_result": _composed_result(
                rows=[{"productName": "프레임", "stock": 0}]
            ),
        }
    )

    assert "가상제품" not in result["final_answer"]
    assert result["final_answer"] == (
        "요청하신 조회 결과입니다.\n\n프레임의 stock는 0입니다."
    )
    assert len(client.calls) == 2


async def test_generate_answer_falls_back_when_highlighted_metric_value_not_in_rows() -> (
    None
):
    response = _answer_response(
        highlighted=[
            {"title": "프레임", "metrics": [{"label": "부족량", "value": 9999}]}
        ],
    )
    client = MockOpenAIClient(response, response)

    result = await make_generate_answer_node(client)(
        {
            "query": "재고 부족 제품 알려줘",
            "composed_result": _composed_result(
                rows=[{"productName": "프레임", "stock": 0, "shortage": 500}]
            ),
        }
    )

    assert "9999" not in result["final_answer"]
    assert result["final_answer"] == (
        "요청하신 조회 결과입니다.\n\n프레임의 stock는 0이고, shortage는 500입니다."
    )


async def test_generate_answer_renders_sections_with_subheadings() -> None:
    """mode="separate"(hybrid 분리 결과)는 섹션마다 소제목 + 목록으로
    조립되고, 근거 대조는 모든 섹션의 rows를 합친 범위에서 이뤄진다."""
    client = MockOpenAIClient(
        _answer_response(
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
        "요청하신 내용을 1개 항목으로 나누어 확인했습니다.\n\n"
        "### 재고 현황\n프레임의 재고는 5입니다."
    )


async def test_generate_answer_accepts_field_concept_label_not_in_source_text() -> None:
    """ "표준원가"처럼 실제 데이터엔 없는(영문 필드명 standardCost만 있는)
    한국어 개념어라도, highlighted.metrics[].label은 애초에 근거 검증
    대상이 아니므로 통과한다 - 값(1912.42)만 실제로 맞으면 라벨 표현은
    자유다. 이게 자유 문장 그라운딩의 근본 한계를 구조적으로 없애는 지점."""
    client = MockOpenAIClient(
        _answer_response(
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
        "Touring-1000 Yellow, 54의 조회 결과를 확인했습니다.\n\n"
        "Touring-1000 Yellow, 54의 정가는 약 $2,384이고, "
        "표준원가는 약 $1,912입니다."
    )


async def test_generate_answer_formats_currency_value_given_as_string() -> None:
    """_ITEM_SCHEMA는 value로 문자열도 허용하므로("2384.07"), LLM이 숫자
    대신 문자열로 값을 줘도 통화 서식("약 $2,384")이 깨지면 안 된다 -
    실제로 라이브 테스트에서 이 경우 서식이 조용히 빠지는 버그가 있었다."""
    client = MockOpenAIClient(
        _answer_response(
            highlighted=[
                {
                    "title": "Touring-1000 Yellow, 54",
                    "metrics": [{"label": "정가", "value": "2384.07"}],
                }
            ],
        )
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "정가를 알려줘",
            "composed_result": _composed_result(
                rows=[{"productName": "Touring-1000 Yellow, 54", "listPrice": 2384.07}]
            ),
        }
    )

    assert "약 $2,384" in result["final_answer"]


async def test_generate_answer_renders_null_title_for_pure_aggregate() -> None:
    """대표할 이름이 없는 순수 집계 결과(예: 활성 공급업체 수)는 title을
    null로 두고도 highlighted를 채울 수 있다."""
    client = MockOpenAIClient(
        _answer_response(
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
        "요청하신 집계 결과를 확인했습니다.\n\n활성 공급업체 수는 12곳입니다."
    )


async def test_generate_answer_falls_back_when_value_ungrounded_even_with_null_title() -> (
    None
):
    response = _answer_response(
        highlighted=[
            {"title": None, "metrics": [{"label": "활성 공급업체 수", "value": 999}]}
        ],
    )
    client = MockOpenAIClient(response, response)

    result = await make_generate_answer_node(client)(
        {
            "query": "활성 공급업체 수를 알려줘",
            "composed_result": _composed_result(rows=[{"activeSupplierCount": 12}]),
        }
    )

    assert "999" not in result["final_answer"]
    assert result["final_answer"] == (
        "요청하신 조회 결과입니다.\n\n활성 공급업체 수는 12곳입니다."
    )


async def test_generate_answer_retries_once_after_grounding_rejection() -> None:
    """근거 검증에 실패하면 실패 사유를 알려주고 한 번 더 시도하며, 두
    번째 응답이 통과하면 그 결과를 최종 답변으로 쓴다."""
    client = MockOpenAIClient(
        _answer_response(
            highlighted=[{"title": None, "metrics": [{"label": "재고", "value": 999}]}]
        ),
        _answer_response(highlighted=_SINGLE_STOCK_HIGHLIGHTED),
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(rows=[{"stock": 10}]),
        }
    )

    assert (
        result["final_answer"]
        == "요청하신 집계 결과를 확인했습니다.\n\n재고는 10입니다."
    )
    assert len(client.calls) == 2
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[0]["role"] == "developer"
    assert retry_messages[1]["role"] == "user"
    assert retry_messages[2]["role"] == "assistant"
    assert retry_messages[3]["role"] == "user"
    assert "999" in retry_messages[3]["content"]


async def test_generate_answer_falls_back_after_one_retry_still_fails() -> None:
    """재시도까지 실패하면 세 번째 호출은 하지 않고 대체 답변으로 마무리한다."""
    client = MockOpenAIClient(
        _answer_response(
            highlighted=[{"title": None, "metrics": [{"label": "재고", "value": 999}]}]
        ),
        _answer_response(
            highlighted=[{"title": None, "metrics": [{"label": "재고", "value": 888}]}]
        ),
    )

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(rows=[{"stock": 10}]),
        }
    )

    assert "999" not in result["final_answer"]
    assert "888" not in result["final_answer"]
    assert result["final_answer"] == "요청하신 조회 결과입니다.\n\nstock는 10입니다."
    assert len(client.calls) == 2


async def test_generate_answer_adds_caveat_when_source_truncated() -> None:
    """caveat는 LLM 문장이 아니라 context의 절단 플래그로만 결정된다 -
    자유 문장이 사라졌으므로 표현 방식과 무관하게 항상 같은 안내 문구가
    붙는다."""
    client = MockOpenAIClient(_answer_response(highlighted=_SINGLE_STOCK_HIGHLIGHTED))

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(
                mode="single", rows=[{"stock": 10}], truncated=True, total_count=5
            ),
        }
    )

    assert result["final_answer"] == (
        "요청하신 집계 결과를 확인했습니다.\n\n재고는 10입니다.\n\n"
        "*일부 결과만 바탕으로 한 답변이며, 전체 건수는 정확하지 않을 수 있습니다.*"
    )


async def test_generate_answer_fallback_notes_truncation_when_more_than_ten_rows() -> (
    None
):
    """폴백은 최대 10건만 보여주므로, 원본 자체는 안 잘렸어도(truncated=False)
    표시 개수가 10건을 넘으면 안내 문구를 붙여야 한다. 항목마다 메트릭 모양이
    똑같아(stock 하나뿐) 표로 렌더링된다 - 표의 데이터 행 개수로 10건 제한을
    확인한다."""
    client = MockOpenAIClient(make_content_response("", finish_reason="length"))
    rows = [{"productName": f"제품{i}", "stock": i} for i in range(12)]

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(rows=rows, total_count=12),
        }
    )

    assert result["final_answer"].count("| 제품") == 10
    assert result["final_answer"].endswith("*일부 결과만 바탕으로 한 답변입니다.*")


async def test_generate_answer_logs_accepted_validation_to_audit_trail(
    tmp_path, monkeypatch
) -> None:
    """모니터링 지표(PR #53 리뷰 권장사항)를 위해 통과 건도 stage/outcome을
    남겨야 사후에 오탐률(거부/전체)을 계산할 수 있다."""
    log_path = tmp_path / "answer_validation_audit.jsonl"
    monkeypatch.setenv("ANSWER_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("ANSWER_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_answer_audit_for_tests()
    client = MockOpenAIClient(_answer_response(highlighted=_SINGLE_STOCK_HIGHLIGHTED))

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
    """caveat가 있는(=일부만 잘려 들어온) 답변도 감사 로그는 답변 1건당
    1줄만 남겨야 한다 - 필드별로 로깅하면 caveat가 있는 답변만 accepted
    건수가 두 배로 잡혀 오탐률 지표가 왜곡된다."""
    log_path = tmp_path / "answer_validation_audit.jsonl"
    monkeypatch.setenv("ANSWER_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("ANSWER_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_answer_audit_for_tests()
    client = MockOpenAIClient(_answer_response(highlighted=_SINGLE_STOCK_HIGHLIGHTED))

    await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(
                mode="single", rows=[{"stock": 10}], truncated=True, total_count=5
            ),
        }
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


async def test_generate_answer_logs_rejected_highlighted_title_to_audit_trail(
    tmp_path, monkeypatch
) -> None:
    """근거 없는 highlighted title 거부 시 실패 사유와 실제 표현을 감사
    로그에 남겨야, 나중에 진짜 환각 사례를 데이터로 추적할 수 있다.

    재시도(1회) 때문에 같은 실패를 두 번 재현해야 최종 거부까지 도달한다 -
    중간 시도 실패는 로깅하지 않고 최종 실패만 감사 로그에 남기므로, 여기선
    두 시도 모두 같은 근거 없는 title을 내는 응답 2개를 준비한다."""
    log_path = tmp_path / "answer_validation_audit.jsonl"
    monkeypatch.setenv("ANSWER_AUDIT_LOG_PATH", str(log_path))
    monkeypatch.setenv("ANSWER_AUDIT_ALSO_CONSOLE", "false")
    audit_module.reset_answer_audit_for_tests()
    response = _answer_response(
        highlighted=[{"title": "가상제품", "metrics": [{"label": "재고", "value": 10}]}]
    )
    client = MockOpenAIClient(response, response)

    result = await make_generate_answer_node(client)(
        {
            "query": "재고를 알려줘",
            "composed_result": _composed_result(
                rows=[{"productName": "프레임", "stock": 10}]
            ),
        }
    )

    assert "가상제품" not in result["final_answer"]
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "stage": "generate_answer",
        "outcome": "rejected",
        "reason": "ungrounded_highlighted_title",
        "detail": ["가상제품"],
    }
