"""OpenAI Chat Completions 응답과 호출 기록을 제공하는 테스트 mock."""

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class MockToolCallFunction:
    name: str
    arguments: str


@dataclass
class MockToolCall:
    function: MockToolCallFunction


@dataclass
class MockMessage:
    content: str | None = None
    tool_calls: list[MockToolCall] | None = None


@dataclass
class MockChoice:
    message: MockMessage
    finish_reason: str | None = "stop"


@dataclass
class MockChatCompletion:
    choices: list[MockChoice]


def make_tool_call_response(
    function_name: str, arguments: dict[str, Any]
) -> MockChatCompletion:
    """지정한 함수와 인자를 담은 tool call 응답을 만든다."""
    return MockChatCompletion(
        choices=[
            MockChoice(
                message=MockMessage(
                    tool_calls=[
                        MockToolCall(
                            function=MockToolCallFunction(
                                name=function_name,
                                arguments=json.dumps(arguments),
                            )
                        )
                    ]
                )
            )
        ]
    )


def make_tool_calls_response(
    calls: list[tuple[str, dict[str, Any]]],
) -> MockChatCompletion:
    """여러 엔티티처럼 한 응답에 여러 tool call을 담는다."""
    return MockChatCompletion(
        choices=[
            MockChoice(
                message=MockMessage(
                    tool_calls=[
                        MockToolCall(
                            function=MockToolCallFunction(
                                name=name,
                                arguments=json.dumps(arguments),
                            )
                        )
                        for name, arguments in calls
                    ]
                )
            )
        ]
    )


def make_no_tool_call_response() -> MockChatCompletion:
    """tool call이 없는 Chat Completions 응답을 만든다."""
    return MockChatCompletion(
        choices=[MockChoice(message=MockMessage(tool_calls=None))]
    )


def make_content_response(
    content: str,
    *,
    finish_reason: str | None = "stop",
) -> MockChatCompletion:
    """텍스트 content와 종료 사유를 담은 Chat Completions 응답을 만든다."""
    return MockChatCompletion(
        choices=[
            MockChoice(
                message=MockMessage(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


class _MockCompletions:
    def __init__(self, responses: list[MockChatCompletion]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> MockChatCompletion:
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]


class _MockChat:
    def __init__(self, completions: _MockCompletions) -> None:
        self.completions = completions


class MockOpenAIClient:
    """호출 순서대로 준비된 응답을 반환하고 전달 인자를 기록한다."""

    def __init__(self, *responses: MockChatCompletion) -> None:
        self._completions = _MockCompletions(list(responses))
        self.chat = _MockChat(self._completions)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._completions.calls
