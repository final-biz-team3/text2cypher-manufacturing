"""이번 범위의 대상 질의 5개(RQ01, RQ02, RQ08, RQ12, RQ13)가 query_contracts.json에
계약대로 정의돼 있는지 확인한다."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
QUERY_CONTRACTS_PATH = PROJECT_ROOT / "queries" / "query_contracts.json"

TARGET_QUERY_ROUTES = {
    "RQ01": "SQL",
    "RQ02": "SQL",
    "RQ08": "SQL",
    "RQ12": "GRAPH",
    "RQ13": "GRAPH",
}


def test_target_queries_exist_with_expected_route() -> None:
    """RQ01·RQ02·RQ08은 SQL, RQ12·RQ13은 GRAPH로 정의돼 있다."""
    contracts = json.loads(QUERY_CONTRACTS_PATH.read_text(encoding="utf-8"))
    questions_by_id = {question["id"]: question for question in contracts["questions"]}

    for query_id, expected_route in TARGET_QUERY_ROUTES.items():
        assert query_id in questions_by_id
        assert questions_by_id[query_id]["route"] == expected_route
