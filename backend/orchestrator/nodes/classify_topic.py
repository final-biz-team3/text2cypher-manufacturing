"""제조 데이터 질문인지 판별해 무관한 질문을 조기에 차단한다."""

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

from orchestrator.guards.audit import log_guard_decision
from orchestrator.query_failures import make_query_failure
from orchestrator.state import OrchestratorState, QueryFailure

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """당신은 제조 데이터 질의 시스템의 주제 판별기입니다.
사용자 질문이 제조 데이터(제품, 재고, 부품, 공급업체, 생산, 작업지시, 공정, 폐기 등)
조회·분석과 관련 있는지만 판단합니다.

- 관련 있으면 ON_TOPIC만 출력합니다.
- 인사, 날씨·감정 등 잡담, 시스템 정체성 질문처럼 무관하면 OFF_TOPIC만 출력합니다.
- 설명이나 다른 텍스트 없이 이 두 단어 중 하나만 반환합니다."""


def _off_topic_failure() -> QueryFailure:
    return make_query_failure(
        code="OFF_TOPIC",
        stage="validation",
        category="OFF_TOPIC",
        kind="user_correctable",
        retryable=False,
        user_safe_reason="제조 데이터와 관련된 질문을 입력해 주세요.",
        suggested_action="제품, 재고, 부품, 공급업체, 생산 정보 등을 조회하고 분석할 수 있습니다.",
    )


def make_classify_topic_node(
    openai_client: Any,
) -> Callable[[OrchestratorState], Awaitable[dict]]:
    """저비용 LLM 호출 1회로 도메인 관련 여부만 판별한다. ON_TOPIC/OFF_TOPIC
    단어 하나만 고르면 되는 단순 분류라 reasoning_effort는 항상 "none"으로
    고정한다(resolve_entity의 엔티티 추출 호출과 동일한 선택). 응답이 정확히
    OFF_TOPIC이 아니면(모호한 응답, 빈 choices 포함) 차단하지 않고 통과시킨다
    - 정상 질문을 잘못 막는 것이 더 나쁘다(fail-open). 호출 자체가
    실패해도(provider 오류, timeout 등) 같은 이유로 막지 않는다 - 판별기
    장애가 전체 조회 경로를 502로 끌고 내려가면 안 된다."""

    async def classify_topic(state: OrchestratorState) -> dict:
        try:
            response = await openai_client.chat.completions.create(
                model=os.environ["OPENAI_MODEL"],
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": state["query"]},
                ],
                reasoning_effort="none",
            )
            content = response.choices[0].message.content if response.choices else None
        except Exception:
            logger.exception("주제 판별 LLM 호출 실패, fail-open으로 통과시킨다")
            content = None
        blocked = isinstance(content, str) and content.strip() == "OFF_TOPIC"
        try:
            await run_in_threadpool(
                log_guard_decision,
                query_type="topic",
                intent=state["query"],
                decision="BLOCK" if blocked else "ALLOW",
                stage="topic_classification",
                reason="OFF_TOPIC" if blocked else None,
            )
        except Exception as exc:
            logger.error("주제 판별 감사 로그 기록 실패(무시하고 계속): %s", exc)
        if blocked:
            return {"query_failure": _off_topic_failure()}
        return {"query_failure": None}

    return classify_topic
