"""사용자 질문을 제조 도메인의 표준 용어로 정규화한다."""

from collections.abc import Callable

from ontology.models import TermDictionary
from ontology.normalizer import normalize_query
from orchestrator.state import NormalizationResult, OrchestratorState


def make_normalize_terms_node(
    dictionary: TermDictionary,
) -> Callable[[OrchestratorState], NormalizationResult]:
    def normalize_terms(state: OrchestratorState) -> NormalizationResult:
        return normalize_query(state["query"], dictionary)

    return normalize_terms
