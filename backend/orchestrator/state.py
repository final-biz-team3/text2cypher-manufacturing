"""Orchestrator Agent가 관리하는 전역 상태를 정의한다."""

from typing import NotRequired, TypedDict


class OrchestratorState(TypedDict):
    """resolve_entity, route_query가 채우고 다음 세션의 run_agents,
    generate_answer가 이어받는 전역 상태.

    query만 필수다. 나머지는 그래프 실행 중 노드가 채워나가므로
    NotRequired로 선언한다 — graph.invoke({"query": ...})처럼 부분
    dict로 시작하거나, 노드 단위 테스트에서 부분 dict를 넘겨도
    mypy가 통과한다.
    """

    query: str
    entity: NotRequired[dict | None]
    tool_plan: NotRequired[list[str]]
    sql_result: NotRequired[dict | None]
    graph_result: NotRequired[dict | None]
    final_answer: NotRequired[str | None]
    error: NotRequired[str | None]
