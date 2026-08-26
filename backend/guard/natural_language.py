"""사용자의 자연어 요청을 읽기·쓰기·확인 필요로 분류한다."""

import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any, Literal, cast

from openai import APIError

from orchestrator.state import (
    DetectedAction,
    NaturalGuardNodeResult,
    NaturalGuardResult,
    NaturalIntent,
    OrchestratorState,
)

logger = logging.getLogger(__name__)

_WRITE_COMMAND_PATTERNS = (
    re.compile(
        r"(삭제|제거|지워|없애|수정|변경|갱신|바꿔|추가|등록|생성|만들어)"
        r"\s*(해\s*줘|해주세요|해라|하라|해|시켜줘|해줘|줘|주세요)",
        re.I,
    ),
    # English write verbs are commands only at the start of a sentence/clause.
    # This keeps noun phrases such as "price change history" on the classifier path.
    re.compile(
        r"(?:^|[.!?;,]\s*|\b(?:and|then)\s+)"
        r"(?:please\s+)?"
        r"(?:change|update|modify|edit|delete|remove|erase|create|insert|add|"
        r"register|drop|alter|truncate|grant|revoke)\b",
        re.I,
    ),
    # 채팅에서는 "새 제품도 등록"처럼 요청형 어미 없이 쓰기 동작만으로
    # 명령을 끝내기도 한다. "등록된 제품"처럼 뒤에 수식어가 이어지는 표현은
    # 매칭하지 않고, 문장 끝에 놓인 쓰기 동작만 명확한 명령으로 처리한다.
    re.compile(r"(삭제|제거|수정|변경|갱신|추가|등록|생성)\s*[.!?]?\s*$", re.I),
)

_SYSTEM_PROMPT = """당신은 읽기 전용 제조 데이터 챗봇의 요청 분류기입니다.
사용자가 데이터를 조회·계산·분석하려는지, 실제 데이터나 스키마를 변경하려는지
판단합니다. '삭제된 제품을 보여줘'는 READ이고 '제품을 삭제해줘'는 DELETE입니다.
'변경된 가격을 알려줘'는 READ이고 '가격을 변경해줘'는 UPDATE입니다.
확실하지 않으면 UNKNOWN으로 분류합니다. 아래 JSON 형식만 반환합니다.
{"intent":"READ|CREATE|UPDATE|DELETE|SCHEMA_CHANGE|PERMISSION_CHANGE|UNKNOWN",
 "confidence":0.0,"reason":"짧은 판정 이유"}"""


def _action_intent(actions: list[DetectedAction]) -> NaturalIntent:
    blocked = [
        item["action_type"] for item in actions if item["default_policy"] == "BLOCK"
    ]
    return blocked[0] if blocked else "UNKNOWN"


def _explicit_write_intent(query: str, actions: list[DetectedAction]) -> NaturalIntent:
    intent = _action_intent(actions)
    if intent != "UNKNOWN":
        return intent
    if re.search(r"\b(drop|alter|truncate)\b|테이블|인덱스|구조", query, re.I):
        return "SCHEMA_CHANGE"
    if re.search(r"삭제|제거|지워|없애|\b(delete|remove|erase)\b", query, re.I):
        return "DELETE"
    if re.search(
        r"추가|등록|생성|만들어|\b(insert|create|add|register)\b", query, re.I
    ):
        return "CREATE"
    if re.search(r"권한|\b(grant|revoke)\b", query, re.I):
        return "PERMISSION_CHANGE"
    return "UPDATE"


def _classify_with_llm(
    query: str, normalized_query: str, openai_client: Any
) -> NaturalGuardResult:
    response = openai_client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"원문: {query}\n정규화문: {normalized_query}",
            },
        ],
        response_format={"type": "json_object"},
    )
    payload = json.loads(response.choices[0].message.content)
    intent_value = str(payload.get("intent", "UNKNOWN"))
    allowed_intents = {
        "READ",
        "CREATE",
        "UPDATE",
        "DELETE",
        "SCHEMA_CHANGE",
        "PERMISSION_CHANGE",
        "UNKNOWN",
    }
    intent = cast(
        NaturalIntent,
        intent_value if intent_value in allowed_intents else "UNKNOWN",
    )
    confidence = float(payload.get("confidence", 0.0))
    reason = str(payload.get("reason", "요청 의도를 확정하지 못했습니다."))
    if intent == "READ" and confidence >= 0.8:
        decision: Literal["ALLOW_READ", "BLOCK_WRITE", "NEEDS_CLARIFICATION"] = (
            "ALLOW_READ"
        )
    elif (
        intent
        in {
            "CREATE",
            "UPDATE",
            "DELETE",
            "SCHEMA_CHANGE",
            "PERMISSION_CHANGE",
        }
        and confidence >= 0.8
    ):
        decision = "BLOCK_WRITE"
    else:
        intent = "UNKNOWN"
        decision = "NEEDS_CLARIFICATION"
    return {
        "decision": decision,
        "intent": intent,
        "reason": reason,
        "confidence": confidence,
    }


def make_natural_language_guard_node(
    openai_client: Any,
) -> Callable[[OrchestratorState], NaturalGuardNodeResult]:
    def validate_natural_language(state: OrchestratorState) -> NaturalGuardNodeResult:
        original = state["query"]
        normalized = state.get("normalized_query") or original
        actions = state.get("detected_actions", [])
        if any(pattern.search(original) for pattern in _WRITE_COMMAND_PATTERNS):
            intent = _explicit_write_intent(original, actions)
            result: NaturalGuardResult = {
                "decision": "BLOCK_WRITE",
                "intent": intent,
                "reason": "데이터 또는 스키마 변경을 요청한 문장입니다.",
                "confidence": 1.0,
            }
        else:
            # 규칙에 없는 동사와 축약형을 조회로 오인하지 않도록 모든 나머지
            # 요청은 분류기가 명시적으로 READ라고 확인해야 실행한다.
            try:
                result = _classify_with_llm(original, normalized, openai_client)
            except (
                APIError,
                AttributeError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                result = {
                    "decision": "NEEDS_CLARIFICATION",
                    "intent": "UNKNOWN",
                    "reason": "요청 의도를 안전하게 확인하지 못했습니다.",
                    "confidence": 0.0,
                }

        allowed = result["decision"] == "ALLOW_READ"
        log = logger.info if allowed else logger.warning
        log(
            "natural guard: decision=%s intent=%s reason=%s detected_actions=%s",
            result["decision"],
            result["intent"],
            result["reason"],
            actions,
        )
        response: NaturalGuardNodeResult = {
            "natural_guard": result,
            "execution_allowed": allowed,
        }
        if not allowed:
            response["error"] = (
                "현재 서비스는 데이터 조회만 지원합니다."
                if result["decision"] == "BLOCK_WRITE"
                else "조회하려는 내용이 무엇인지 조금 더 구체적으로 입력해주세요."
            )
        return response

    return validate_natural_language


def route_after_natural_guard(state: OrchestratorState) -> str:
    """명시적으로 허용된 요청만 기존 쿼리 생성 흐름으로 보낸다."""
    if state.get("execution_allowed") is True:
        return "continue"
    return "stop"
