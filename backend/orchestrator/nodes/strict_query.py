"""이전 질의 경로가 유효한 결과를 내지 못하면 깨끗한 #61 경로로 전환한다."""

import asyncio
import logging
import math
import os
from copy import deepcopy
from time import perf_counter
from typing import Any

from agents.generator import DEFAULT_REASONING_EFFORT, ReasoningEffort
from orchestrator.state import OrchestratorState
from strict_query.pipeline import build_strict_query

logger = logging.getLogger(__name__)


def _rejection_reason(result: dict[str, Any]) -> str | None:
    if result.get("query_failure") or result.get("error"):
        return "query_failure"
    composed = result.get("composed_result")
    if not isinstance(composed, dict) or composed.get("error"):
        return "composition_failure"
    if composed.get("truncated"):
        return "truncated"
    rows = composed.get("rows") or []
    sections = composed.get("sections") or {}
    if not rows and not any(section.get("rows") for section in sections.values()):
        return "empty_result"
    return None


def make_strict_query_node(
    openai_client: Any,
    pool: Any,
    *,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
) -> Any:
    initial_error = None
    try:
        run_strict = build_strict_query(
            openai_client, pool, reasoning_effort=reasoning_effort
        )
    except Exception as exc:
        initial_error = f"initialization:{type(exc).__name__}"
        run_strict = None
        logger.warning("엄격한 질의 경로 초기화 실패: %s", type(exc).__name__)
    timeout = float(os.getenv("STRICT_QUERY_TIMEOUT_SECONDS", "60"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("STRICT_QUERY_TIMEOUT_SECONDS must be positive")

    async def strict_query(state: OrchestratorState) -> dict[str, Any]:
        started = perf_counter()
        query_input: dict[str, Any] = {"query": state["query"]}
        if state.get("confirmed_entity") is not None:
            query_input["confirmed_entity"] = deepcopy(state["confirmed_entity"])
        result: dict[str, Any] = {}
        reason: str | None
        try:
            if initial_error is not None:
                reason = initial_error
            else:
                async with asyncio.timeout(timeout):
                    result = await run_strict(query_input)
                reason = _rejection_reason(result)
        except TimeoutError:
            reason = "timeout"
        except Exception as exc:
            # 중간 상태/원본 오류는 다음 질의 경로와 사용자 답변에 전달하지 않는다.
            reason = type(exc).__name__
            logger.warning("엄격한 질의 경로 실패: %s", reason)
        diagnostic = {
            "status": "accepted" if reason is None else "fallback",
            "reason": reason,
            "elapsedMs": round((perf_counter() - started) * 1000, 3),
        }
        if reason is not None:
            return {"query_strategy": "pr61", "strict_attempt": diagnostic}
        accepted = {
            key: value
            for key, value in result.items()
            if key in OrchestratorState.__annotations__
            and key
            not in {"query", "confirmed_entity", "final_answer", "answer_metadata"}
        }
        return {**accepted, "query_strategy": "strict", "strict_attempt": diagnostic}

    return strict_query
