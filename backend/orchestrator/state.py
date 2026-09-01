from typing import Any, Literal, NotRequired, TypedDict

from orchestrator.planning import BomShortageTransform, RouteDraft, Subquery

EmptyReason = Literal["NO_DATA", "INCONCLUSIVE"]
CompositionMode = Literal["single", "joined", "separate"]
ToolName = Literal["sql", "graph"]
FailureKind = Literal["user_correctable", "infrastructure", "internal"]
FailureStage = Literal[
    "entity_resolution",
    "routing",
    "planning",
    "validation",
    "execution",
    "dependency",
]


class QueryFailure(TypedDict):
    """LLM과 API 흐름에서 사용해도 되는 사용자 안전 실패 정보."""

    code: str
    stage: FailureStage
    category: str
    kind: FailureKind
    retryable: bool
    user_safe_reason: str
    suggested_action: str
    failed_tool: ToolName | None
    dependent_failure: bool


class ComposedSection(TypedDict):
    tool: ToolName
    rows: list[dict[str, Any]]
    empty_reason: EmptyReason | None


class ComposedResult(TypedDict):
    mode: CompositionMode
    transform: NotRequired[str]
    rows: list[dict[str, Any]]
    sections: dict[str, ComposedSection]
    error: str | None
    empty_reason: EmptyReason | None
    total_count: int
    truncated: bool


# query만 필수이고 나머지는 그래프 실행 중 노드가 채워나가므로 NotRequired로 선언한다
# -> graph.invoke({"query": ...})처럼 부분 dict로 시작하거나, 노드 단위 테스트에서
#    부분 dict를 넘겨도 mypy가 통과한다.
class OrchestratorState(TypedDict):
    # 사용자 자연어 질의 (필수)
    query: str

    entity: NotRequired[dict | list[dict] | None]

    confirmed_entity: NotRequired[dict | list[dict] | None]

    # route_query의 dependency DAG에서 파생한 실행 순서
    tool_plan: NotRequired[list[str]]

    routeDraft: NotRequired[RouteDraft]
    rawRouteDraft: NotRequired[dict[str, Any]]
    subqueries: NotRequired[list[Subquery]]

    resultTransform: NotRequired[BomShortageTransform | None]

    # tool_plan에 따라 생성된 읽기 전용 쿼리 (DB 실행 전 단계)
    sql_query: NotRequired[str | None]
    cypher_query: NotRequired[str | None]

    # SQL 실행 결과
    sql_result: NotRequired[dict | None]

    # Cypher 실행 결과
    graph_result: NotRequired[dict | None]

    # SQL·GRAPH 실행 결과를 계획의 join 계약에 따라 조합한 내부 결과
    # (/chat 응답에는 노출하지 않는다.)
    composed_result: NotRequired[ComposedResult]

    # 원본 예외·쿼리를 포함하지 않는 사용자 안전 실패 컨텍스트
    query_failure: NotRequired[QueryFailure | None]

    # 최종 자연어 응답
    final_answer: NotRequired[str | None]

    # Orchestrator 레벨 에러 메시지
    error: NotRequired[str | None]
