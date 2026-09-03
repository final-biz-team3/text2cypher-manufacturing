"""SQL과 Cypher Agent가 공유하는 프롬프트 메시지 조립을 테스트한다."""

import json
from datetime import date
from decimal import Decimal

import neo4j.time
import pytest

from agents.prompt import build_prompt_messages


def test_build_prompt_messages_separates_trusted_context_and_user_input() -> None:
    """지침·스키마·업무 규칙은 system, 질의·entity는 user에 배치한다."""
    messages = build_prompt_messages(
        instructions="읽기 전용 쿼리를 생성하세요.",
        query="제품의 재고를 알려줘.",
        entity={"productId": 985, "productName": "Paint - Black"},
        schema_text="Product {productId: INTEGER}",
        business_rules=["재고는 수량의 합계다.", "재고가 없으면 0이다."],
    )

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == """읽기 전용 쿼리를 생성하세요.

Schema:
Product {productId: INTEGER}

Business rules:
- 재고는 수량의 합계다.
- 재고가 없으면 0이다."""
    assert messages[1]["role"] == "user"
    assert json.loads(messages[1]["content"]) == {
        "query": "제품의 재고를 알려줘.",
        "entity": {"productId": 985, "productName": "Paint - Black"},
    }
    assert "제품의 재고를 알려줘." in messages[1]["content"]


def test_build_prompt_messages_includes_feedback_with_empty_previous_error() -> None:
    """previous_error가 빈 문자열이어도(메시지 없는 예외) previous_query가 있으면
    재시도 피드백 섹션을 누락하지 않는다."""
    messages = build_prompt_messages(
        instructions="쿼리를 생성하세요.",
        query="제품 수를 알려줘.",
        entity=None,
        schema_text="Product {}",
        previous_query="SELECT * FROM bad",
        previous_error="",
    )

    assert "Previous attempt failed" in messages[0]["content"]
    assert "SELECT * FROM bad" in messages[0]["content"]


def test_build_prompt_messages_omits_empty_business_rules_section() -> None:
    """업무 규칙이 없으면 불필요한 영역을 만들지 않는다."""
    messages = build_prompt_messages(
        instructions="쿼리를 생성하세요.",
        query="전체 개수를 알려줘.",
        entity=None,
        schema_text="Product {}",
    )

    assert messages[0]["content"] == """쿼리를 생성하세요.

Schema:
Product {}"""
    assert json.loads(messages[1]["content"]) == {
        "query": "전체 개수를 알려줘.",
        "entity": None,
    }


def test_build_prompt_messages_includes_aligned_input_bindings() -> None:
    """선행 결과에서 추출한 배열과 행 대응·중복 보존 규칙을 전달한다."""
    messages = build_prompt_messages(
        instructions="쿼리를 생성하세요.",
        query="해당 부품의 재고를 알려줘.",
        entity={"supplierId": 2},
        schema_text="Product {}",
        input_bindings={
            "componentIds": [7, 7, 9],
            "supplierIds": [2, 3, 3],
        },
    )

    assert "aligned by row index" in messages[0]["content"]
    assert "may contain duplicates" in messages[0]["content"]
    assert json.loads(messages[1]["content"])["inputBindings"] == {
        "componentIds": [7, 7, 9],
        "supplierIds": [2, 3, 3],
    }


def test_build_prompt_messages_keeps_original_query_authoritative_over_scope() -> None:
    """HYBRID source scope는 실행 범위일 뿐 원질문의 의미를 덮어쓰지 않는다."""
    messages = build_prompt_messages(
        instructions="쿼리를 생성하세요.",
        query="부품별 재고 부족량을 알려줘.",
        source_scope="경로 정보와 depth를 포함해 영향 부품을 찾는다.",
        entity={"productId": 7},
        schema_text="Product {}",
        required_outputs=["componentId"],
    )

    system = messages[0]["content"]
    assert "The original query is authoritative" in system
    assert "Source scope only narrows" in system
    assert "Required output aliases" in system
    assert json.loads(messages[1]["content"]) == {
        "query": "부품별 재고 부족량을 알려줘.",
        "entity": {"productId": 7},
        "sourceScope": "경로 정보와 depth를 포함해 영향 부품을 찾는다.",
    }


def test_build_prompt_messages_serializes_database_binding_scalars() -> None:
    """Decimal과 표준/Neo4j 날짜 타입을 손실 없는 문자열로 전달한다."""
    messages = build_prompt_messages(
        instructions="쿼리를 생성하세요.",
        query="조건에 맞는 제품을 알려줘.",
        entity=None,
        schema_text="Product {}",
        input_bindings={
            "prices": [Decimal("12.50")],
            "dates": [date(2026, 8, 28)],
            "timestamps": [neo4j.time.DateTime(2026, 8, 28, 12, 34, 56)],
        },
    )

    assert json.loads(messages[1]["content"])["inputBindings"] == {
        "prices": ["12.50"],
        "dates": ["2026-08-28"],
        "timestamps": ["2026-08-28T12:34:56.000000000"],
    }


def test_build_prompt_messages_rejects_unsupported_binding_value() -> None:
    """지원하지 않는 객체를 불명확한 repr 문자열로 조용히 변환하지 않는다."""
    with pytest.raises(TypeError, match="not JSON serializable"):
        build_prompt_messages(
            instructions="쿼리를 생성하세요.",
            query="질문",
            entity=None,
            schema_text="Product {}",
            input_bindings={"values": [object()]},
        )
