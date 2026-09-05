import hashlib
import json
from pathlib import Path

import pytest

from evaluation.errors import ConfigurationError
from evaluation.stability_policy import build_stability_policy


def _artifact(tmp_path: Path, *, dirty: bool = False) -> Path:
    records = []
    case_ids = [f"CASE{number:02d}" for number in range(1, 91)]
    for case_id in case_ids:
        for run in (1, 2, 3):
            passed = case_id != "CASE02" and not (case_id == "CASE03" and run == 2)
            records.append(
                {
                    "caseId": case_id,
                    "contractId": case_id,
                    "suite": "canonical",
                    "run": run,
                    "status": "PASS" if passed else "FAIL",
                    "queryPipelinePass": passed,
                    "executionMode": "orchestrator",
                }
            )
    document = {
        "summary": {
            "commit": "a" * 40,
            "workingTreeDirty": dirty,
            "model": "test-model",
            "reasoningEffort": "medium",
            "snapshot": {"sha256": "b" * 64},
            "metrics": {"executionMode": "orchestrator"},
            "evaluationSet": {"caseIds": case_ids},
        },
        "cases": records,
    }
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_build_stability_policy_classifies_complete_clean_runs(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)

    policy = build_stability_policy(artifact, expected_commit="a" * 40)

    assert (
        policy["baseline"]["artifactSha256"]
        == hashlib.sha256(artifact.read_bytes()).hexdigest()
    )
    assert policy["baseline"]["caseCount"] == 90
    assert policy["cases"]["CASE01"]["outcome"] == "CONSISTENT_PASS"
    assert policy["cases"]["CASE02"]["outcome"] == "CONSISTENT_FAIL"
    assert policy["cases"]["CASE03"]["outcome"] == "VARIABLE"
    assert "CASE01" in policy["ratchetPassCaseIds"]
    assert "CASE02" not in policy["ratchetPassCaseIds"]


def test_stability_policy_rejects_dirty_or_incomplete_runs(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="clean worktree"):
        build_stability_policy(_artifact(tmp_path, dirty=True))

    artifact = _artifact(tmp_path)
    document = json.loads(artifact.read_text(encoding="utf-8"))
    document["cases"].pop()
    artifact.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="90 case × 3 run"):
        build_stability_policy(artifact)


def test_stability_policy_rejects_commit_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="commit 불일치"):
        build_stability_policy(_artifact(tmp_path), expected_commit="c" * 40)
