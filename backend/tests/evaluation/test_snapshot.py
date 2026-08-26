from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from evaluation.errors import InfrastructureError
from evaluation.runner import EvaluationRunner


def _snapshot_runner(
    *, actual: int, sync_run_ids: list[str | None]
) -> EvaluationRunner:
    runner = object.__new__(EvaluationRunner)
    runner.manifest = SimpleNamespace(  # type: ignore[assignment]
        snapshot_checks=(
            SimpleNamespace(
                source="sql",
                name="fixture.count",
                query="SELECT 1 AS value",
                expected=1,
                parameters={},
            ),
        ),
        snapshot_sync_run_id="approved-run",
    )
    runner.database = SimpleNamespace(sync_run_ids=lambda: sync_run_ids)

    def execute(
        self: EvaluationRunner,
        tool: str,
        query: str,
        parameters: dict[str, Any],
        max_rows: int,
    ) -> list[dict[str, int]]:
        return [{"value": actual}]

    runner._execute = MethodType(execute, runner)  # type: ignore[method-assign]
    return runner


def test_snapshot_rejects_business_fixture_mismatch() -> None:
    runner = _snapshot_runner(actual=2, sync_run_ids=["approved-run"])

    with pytest.raises(InfrastructureError, match="DB snapshot 불일치"):
        runner.validate_snapshot()


@pytest.mark.parametrize("sync_run_ids", [[], ["a", "b"], [None]])
def test_snapshot_requires_one_approved_sync_run(
    sync_run_ids: list[str | None],
) -> None:
    runner = _snapshot_runner(actual=1, sync_run_ids=sync_run_ids)

    with pytest.raises(InfrastructureError, match="syncRunId"):
        runner.validate_snapshot()
