"""make_batch가 N+1개 결과를 N개로 자르고 truncated를 정확히 계산하는지 검증한다."""

from orchestrator.execution.result import make_batch


def test_make_batch_marks_truncated_when_over_limit() -> None:
    batch = make_batch([1, 2, 3], row_limit=2)

    assert batch["rows"] == [1, 2]
    assert batch["truncated"] is True


def test_make_batch_not_truncated_when_within_limit() -> None:
    batch = make_batch([1, 2], row_limit=2)

    assert batch["rows"] == [1, 2]
    assert batch["truncated"] is False


def test_make_batch_not_truncated_when_empty() -> None:
    batch = make_batch([], row_limit=2)

    assert batch["rows"] == []
    assert batch["truncated"] is False


def test_make_batch_applies_extract_before_slicing_result() -> None:
    class _Record:
        def __init__(self, value: int) -> None:
            self.value = value

        def data(self) -> dict[str, int]:
            return {"value": self.value}

    records = [_Record(1), _Record(2), _Record(3)]

    batch = make_batch(records, row_limit=2, extract=lambda record: record.data())

    assert batch["rows"] == [{"value": 1}, {"value": 2}]
    assert batch["truncated"] is True
