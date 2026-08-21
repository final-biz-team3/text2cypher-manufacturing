"""오케스트레이터 노드 테스트에서 쓰는 OpenAI/PostgreSQL 더블."""

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class FakeToolCallFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    function: FakeToolCallFunction


@dataclass
class FakeMessage:
    content: str | None = None
    tool_calls: list[FakeToolCall] | None = None


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeChatCompletion:
    choices: list[FakeChoice]


def make_tool_call_response(
    function_name: str, arguments: dict[str, Any]
) -> FakeChatCompletion:
    """LLM이 지정한 함수를 호출하는 응답을 만든다."""
    return FakeChatCompletion(
        choices=[
            FakeChoice(
                message=FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            function=FakeToolCallFunction(
                                name=function_name,
                                arguments=json.dumps(arguments),
                            )
                        )
                    ]
                )
            )
        ]
    )


def make_no_tool_call_response() -> FakeChatCompletion:
    """LLM이 아무 함수도 호출하지 않는 응답을 만든다."""
    return FakeChatCompletion(
        choices=[FakeChoice(message=FakeMessage(tool_calls=None))]
    )


def make_content_response(content: str) -> FakeChatCompletion:
    """LLM이 텍스트만 반환하는 응답을 만든다."""
    return FakeChatCompletion(
        choices=[FakeChoice(message=FakeMessage(content=content))]
    )


class _FakeCompletions:
    def __init__(self, responses: list[FakeChatCompletion]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeChatCompletion:
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class FakeOpenAIClient:
    """.chat.completions.create() 호출마다 정해진 응답을 순서대로 반환하는 더블."""

    def __init__(self, *responses: FakeChatCompletion) -> None:
        self._completions = _FakeCompletions(list(responses))
        self.chat = _FakeChat(self._completions)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._completions.calls


class _FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class FakePostgresConnection:
    """production.product 조회만 흉내내는 psycopg Connection 더블."""

    def __init__(self, rows_by_name: dict[str, tuple[Any, ...]]) -> None:
        self._rows_by_name = rows_by_name
        self.last_query: tuple[str, tuple[Any, ...]] | None = None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self.last_query = (query, params)
        if not params:
            return _FakeCursor(None)
        name = params[0]
        return _FakeCursor(self._rows_by_name.get(name))
