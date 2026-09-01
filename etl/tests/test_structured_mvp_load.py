"""배치 분할·DB 이름 생성(순수 함수)과 승격 안전장치(가짜 세션)를 검증한다.
실제 MERGE/제약조건 적용/DB 생성은 Neo4j 드라이버 세션이 필요해 로컬 docker
환경에서 통합 검증한다."""

import re
from typing import Any

import pytest
from structured_mvp_load import (
    build_promotion_failure_message,
    chunk_rows,
    database_exists,
    generate_database_name,
    has_active_transactions,
    retry_promote,
)


class _FakeResult:
    def __init__(self, record: Any) -> None:
        self._record = record

    def single(self) -> Any:
        return self._record


class _FakeSession:
    """준비된 record를 순서대로 돌려주고 실행된 쿼리를 기록하는 가짜 system 세션."""

    def __init__(self, records: list[Any]) -> None:
        self._records = list(records)
        self.runs: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **params: Any) -> _FakeResult:
        self.runs.append((query, params))
        return _FakeResult(self._records.pop(0) if self._records else None)

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeDriver:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def session(self, *, database: str | None = None) -> _FakeSession:
        return self._session


def test_chunk_rows_splits_into_batches_of_given_size() -> None:
    rows = list(range(2500))

    batches = list(chunk_rows(rows, batch_size=1000))

    assert len(batches) == 3
    assert len(batches[0]) == 1000
    assert len(batches[1]) == 1000
    assert len(batches[2]) == 500
    assert batches[0][0] == 0
    assert batches[2][-1] == 2499


def test_chunk_rows_handles_empty_list() -> None:
    assert list(chunk_rows([], batch_size=1000)) == []


def test_generate_database_name_matches_neo4j_naming_rules() -> None:
    # Neo4j DB 이름 규칙: 영문 시작, 이후 영숫자/점/대시만(밑줄 금지), 3~63자.
    name = generate_database_name("mvpgraph")

    assert re.fullmatch(r"[a-z][a-z0-9.-]{2,62}", name)
    assert "_" not in name
    assert name.startswith("mvpgraph-")


def test_generate_database_name_uses_given_prefix() -> None:
    assert generate_database_name("customprefix").startswith("customprefix-")


def test_build_promotion_failure_message_transactions_in_use_names_target_db() -> None:
    message = build_promotion_failure_message(
        "transactions_in_use", previous_default="mvpgraph-old", new_db="mvpgraph-new"
    )

    assert "mvpgraph-old" in message
    assert "트랜잭션을 이용 중입니다" in message
    assert "mvpgraph-new" in message


def test_build_promotion_failure_message_race_names_both_defaults() -> None:
    message = build_promotion_failure_message(
        "race",
        previous_default="mvpgraph-expected",
        new_db="mvpgraph-new",
        actual_default="mvpgraph-actual",
    )

    assert "mvpgraph-expected" in message
    assert "mvpgraph-actual" in message
    assert "다른 세션" in message
    assert "mvpgraph-new" in message


def test_build_promotion_failure_message_unknown_reason_falls_back() -> None:
    message = build_promotion_failure_message(
        "mystery", previous_default="mvpgraph-old", new_db="mvpgraph-new"
    )

    assert "mystery" in message


def test_database_exists_true_when_count_positive() -> None:
    session = _FakeSession([{"c": 1}])

    assert database_exists(session, "mvpgraph-new") is True


def test_database_exists_false_when_count_zero() -> None:
    session = _FakeSession([{"c": 0}])

    assert database_exists(session, "mvpgraph-new") is False


def test_has_active_transactions_reflects_count() -> None:
    assert has_active_transactions(_FakeSession([{"c": 0}]), "graph.db") is False
    assert has_active_transactions(_FakeSession([{"c": 3}]), "graph.db") is True


def test_retry_promote_happy_path_stops_old_and_sets_new_default() -> None:
    # 존재 확인 -> 기본 DB 조회 -> 트랜잭션 확인 -> STOP -> setDefaultDatabase
    session = _FakeSession([{"c": 1}, {"name": "mvpgraph-old"}, {"c": 0}, None, None])

    retry_promote(
        _FakeDriver(session), "mvpgraph-new", expected_previous_default="mvpgraph-old"
    )

    queries = " ".join(q for q, _ in session.runs)
    assert "STOP DATABASE `mvpgraph-old`" in queries
    assert "setDefaultDatabase" in queries


def test_retry_promote_exits_when_new_db_missing() -> None:
    session = _FakeSession([{"c": 0}])

    with pytest.raises(SystemExit) as exc:
        retry_promote(_FakeDriver(session), "mvpgraph-new")

    assert "존재하지 않습니다" in str(exc.value)
    assert not any("STOP DATABASE" in q for q, _ in session.runs)


def test_retry_promote_exits_on_race_without_touching_databases() -> None:
    session = _FakeSession([{"c": 1}, {"name": "mvpgraph-other"}])

    with pytest.raises(SystemExit) as exc:
        retry_promote(
            _FakeDriver(session),
            "mvpgraph-new",
            expected_previous_default="mvpgraph-old",
        )

    message = str(exc.value)
    assert "mvpgraph-old" in message and "mvpgraph-other" in message
    assert not any("STOP DATABASE" in q for q, _ in session.runs)


def test_retry_promote_exits_when_transactions_active() -> None:
    session = _FakeSession([{"c": 1}, {"name": "mvpgraph-old"}, {"c": 2}])

    with pytest.raises(SystemExit) as exc:
        retry_promote(
            _FakeDriver(session),
            "mvpgraph-new",
            expected_previous_default="mvpgraph-old",
        )

    assert "트랜잭션을 이용 중입니다" in str(exc.value)
    assert not any("STOP DATABASE" in q for q, _ in session.runs)
