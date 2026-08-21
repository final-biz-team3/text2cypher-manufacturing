from typing import NotRequired, TypedDict


# Orchestrator Agent가 관리하는 전역 상태
# resolve_entity, route_query가 채우고 다음 세션의 run_agents, generate_answer가 이어받는다.
# query만 필수이고 나머지는 그래프 실행 중 노드가 채워나가므로 NotRequired로 선언한다
# -> graph.invoke({"query": ...})처럼 부분 dict로 시작하거나, 노드 단위 테스트에서
#    부분 dict를 넘겨도 mypy가 통과한다.
class OrchestratorState(TypedDict):
    # 사용자 자연어 질의 (필수)
    query: str

    # resolve_entity가 확정한 엔티티 (productId, productName)
    entity: NotRequired[dict | None]

    # route_query가 결정한 실행 계획 (["sql"] / ["graph"] / ["sql", "graph"])
    tool_plan: NotRequired[list[str]]

    # SQL Agent 실행 결과 (다음 세션에서 채움, 이번 범위 미사용)
    sql_result: NotRequired[dict | None]

    # Cypher Agent 실행 결과 (다음 세션에서 채움, 이번 범위 미사용)
    graph_result: NotRequired[dict | None]

    # generate_answer가 만드는 최종 자연어 응답 (다음 세션에서 채움, 이번 범위 미사용)
    final_answer: NotRequired[str | None]

    # Orchestrator 레벨 에러 메시지 (다음 세션에서 채움, 이번 범위 미사용)
    error: NotRequired[str | None]
