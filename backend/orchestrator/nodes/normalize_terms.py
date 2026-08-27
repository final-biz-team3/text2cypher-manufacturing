"""사용자 질문을 제조 도메인의 표준 용어로 정규화한다."""

import logging
from collections.abc import Callable
from time import perf_counter
from typing import cast

from ontology.models import TermDictionary
from ontology.normalizer import compile_term_dictionary, normalize_query
from orchestrator.state import NormalizationNodeResult, OrchestratorState

logger = logging.getLogger(__name__)


def make_normalize_terms_node(
    dictionary: TermDictionary,
) -> Callable[[OrchestratorState], NormalizationNodeResult]:
    compiled_index = compile_term_dictionary(dictionary)

    def normalize_terms(state: OrchestratorState) -> NormalizationNodeResult:
        started_at = perf_counter()
        result = cast(
            NormalizationNodeResult,
            normalize_query(state["query"], dictionary, compiled_index=compiled_index),
        )
        result["normalization_elapsed_ms"] = (perf_counter() - started_at) * 1000
        if result["normalization_status"] == "NEEDS_CLARIFICATION":
            result["execution_allowed"] = False
            result["error"] = "의미가 여러 개인 용어를 확인해주세요."
            logger.warning(
                "normalize_terms blocked: ambiguous_terms=%s reason=%s elapsed_ms=%.3f",
                result["ambiguous_terms"],
                result["error"],
                result["normalization_elapsed_ms"],
            )
        else:
            result["execution_allowed"] = True
            logger.info(
                "normalize_terms: matched_terms=%s detected_actions=%s elapsed_ms=%.3f",
                result["matched_terms"],
                result["detected_actions"],
                result["normalization_elapsed_ms"],
            )
        return result

    return normalize_terms


def route_after_normalization(state: OrchestratorState) -> str:
    if state.get("execution_allowed") is True:
        return "continue"
    return "stop"
