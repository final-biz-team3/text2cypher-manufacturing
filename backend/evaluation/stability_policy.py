"""clean 90×3 평가 artifact에서 변경 회귀 기준 정책을 생성한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.errors import ConfigurationError

EXPECTED_CASE_COUNT = 90
EXPECTED_RUNS = (1, 2, 3)
OUTCOMES = frozenset({"CONSISTENT_PASS", "VARIABLE", "CONSISTENT_FAIL"})


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{label}이 비어 있습니다.")
    return value


def _classify(records: list[dict[str, Any]]) -> str:
    results = [record.get("queryPipelinePass") for record in records]
    if len(results) != len(EXPECTED_RUNS) or any(
        not isinstance(value, bool) for value in results
    ):
        return "INCOMPLETE"
    if all(results):
        return "CONSISTENT_PASS"
    if not any(results):
        return "CONSISTENT_FAIL"
    return "VARIABLE"


def build_stability_policy(
    artifact_path: Path, *, expected_commit: str | None = None
) -> dict[str, Any]:
    """평가 JSON을 검증하고 case별 고정 안정성 분류를 반환한다."""
    resolved = artifact_path.resolve()
    try:
        payload = resolved.read_bytes()
        document = json.loads(payload)
        summary = document["summary"]
        records = document["cases"]
        metrics = summary["metrics"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigurationError(
            f"평가 artifact를 읽을 수 없습니다: {resolved}: {exc}"
        ) from exc
    if not isinstance(summary, dict) or not isinstance(records, list):
        raise ConfigurationError("평가 artifact의 summary/cases 형식이 잘못됐습니다.")
    if len(records) != EXPECTED_CASE_COUNT * len(EXPECTED_RUNS):
        raise ConfigurationError(
            "stability policy는 정확히 90 case × 3 run이 필요합니다."
        )
    if summary.get("workingTreeDirty") is not False:
        raise ConfigurationError("stability baseline은 clean worktree여야 합니다.")
    if not isinstance(metrics, dict) or metrics.get("executionMode") != "orchestrator":
        raise ConfigurationError("stability baseline은 orchestrator mode여야 합니다.")

    commit = _non_empty_string(summary.get("commit"), "baseline commit")
    if commit == "unknown":
        raise ConfigurationError("baseline commit을 확인할 수 없습니다.")
    if expected_commit is not None and commit != expected_commit:
        raise ConfigurationError(
            f"baseline commit 불일치: expected={expected_commit}, actual={commit}"
        )
    model = _non_empty_string(summary.get("model"), "model")
    reasoning_effort = _non_empty_string(
        summary.get("reasoningEffort"), "reasoning effort"
    )
    snapshot = summary.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ConfigurationError("snapshot 메타데이터가 없습니다.")
    snapshot_sha = _non_empty_string(snapshot.get("sha256"), "snapshot SHA-256")

    records_by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ConfigurationError("cases 항목은 객체여야 합니다.")
        case_id = _non_empty_string(record.get("caseId"), "case ID")
        records_by_case.setdefault(case_id, []).append(record)
        if record.get("executionMode") != "orchestrator":
            raise ConfigurationError(f"{case_id}가 orchestrator mode가 아닙니다.")
        if record.get("status") == "ERROR":
            raise ConfigurationError(
                f"{case_id}에 incomplete infrastructure run이 있습니다."
            )

    if len(records_by_case) != EXPECTED_CASE_COUNT:
        raise ConfigurationError(
            "stability policy는 정확히 90개의 고유 case가 필요합니다."
        )

    case_policy: dict[str, dict[str, Any]] = {}
    for case_id, case_records in sorted(records_by_case.items()):
        ordered = sorted(case_records, key=lambda item: int(item.get("run", 0)))
        runs = [record.get("run") for record in ordered]
        if runs != list(EXPECTED_RUNS):
            raise ConfigurationError(
                f"{case_id} run은 정확히 1, 2, 3이어야 합니다: {runs}"
            )
        outcome = _classify(ordered)
        if outcome not in OUTCOMES:
            raise ConfigurationError(f"{case_id} 분류가 INCOMPLETE입니다.")
        contract_ids = {record.get("contractId") for record in ordered}
        suites = {record.get("suite") for record in ordered}
        if len(contract_ids) != 1 or len(suites) != 1:
            raise ConfigurationError(
                f"{case_id}의 contract/suite가 run 사이에 다릅니다."
            )
        case_policy[case_id] = {
            "contractId": next(iter(contract_ids)),
            "suite": next(iter(suites)),
            "outcome": outcome,
            "passCount": sum(
                record.get("queryPipelinePass") is True for record in ordered
            ),
        }

    summary_case_ids = summary.get("evaluationSet", {}).get("caseIds")
    if summary_case_ids != sorted(records_by_case):
        raise ConfigurationError("summary와 cases의 case 집합이 일치하지 않습니다.")

    return {
        "policyVersion": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "baseline": {
            "commit": commit,
            "artifactSha256": hashlib.sha256(payload).hexdigest(),
            "model": model,
            "reasoningEffort": reasoning_effort,
            "snapshotSha256": snapshot_sha,
            "executionMode": "orchestrator",
            "workingTreeDirty": False,
            "caseCount": EXPECTED_CASE_COUNT,
            "runs": len(EXPECTED_RUNS),
        },
        "cases": case_policy,
        "ratchetPassCaseIds": sorted(
            case_id
            for case_id, item in case_policy.items()
            if item["outcome"] == "CONSISTENT_PASS"
        ),
    }


def write_stability_policy(
    artifact_path: Path,
    output_path: Path,
    *,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    policy = build_stability_policy(artifact_path, expected_commit=expected_commit)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="90×3 stability policy 생성")
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("queries/evaluation/stability_policy.json"),
    )
    parser.add_argument("--expected-commit")
    args = parser.parse_args(argv)
    try:
        policy = write_stability_policy(
            args.artifact,
            args.output,
            expected_commit=args.expected_commit,
        )
    except ConfigurationError as exc:
        parser.error(str(exc))
    print(json.dumps(policy["baseline"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
