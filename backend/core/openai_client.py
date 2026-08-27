import os

from openai import AsyncOpenAI

# OpenAI 클라이언트를 지연 초기화된 싱글턴으로 제공
_client: AsyncOpenAI | None = None


# 환경변수 OPENAI_API_KEY로 초기화된 AsyncOpenAI 클라이언트를 반환
def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client
