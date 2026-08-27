import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import evaluation.cli as cli
from evaluation.models import EvaluationCase
from evaluation.runner import EvaluationRun


def _read_evaluation(output_dir: Path) -> dict[str, Any]:
    return json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))


def _failed_record() -> dict[str, Any]:
    return {
        "caseId": "RQ01",
        "contractId": "RQ01",
        "suite": "canonical",
        "run": 1,
        "route": "SQL",
        "supportStatus": "FULLY_EVALUATED",
        "status": "FAIL",
        "queryPipelinePass": False,
        "finalResultEvaluated": True,
        "finalResultPass": False,
        "checks": {
            "entity": True,
            "routing": True,
            "split": True,
            "generation": True,
            "execution": True,
            "result": False,
        },
        "subqueries": [{"tool": "sql", "status": "FAIL", "checks": {"result": False}}],
    }


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    result: EvaluationRun,
) -> None:
    case = EvaluationCase("RQ01", "RQ01", "canonical", "질문", {})
    manifest = SimpleNamespace(
        cases=(case,),
        contracts={"RQ01": SimpleNamespace(route="SQL")},
    )

    class FakeDatabase:
        @classmethod
        def from_environment(cls) -> "FakeDatabase":
            return cls()

        def close(self) -> None:
            pass

    class FakeRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, cases: list[EvaluationCase], runs: int) -> EvaluationRun:
            return result

    async def _noop() -> None:
        return None

    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "load_manifest", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(cli, "ReadOnlyDatabaseExecutor", FakeDatabase)
    monkeypatch.setattr(cli, "EvaluationRunner", FakeRunner)
    monkeypatch.setattr(cli, "AsyncOpenAI", lambda **kwargs: object())
    monkeypatch.setattr(cli, "bootstrap_postgres", _noop)
    monkeypatch.setattr(cli, "open_pool", _noop)
    monkeypatch.setattr(cli, "close_pool", _noop)
    monkeypatch.setattr(cli, "_working_tree_dirty", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_report_returns_zero_for_query_accuracy_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_runtime(
        monkeypatch, EvaluationRun([_failed_record()], {"sha256": "x"}, False)
    )

    exit_code = cli.main(["--model", "test-model", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert {path.name for path in tmp_path.iterdir()} == {
        "evaluation.json",
        "report.md",
    }
    summary = _read_evaluation(tmp_path)["summary"]
    assert "gate" not in summary
    assert summary["workingTreeDirty"] is False


def test_infrastructure_error_returns_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_runtime(monkeypatch, EvaluationRun([], {}, True))

    exit_code = cli.main(["--model", "test-model", "--output-dir", str(tmp_path)])

    assert exit_code == 2
    summary = _read_evaluation(tmp_path)["summary"]
    assert summary["infrastructureError"] is True


def test_mixed_known_and_unknown_ids_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_runtime(monkeypatch, EvaluationRun([], {}, False))

    exit_code = cli.main(
        [
            "--ids",
            "RQ01,RQ99",
            "--model",
            "test-model",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 2
    summary = _read_evaluation(tmp_path)["summary"]
    assert summary["error"] == "manifest에 없는 query ID: RQ99"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 모델 | test-model |" in report
    assert "| 인프라 오류 | manifest에 없는 query ID: RQ99 |" in report
