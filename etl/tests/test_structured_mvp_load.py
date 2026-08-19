"""배치 분할(순수 함수)만 pytest로 검증한다. 실제 MERGE/prune 실행은 Neo4j
드라이버 세션이 필요해 로컬 docker 환경에서 통합 검증한다."""

from structured_mvp_load import chunk_rows


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
