"""사용자의 자연어 요청을 읽기·쓰기·확인 필요로 분류한다."""

import json
import os
import re
from collections.abc import Callable
from typing import Any, Literal, cast

from orchestrator.state import (
    DetectedAction,
    NaturalGuardNodeResult,
    NaturalGuardResult,
    NaturalIntent,
    OrchestratorState,
)

_WRITE_COMMAND_PATTERNS = (
    re.compile(
        r"(삭제|제거|지워|없애|수정|변경|갱신|바꿔|추가|등록|생성|만들어)"
        r"\s*(해\s*줘|해주세요|해라|하라|해|시켜줘|해줘)",
        re.I,
    ),
    re.compile(
        r"\b(delete|remove|erase|update|modify|edit|change|insert|create|add|"
        r"register|alter|truncate|grant|revoke)\b",
        re.I,
    ),
)
_READ_REQUEST_PATTERN = re.compile(
    r"(알려|보여|조회|검색|찾아|확인|계산|비교|분석|몇\s*개|얼마|"
    r"\b(select|show|find|search|get|list)\b)",
    re.I,
)

_SYSTEM_PROMPT = """당신은 읽기 전용 제조 데이터 챗봇의 요청 분류기입니다.
사용자가 데이터를 조회·계산·분석하려는지, 실제 데이터나 스키마를 변경하려는지
판단합니다. '삭제된 제품을 보여줘'는 READ이고 '제품을 삭제해줘'는 DELETE입니다.
'변경된 가격을 알려줘'는 READ이고 '가격을 변경해줘'는 UPDATE입니다.
확실하지 않으면 UNKNOWN으로 분류합니다. 아래 JSON 형식만 반환합니다.
{"intent":"READ|CREATE|UPDATE|DELETE|SCHEMA_CHANGE|PERMISSION_CHANGE|UNKNOWN",
 "confidence":0.0,"reason":"짧은 판정 이유"}"""


def _action_intent(actions: list[DetectedAction]) -> NaturalIntent:
    blocked = [item["action_type"] for item in actions if item["default_policy"] == "BLOCK"]
    return blocked[0] if blocked else "UNKNOWN"


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
    elif intent in {
        "CREATE",
        "UPDATE",
        "DELETE",
        "SCHEMA_CHANGE",
        "PERMISSION_CHANGE",
    } and confidence >= 0.8:
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
            intent = _action_intent(actions)
            if intent == "UNKNOWN":
                intent = "UPDATE"
            result: NaturalGuardResult = {
                "decision": "BLOCK_WRITE",
                "intent": intent,
                "reason": "데이터 또는 스키마 변경을 요청한 문장입니다.",
                "confidence": 1.0,
            }
        elif _READ_REQUEST_PATTERN.search(original):
            result = {
                "decision": "ALLOW_READ",
                "intent": "READ",
                "reason": "데이터 조회·계산·분석 요청입니다.",
                "confidence": 1.0,
            }
        else:
            try:
                result = _classify_with_llm(original, normalized, openai_client)
            except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                result = {
                    "decision": "NEEDS_CLARIFICATION",
                    "intent": "UNKNOWN",
                    "reason": "요청 의도를 안전하게 확인하지 못했습니다.",
                    "confidence": 0.0,
                }

        allowed = result["decision"] == "ALLOW_READ"
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
