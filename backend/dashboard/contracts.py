"""대시보드 SQL·정렬 계약을 단일 JSON 파일에서 읽는다."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "queries" / "dashboard" / "contracts.json"
)
PROCESS_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "queries"
    / "dashboard"
    / "process_contracts.json"
)


@lru_cache(maxsize=1)
def load_dashboard_contracts() -> dict[str, Any]:
    with CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        contracts: dict[str, Any] = json.load(contract_file)

    if set(contracts) != {"snapshot", "kpis", "cards"}:
        raise RuntimeError("대시보드 계약의 최상위 키가 올바르지 않습니다.")
    if len(contracts["kpis"]) != 6 or len(contracts["cards"]) != 7:
        raise RuntimeError("대시보드 KPI 또는 카드 계약 수가 올바르지 않습니다.")
    return contracts


def get_card_contract(card_key: str) -> dict[str, Any] | None:
    return load_dashboard_contracts()["cards"].get(card_key)


@lru_cache(maxsize=1)
def load_process_dashboard_contracts() -> dict[str, Any]:
    with PROCESS_CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        contracts: dict[str, Any] = json.load(contract_file)

    if set(contracts) != {"range", "summary", "trend", "locations"}:
        raise RuntimeError("공정 대시보드 계약의 최상위 키가 올바르지 않습니다.")
    return contracts
