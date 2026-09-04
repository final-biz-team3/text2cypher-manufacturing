"""쿼리 생성 -> 실행 -> (실패 시) 재생성 재시도 SubGraph의 공통 배선.

sql_agent.py/cypher_agent.py는 완전히 대칭 구조라(생성 함수, 실행 콜백, DB별
예외 화이트리스트, 빈 결과 피드백 문구만 다름) 여기서 재시도 상태 머신
(agent -> tools -> should_retry 사이클, 예외 3분류, 빈 결과 1회 재시도)을
한 곳에 모아두고 각 모듈은 설정값만 주입한다."""

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from starlette.concurrency import run_in_threadpool

from core.observability.events import emit_event
from core.observability.metrics import QUERY_ATTEMPTS, REPAIR_EXHAUSTED, REPAIRS
from core.observability.privacy import query_hash, redact_query
from orchestrator.errors import QueryInfrastructureError
from orchestrator.execution.result import QueryResultBatch
from orchestrator.guards.audit import log_guard_decision
from orchestrator.guards.result import GuardResult
from orchestrator.query_failures import make_query_failure
from orchestrator.repair import (
    RepairContext,
    make_repair_context,
    render_repair_feedback,
)
from orchestrator.state import QueryFailure, ToolName

# 원본 1회 + 재시도 2회 = 총 3회
MAX_ATTEMPTS = 3

# 스키마 화이트리스트에 아예 없는 이름을 참조한 차단은 LLM이 재생성으로
# 고칠 수 없다(같은 schema 텍스트를 다시 줘도 같은 결론에 도달함) - 재시도
# 예산을 낭비하지 않도록 접속 오류와 같은 성격(retryable=False)으로 취급한다.
# 반대로 WRITE_KEYWORD_DETECTED 등 쿼리 형태 문제나, 스키마 한정자 누락처럼
# 가드가 해석에 실패한 fail-closed 케이스(UNRESOLVED_TABLE_REFERENCE/
# UNRECOGNIZED_LABEL_SYNTAX)는 LLM이 쿼리를 다르게 써서 고칠 여지가 있으므로
# 재시도 대상으로 둔다.
_NON_RETRYABLE_GUARD_REASONS = frozenset(
    {
        "UNKNOWN_TABLE",
        "UNKNOWN_LABEL_OR_RELATIONSHIP",
    }
)

EMPTY_RESULT_ERROR = "EMPTY_RESULT"

# 빈 결과가 최종 수용될 때, 이전 시도 이력에 실행 오류(EMPTY_RESULT가 아닌 진짜
# 쿼리 결함)가 있었는지로 원인을 구분한다.
NO_DATA = "NO_DATA"
INCONCLUSIVE = "INCONCLUSIVE"


class RetryAgentState(TypedDict):
    query: str
    # 평가에서 V1/V2에 완전히 같은 최초 실패 쿼리를 주입할 때만 사용한다.
    # 프로덕션 상태에는 이 값이 없으므로 기존 최초 생성 경로는 그대로다.
    initial_query: NotRequired[str]
    source_scope: NotRequired[str]
    entity: dict | list[dict] | None
    schema: str
    messages: list
    result: Any | None
    error: str | None
    attempt_count: int
    attempts: list[dict]
    empty_retried: bool
    retryable: bool
    empty_reason: str | None
    truncated: NotRequired[bool]
    required_outputs: NotRequired[list[str]]
    input_bindings: NotRequired[dict[str, list[Any]]]
    business_rules: NotRequired[list[str]]
    failure: NotRequired[QueryFailure | None]
    repair_context: NotRequired[RepairContext | None]
    repair_issue_codes: NotRequired[list[str]]
    failed_query: NotRequired[str | None]
    generated_query: NotRequired[str | None]


def _classify_empty_result(prior_attempts: list[dict]) -> str:
    """이번 빈 결과 이전 시도 중 실행 오류가 하나라도 있었으면, 그 오류가
    정말 고쳐졌다는 보장이 없으므로 INCONCLUSIVE로 본다. 전부 깨끗하게
    실행됐는데 결과만 없었다면 NO_DATA(실제로 데이터가 없음)로 본다."""
    has_real_error = any(
        attempt["error"] not in (None, EMPTY_RESULT_ERROR) for attempt in prior_attempts
    )
    return INCONCLUSIVE if has_real_error else NO_DATA


def make_retry_agent_subgraph(
    *,
    logger: logging.Logger,
    label: str,
    generate: Callable[[RetryAgentState, str | None, str | None], Awaitable[str]],
    execute: Callable[[str], Awaitable[Any]],
    connection_exceptions: tuple[type[Exception], ...],
    retryable_exceptions: tuple[type[Exception], ...],
    empty_result_feedback: str,
    guard: Callable[[str], GuardResult] | None = None,
    query_contract_error: Callable[[str, list[str]], str | None] | None = None,
    classify_execution_error: Callable[[Exception], QueryFailure] | None = None,
    repair_instructions: dict[str, tuple[str, ...]] | None = None,
    repair_engine_env: str | None = None,
) -> CompiledStateGraph:
    """query/entity/schema를 state에서 읽어 쿼리 문자열을 생성하는 generate와,
    쿼리 문자열을 실행하는 execute를 주입받아 재시도 SubGraph를 만든다.
    connection_exceptions는 retryable_exceptions보다 반드시 나중에 검사한다
    (예: psycopg.errors.QueryCanceled는 psycopg.OperationalError의 서브클래스라
    순서를 바꾸면 재시도 대상인데도 접속 오류로 오분류된다). execute 내부 구현
    (DB 접속, READ 전용 가드, rollback/commit)은 이 함수의 책임이 아니며 이미
    올바르게 구현되어 있다는 전제로 호출만 한다. 그래프 내부에서는 절대
    raise하지 않고 error 필드로만 실패를 전달한다.
    guard가 주어지면 execute 호출 직전에 쿼리를 검증하고, 차단 시 execute를
    호출하지 않은 채 재시도 대상으로 처리한다(attempt_count에 포함)."""

    tool_name: ToolName = "sql" if label.startswith("sql") else "graph"
    contract_issue_code = (
        "SQL_OUTPUT_CONTRACT_FAILED"
        if tool_name == "sql"
        else "CYPHER_OUTPUT_CONTRACT_FAILED"
    )
    instructions_by_code = repair_instructions or {}

    def repair_engine() -> str:
        if repair_engine_env is None:
            return "v1"
        configured = os.getenv(repair_engine_env, "v1").strip().lower()
        return configured if configured in {"v1", "v2"} else "v1"

    def _internal_validation_error() -> QueryFailure:
        return make_query_failure(
            code="QUERY_VALIDATION_INTERNAL_ERROR",
            stage="validation",
            category="INTERNAL_QUERY_FAILURE",
            kind="internal",
            retryable=False,
            user_safe_reason="질의를 검증하는 과정에서 내부 오류가 발생했습니다.",
            suggested_action="잠시 후 다시 시도해 주세요.",
            failed_tool=tool_name,
        )

    def feedback_text(error: str | None, context: RepairContext | None) -> str | None:
        if error is None:
            return None
        if repair_engine() == "v2" and context is not None:
            return render_repair_feedback(context)
        if error == EMPTY_RESULT_ERROR:
            return empty_result_feedback
        return error

    async def agent(state: RetryAgentState) -> dict:
        previous_message = state["messages"][-1] if state["messages"] else None
        try:
            initial_query = state.get("initial_query")
            if state.get("attempt_count", 0) == 0 and initial_query is not None:
                query_text = initial_query
            else:
                query_text = await generate(
                    state,
                    previous_message["content"] if previous_message else None,
                    feedback_text(state.get("error"), state.get("repair_context")),
                )
        except QueryInfrastructureError:
            raise
        except Exception as exc:
            # 쿼리 생성 모델을 호출할 수 없는 상태에서 실패 설명 LLM을 다시
            # 호출하지 않는다. 공개 503 계약으로 즉시 fail-closed한다.
            logger.error("%s: 쿼리 생성 실패: %s", label, exc, exc_info=True)
            raise QueryInfrastructureError() from exc
        attempt = state.get("attempt_count", 0) + 1
        emit_event(
            "query.generated",
            "query",
            tool=tool_name,
            attempt=attempt,
            max_attempts=MAX_ATTEMPTS,
            query_hash=query_hash(query_text),
            query_length=len(query_text),
            generated_query=redact_query(query_text),
        )
        emit_event(
            "query.attempt.started",
            "query",
            tool=tool_name,
            attempt=attempt,
            max_attempts=MAX_ATTEMPTS,
        )
        return {
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": query_text},
            ],
            "attempt_count": attempt,
            # RetryAgentState.retryable은 필수 필드지만 그래프 진입 시점의
            # 초기 상태(graph.py)에는 없을 수 있다. agent는 tools보다 항상
            # 먼저 실행되므로 여기서 기본값을 채워 should_retry가 첫 턴부터
            # state["retryable"]을 안전하게 읽을 수 있게 한다.
            "retryable": state.get("retryable", False),
        }

    async def tools(state: RetryAgentState) -> dict:
        query_text = state["messages"][-1]["content"]
        attempts = state.get("attempts", [])

        def failure(
            message: str, *, retryable: bool, safe_failure: QueryFailure
        ) -> dict:
            issue_code = safe_failure.get("code", "INTERNAL_QUERY_FAILURE")
            failed_query = redact_query(query_text)
            QUERY_ATTEMPTS.labels(tool_name, issue_code, "failure").inc()
            emit_event(
                "query.attempt.completed",
                "query",
                level="WARNING",
                force=True,
                tool=tool_name,
                outcome="failure",
                attempt=state.get("attempt_count", 0),
                max_attempts=MAX_ATTEMPTS,
                issue_code=issue_code,
                failure_reason=safe_failure.get("user_safe_reason"),
                failure_stage=safe_failure.get("stage"),
                failure_category=safe_failure.get("category"),
                retryable=retryable,
                failed_query=failed_query,
            )
            emit_event(
                "repair.decision.made",
                "repair",
                force=True,
                tool=tool_name,
                outcome="failure",
                decision=(
                    "retry"
                    if retryable and state.get("attempt_count", 0) < MAX_ATTEMPTS
                    else "stop"
                ),
                issue_code=issue_code,
                failure_reason=safe_failure.get("user_safe_reason"),
                failed_query=failed_query,
                attempt=state.get("attempt_count", 0),
                repair_engine=repair_engine(),
            )
            previous_codes = state.get("repair_issue_codes", [])
            context = make_repair_context(
                tool=tool_name,
                attempt=state.get("attempt_count", 0),
                failure=safe_failure,
                exact_failure=message,
                required_outputs=state.get("required_outputs", []),
                repair_instructions=instructions_by_code.get(
                    issue_code,
                    instructions_by_code.get("default", ()),
                ),
                previous_issue_codes=previous_codes,
            )
            return {
                "error": message,
                "result": None,
                "attempts": [*attempts, {"query": query_text, "error": message}],
                "retryable": retryable,
                "failure": safe_failure,
                "failed_query": failed_query,
                "repair_context": context,
                "repair_issue_codes": context["previous_issue_codes"],
            }

        if guard is not None:
            try:
                guard_result = guard(query_text)
            except Exception as exc:
                # 가드 자체의 파싱 버그(예: 깊게 중첩된 쿼리에서 RecursionError)가
                # 그래프 밖으로 raise되는 걸 막는 안전망 - execute() 주변의
                # 미분류 예외 처리와 동일한 성격으로 다룬다.
                logger.error(
                    "%s: 쿼리 가드 실행 중 예외(재시도 대상 아님, 안전망): %s",
                    label,
                    exc,
                    exc_info=True,
                )
                return failure(
                    "쿼리 검증 중 오류가 발생했습니다.",
                    retryable=False,
                    safe_failure=_internal_validation_error(),
                )

            # 감사 로그 파일 쓰기(동기 I/O)가 이벤트 루프를 막지 않도록 스레드풀로 뺀다.
            # 감사 로그는 부가 기능이라 여기서 예외가 나도(디스크 풀/권한 문제 등)
            # 실제 가드 판정·쿼리 실행 흐름을 막아서는 안 된다(안전망).
            try:
                await run_in_threadpool(
                    log_guard_decision,
                    query_type=label,
                    intent=state["query"],
                    decision="ALLOW" if guard_result.allowed else "BLOCK",
                    stage="pre_execution_guard",
                    reason=guard_result.reason_code,
                )
            except Exception as exc:
                logger.error("%s: 감사 로그 기록 실패(무시하고 계속): %s", label, exc)
            if not guard_result.allowed:
                logger.warning(
                    "%s: 쿼리 가드 차단(%s): %s",
                    label,
                    guard_result.reason_code,
                    guard_result.reason_detail,
                )
                # 상세 사유(reason_detail)는 로그에만 남긴다 - attempts/error를
                # 거쳐 API 응답(final_answer)까지 그대로 노출되면, 사용자가 재시도
                # 3번 동안 실제 테이블/라벨명을 하나씩 유추해낼 수 있기 때문이다.
                return failure(
                    f"쿼리가 안전 정책에 의해 차단되었습니다({guard_result.reason_code}).",
                    retryable=guard_result.reason_code
                    not in _NON_RETRYABLE_GUARD_REASONS,
                    safe_failure=make_query_failure(
                        code="QUERY_POLICY_BLOCKED",
                        stage="validation",
                        category="POLICY_BLOCKED",
                        kind="user_correctable",
                        retryable=False,
                        user_safe_reason="생성된 조회가 데이터 안전 정책을 통과하지 못했습니다.",
                        suggested_action=(
                            "데이터를 변경하지 않는 조회 요청으로 질문을 바꿔 주세요."
                        ),
                        failed_tool=tool_name,
                    ),
                )

        if query_contract_error is not None:
            contract_error = query_contract_error(
                query_text, state.get("required_outputs", [])
            )
            if contract_error is not None:
                return failure(
                    contract_error,
                    retryable=True,
                    safe_failure=make_query_failure(
                        code=contract_issue_code,
                        stage="validation",
                        category="QUERY_INVALID",
                        kind="user_correctable",
                        retryable=True,
                        user_safe_reason="질문에 필요한 결과 형식을 만족하는 조회를 만들지 못했습니다.",
                        suggested_action="필요한 항목과 조회 조건을 더 구체적으로 지정해 주세요.",
                        failed_tool=tool_name,
                    ),
                )

        try:
            executed = await execute(query_text)
        except retryable_exceptions as exc:
            logger.warning("%s: 실행 오류(재시도 대상): %s", label, exc, exc_info=True)
            is_timeout = (
                "timeout" in type(exc).__name__.lower()
                or "canceled" in type(exc).__name__.lower()
            )
            safe_failure = (
                classify_execution_error(exc)
                if classify_execution_error is not None
                else make_query_failure(
                    code="QUERY_TIMEOUT" if is_timeout else "QUERY_EXECUTION_FAILED",
                    stage="execution",
                    category="TIMEOUT" if is_timeout else "QUERY_INVALID",
                    kind="user_correctable",
                    retryable=True,
                    user_safe_reason=(
                        "조회가 제한 시간 안에 완료되지 않았습니다."
                        if is_timeout
                        else "생성된 조회를 정상적으로 실행하지 못했습니다."
                    ),
                    suggested_action=(
                        "조회 기간이나 대상 범위를 줄여 다시 질문해 주세요."
                        if is_timeout
                        else "조회 대상과 조건을 더 구체적으로 지정해 주세요."
                    ),
                    failed_tool=tool_name,
                )
            )
            return failure(
                "쿼리를 실행하지 못했습니다.",
                retryable=True,
                safe_failure=safe_failure,
            )
        except connection_exceptions as exc:
            logger.error(
                "%s: 접속 오류(재시도 대상 아님): %s", label, exc, exc_info=True
            )
            return failure(
                "접속 오류가 발생했습니다.",
                retryable=False,
                safe_failure=make_query_failure(
                    code="INFRASTRUCTURE_UNAVAILABLE",
                    stage="execution",
                    category="CONNECTION_ERROR",
                    kind="infrastructure",
                    retryable=True,
                    user_safe_reason="조회 시스템에 일시적으로 연결할 수 없습니다.",
                    suggested_action="잠시 후 다시 시도해 주세요.",
                    failed_tool=tool_name,
                ),
            )
        except Exception as exc:
            # 화이트리스트 밖 예외: 예상 못 한 버그가 재시도 뒤에 숨는 것을 막기 위한 안전망
            logger.error(
                "%s: 미분류 예외(재시도 대상 아님, 안전망): %s",
                label,
                exc,
                exc_info=True,
            )
            return failure(
                "질의 실행 중 내부 오류가 발생했습니다.",
                retryable=False,
                safe_failure=make_query_failure(
                    code="INTERNAL_QUERY_FAILURE",
                    stage="execution",
                    category="INTERNAL_QUERY_FAILURE",
                    kind="internal",
                    retryable=False,
                    user_safe_reason="질의 실행 중 내부 오류가 발생했습니다.",
                    suggested_action="잠시 후 다시 시도해 주세요.",
                    failed_tool=tool_name,
                ),
            )

        if (
            isinstance(executed, dict)
            and isinstance(executed.get("rows"), list)
            and isinstance(executed.get("truncated"), bool)
        ):
            batch = QueryResultBatch(
                rows=executed["rows"],
                truncated=executed["truncated"],
            )
            result = batch["rows"]
            truncated = batch["truncated"]
        else:
            # 주입형 테스트 executor와 기존 사용자 정의 executor는 list 반환도
            # 계속 지원한다. production executor는 위 QueryResultBatch를 사용한다.
            result = executed
            truncated = False

        if not result:
            new_attempts = [
                *attempts,
                {"query": query_text, "error": EMPTY_RESULT_ERROR},
            ]
            can_retry_empty = (
                not state.get("empty_retried", False)
                and state.get("attempt_count", 0) < MAX_ATTEMPTS
            )
            if can_retry_empty:
                logger.info("%s: 결과 없음 - 1회 한정 재시도", label)
                empty_issue_code = (
                    "SQL_EMPTY_RESULT" if tool_name == "sql" else "CYPHER_EMPTY_RESULT"
                )
                empty_failure = make_query_failure(
                    code=empty_issue_code,
                    stage="result",
                    category="EMPTY_RESULT",
                    kind="user_correctable",
                    retryable=True,
                    user_safe_reason="조회가 정상 실행됐지만 결과가 없습니다.",
                    suggested_action="조회 조건을 확인해 주세요.",
                    failed_tool=tool_name,
                )
                previous_codes = state.get("repair_issue_codes", [])
                context = make_repair_context(
                    tool=tool_name,
                    attempt=state.get("attempt_count", 0),
                    failure=empty_failure,
                    exact_failure=empty_result_feedback,
                    required_outputs=state.get("required_outputs", []),
                    repair_instructions=instructions_by_code.get(
                        empty_issue_code,
                        instructions_by_code.get("default", ()),
                    ),
                    previous_issue_codes=previous_codes,
                )
                return {
                    "error": EMPTY_RESULT_ERROR,
                    "result": result,
                    "attempts": new_attempts,
                    "retryable": True,
                    "empty_retried": True,
                    "truncated": truncated,
                    "failure": None,
                    "repair_context": context,
                    "repair_issue_codes": context["previous_issue_codes"],
                }
            reason = _classify_empty_result(attempts)
            logger.info("%s: 결과 없음으로 최종 수용 (%s)", label, reason)
            return {
                "result": result,
                "error": None,
                "empty_reason": reason,
                "attempts": new_attempts,
                "retryable": False,
                "truncated": truncated,
                "failure": None,
                "generated_query": redact_query(query_text),
            }

        required_outputs = state.get("required_outputs", [])
        for row_index, row in enumerate(result):
            missing = [
                alias
                for alias in required_outputs
                if not isinstance(row, dict) or alias not in row
            ]
            if missing:
                aliases = ", ".join(missing)
                return failure(
                    f"결과의 {row_index}번 행에 필수 alias가 없습니다: {aliases}",
                    retryable=True,
                    safe_failure=make_query_failure(
                        code=contract_issue_code,
                        stage="validation",
                        category="QUERY_INVALID",
                        kind="user_correctable",
                        retryable=True,
                        user_safe_reason="질문에 필요한 항목을 조회 결과에서 확인하지 못했습니다.",
                        suggested_action="필요한 결과 항목을 명확하게 지정해 다시 질문해 주세요.",
                        failed_tool=tool_name,
                    ),
                )

        QUERY_ATTEMPTS.labels(tool_name, "none", "success").inc()
        if state.get("attempt_count", 0) > 1:
            REPAIRS.labels(tool_name, "none", "success", repair_engine()).inc()
            emit_event(
                "repair.completed",
                "repair",
                force=True,
                tool=tool_name,
                attempt=state.get("attempt_count", 0),
                repair_engine=repair_engine(),
            )
        emit_event(
            "query.attempt.completed",
            "query",
            tool=tool_name,
            attempt=state.get("attempt_count", 0),
            max_attempts=MAX_ATTEMPTS,
            row_count=len(result),
            generated_query=redact_query(query_text),
        )
        return {
            "result": result,
            "error": None,
            "empty_reason": None,
            "attempts": [*attempts, {"query": query_text, "error": None}],
            "retryable": False,
            "truncated": truncated,
            "failure": None,
            "generated_query": redact_query(query_text),
        }

    def should_retry(state: RetryAgentState) -> str:
        if state["error"] is None:
            return "done"
        if not state.get("retryable", False):
            return "done"
        if state["attempt_count"] >= MAX_ATTEMPTS:
            failure: QueryFailure | dict[str, Any] = state.get("failure") or {}
            issue_code = failure.get("code", "INTERNAL_QUERY_FAILURE")
            REPAIR_EXHAUSTED.labels(tool_name, issue_code).inc()
            REPAIRS.labels(tool_name, issue_code, "failure", repair_engine()).inc()
            emit_event(
                "repair.exhausted",
                "repair",
                level="ERROR",
                force=True,
                tool=tool_name,
                outcome="failure",
                issue_code=issue_code,
                failure_reason=failure.get("user_safe_reason"),
                failure_stage=failure.get("stage"),
                failure_category=failure.get("category"),
                failed_query=state.get("failed_query"),
                attempt=state["attempt_count"],
                max_attempts=MAX_ATTEMPTS,
                repair_engine=repair_engine(),
            )
            return "done"
        return "retry"

    graph = StateGraph(RetryAgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "tools")
    graph.add_conditional_edges("tools", should_retry, {"retry": "agent", "done": END})
    return graph.compile()
