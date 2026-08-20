"""resolve_entity -> route_query 2노드 Orchestrator 서브그래프를 조립한다."""

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.state import OrchestratorState


def build_orchestrator_graph(
    openai_client: Any, postgres_connection: Any
) -> CompiledStateGraph:
    """OpenAI/PostgreSQL 클라이언트를 주입받아 컴파일된 그래프를 반환한다.

    START -> resolve_entity -> route_query -> END.
    run_agents, generate_answer는 다음 세션에서 이어붙인다.
    """
    graph = StateGraph(OrchestratorState)
    # mypy는 factory가 반환하는 `Callable[[OrchestratorState], dict]` 정적 타입을
    # add_node의 `_Node[NodeInputT] | ...` 오버로드 Union과 단일화하지 못해
    # call-overload 오류를 낸다(런타임 시그니처는 `_Node`와 정확히 일치). 이는
    # langgraph 1.2.11의 add_node 오버로드/mypy 2.3.0 조합에서 알려진 타입 추론
    # 한계이며, 인자를 top-level 함수로 직접 넘기면 재현되지 않는다.
    graph.add_node(
        "resolve_entity",
        make_resolve_entity_node(openai_client, postgres_connection),  # type: ignore[call-overload]
    )
    graph.add_node(
        "route_query", make_route_query_node(openai_client)  # type: ignore[call-overload]
    )
    graph.add_edge(START, "resolve_entity")
    graph.add_edge("resolve_entity", "route_query")
    graph.add_edge("route_query", END)
    return graph.compile()
