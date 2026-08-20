"""OpenAI 클라이언트를 지연 초기화된 싱글턴으로 제공한다."""

import os

from openai import OpenAI

_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    """환경변수 OPENAI_API_KEY로 초기화된 OpenAI 클라이언트를 반환한다."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client
