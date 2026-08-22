"""배치 분할·DB 이름 생성(순수 함수)만 pytest로 검증한다. 실제 MERGE/제약조건
적용/DB 생성·승격은 Neo4j 드라이버 세션이 필요해 로컬 docker 환경에서
통합 검증한다."""

import re

from structured_mvp_load import (
    build_promotion_failure_message,
    chunk_rows,
    generate_database_name,
)


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
