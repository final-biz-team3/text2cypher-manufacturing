from typing import Literal, NotRequired, TypedDict

NaturalIntent = Literal[
    "READ",
    "CREATE",
    "UPDATE",
    "DELETE",
    "SCHEMA_CHANGE",
    "PERMISSION_CHANGE",
    "UNKNOWN",
]


class MatchedTerm(TypedDict):
    original: str
    canonical: str
    concept_id: str
    concept_type: Literal["BUSINESS", "ACTION"]
    target_type: str | None


class DetectedAction(TypedDict):
    original: str
    canonical: str
    action_type: Literal[
        "READ",
        "CREATE",
        "UPDATE",
        "DELETE",
        "SCHEMA_CHANGE",
        "PERMISSION_CHANGE",
    ]
    default_policy: Literal["ALLOW", "BLOCK"]


class NaturalGuardResult(TypedDict):
    decision: Literal["ALLOW_READ", "BLOCK_WRITE", "NEEDS_CLARIFICATION"]
    intent: NaturalIntent
    reason: str
    confidence: float


class GuardViolation(TypedDict):
    database: Literal["postgresql", "neo4j"]
    code: str
    message: str


class QueryGuardResult(TypedDict):
    decision: Literal["PASSED", "BLOCKED"]
    sql_read_only: bool | None
    cypher_read_only: bool | None
    violations: list[GuardViolation]


class NormalizationResult(TypedDict):
    normalized_query: str
    matched_terms: list[MatchedTerm]
    detected_actions: list[DetectedAction]


class NaturalGuardNodeResult(TypedDict, total=False):
    natural_guard: NaturalGuardResult
    execution_allowed: bool
    error: str


class QueryGuardNodeResult(TypedDict, total=False):
    query_guard: QueryGuardResult
    execution_allowed: bool
    error: str


# query만 필수이고 나머지는 그래프 실행 중 노드가 채워나가므로 NotRequired로 선언한다
# -> graph.invoke({"query": ...})처럼 부분 dict로 시작하거나, 노드 단위 테스트에서
#    부분 dict를 넘겨도 mypy가 통과한다.
class OrchestratorState(TypedDict):
    # 사용자 자연어 질의 (필수)
    query: str

    # normalize_terms가 만든 내부 처리용 질문과 추적 정보
    normalized_query: NotRequired[str]
    matched_terms: NotRequired[list[MatchedTerm]]
    detected_actions: NotRequired[list[DetectedAction]]

    # 자연어 및 생성 쿼리의 읽기 전용 검사 결과
    natural_guard: NotRequired[NaturalGuardResult]
    query_guard: NotRequired[QueryGuardResult]
    execution_allowed: NotRequired[bool]

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


def get_effective_query(state: OrchestratorState) -> str:
    """정규화된 질문이 있으면 사용하고, 없으면 사용자 원문을 반환한다."""
    return state.get("normalized_query") or state["query"]
