from typing import NotRequired, TypedDict


# query만 필수이고 나머지는 그래프 실행 중 노드가 채워나가므로 NotRequired로 선언한다
# -> graph.invoke({"query": ...})처럼 부분 dict로 시작하거나, 노드 단위 테스트에서
#    부분 dict를 넘겨도 mypy가 통과한다.
class OrchestratorState(TypedDict):
    # 사용자 자연어 질의 (필수)
    query: str

    # resolve_entity가 확정한 엔티티 (productId, productName)
    entity: NotRequired[dict | None]

    # 이전 턴에 사용자가 확인한 entity (있으면 resolve_entity가 매칭을 건너뜀)
    confirmed_entity: NotRequired[dict | None]

    # route_query가 결정한 실행 계획 (["sql"] / ["graph"] / ["sql", "graph"])
    tool_plan: NotRequired[list[str]]

    # tool_plan에 따라 생성된 읽기 전용 쿼리 (DB 실행 전 단계)
    sql_query: NotRequired[str | None]
    cypher_query: NotRequired[str | None]

    # SQL 실행 결과
    sql_result: NotRequired[dict | None]

    # Cypher 실행 결과
    graph_result: NotRequired[dict | None]

    # 최종 자연어 응답
    final_answer: NotRequired[str | None]

    # Orchestrator 레벨 에러 메시지
    error: NotRequired[str | None]
