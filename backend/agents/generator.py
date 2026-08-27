"""프롬프트 메시지를 LLM에 전달해 생성된 쿼리 문자열을 반환한다."""

import os
from typing import Any


async def generate_query(
    openai_client: Any,
    messages: list[dict[str, str]],
) -> str:
    """LLM 응답에서 비어 있지 않은 쿼리 문자열을 추출한다."""
    response = await openai_client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        messages=messages,
    )

    if not response.choices:
        raise ValueError("LLM returned no query choices.")

    choice = response.choices[0]
    if choice.finish_reason != "stop":
        raise ValueError(
            f"LLM query generation did not finish normally: {choice.finish_reason}."
        )

    content = choice.message.content

    if content is None or not content.strip():
        raise ValueError("LLM returned an empty query.")

    return content.strip()
