"""Windows에서 psycopg async 모드를 쓰기 위한 이벤트 루프 정책 설정.

psycopg의 async 모드는 Windows 기본 이벤트 루프(ProactorEventLoop)를
지원하지 않고 InterfaceError로 즉시 거부한다 - WindowsSelectorEventLoopPolicy로
고정해야 한다. main.py/evaluation/cli.py/tests/conftest.py가 각자 이
로직을 복붙해서 갖고 있었는데, 나중에 조정할 일이 생기면 세 곳을 같이
고쳐야 해서 여기로 모았다.
"""

import asyncio
import sys


def windows_selector_event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """Windows면 WindowsSelectorEventLoopPolicy, 아니면 기본 정책을 반환한다."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


def use_windows_selector_event_loop_policy() -> None:
    """Windows일 때만 프로세스 전역 이벤트 루프 정책을 고정한다. 이벤트 루프가
    만들어지기 전, 모듈 로드 시점에 호출해야 한다."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
