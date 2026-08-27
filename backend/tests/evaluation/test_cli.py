import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import evaluation.cli as cli
from evaluation.models import EvaluationCase
from evaluation.runner import EvaluationRun


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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_report_returns_zero_for_query_accuracy_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_runtime(
        monkeypatch, EvaluationRun([_failed_record()], {"sha256": "x"}, False)
    )

    exit_code = cli.main(["--model", "test-model", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert (tmp_path / "junit.xml").is_file()
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert "gate" not in summary


def test_infrastructure_error_returns_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_runtime(monkeypatch, EvaluationRun([], {}, True))

    exit_code = cli.main(["--model", "test-model", "--output-dir", str(tmp_path)])

    assert exit_code == 2
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
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
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["error"] == "manifest에 없는 query ID: RQ99"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Model: `test-model`" in report
    assert "Infrastructure error: `manifest에 없는 query ID: RQ99`" in report
