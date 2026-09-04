from core.observability.pricing import PRICING_VERSION, estimate_cost_usd


def test_short_context_luna_cost_uses_all_token_classes(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS", "272000")
    cost, tier = estimate_cost_usd(
        "gpt-5.6-luna",
        input_tokens=1_000_000,
        cached_input_tokens=100_000,
        cache_write_tokens=50_000,
        output_tokens=10_000,
    )
    # 입력 토큰이 기준보다 크므로 long 단가를 적용한다.
    assert tier == "long"
    assert cost == 0.387


def test_short_context_cost(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS", "272000")
    cost, tier = estimate_cost_usd(
        "gpt-5.6-luna",
        input_tokens=100_000,
        cached_input_tokens=20_000,
        cache_write_tokens=10_000,
        output_tokens=5_000,
    )
    assert tier == "short"
    assert cost == 0.0229


def test_unknown_model_does_not_invent_cost(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS", "272000")
    cost, tier = estimate_cost_usd(
        "unknown-model",
        input_tokens=10,
        cached_input_tokens=0,
        cache_write_tokens=0,
        output_tokens=10,
    )
    assert cost is None
    assert tier == "short"
    assert PRICING_VERSION == "gpt-5.6-2026-09-02"
