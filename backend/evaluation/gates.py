"""Promotion gates derived from completed evaluation records."""

from typing import Any


def _check(name: str, passed: bool, actual: Any, required: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "actual": actual,
        "required": required,
    }


def _strict_range(records: list[dict[str, Any]]) -> float | None:
    by_run: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        run = record.get("run")
        if isinstance(run, int):
            by_run.setdefault(run, []).append(record)
    if len(by_run) < 3 or any(not values for values in by_run.values()):
        return None
    scores = [
        sum(record.get("queryPipelinePass") is True for record in values) / len(values)
        for values in by_run.values()
    ]
    return round(max(scores) - min(scores), 6)


def build_regression_gate(
    records: list[dict[str, Any]],
    metrics: dict[str, Any],
    performance_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trial_summary = metrics.get("caseTrialSummary", {})
    strict_range = _strict_range(records)
    required_cases = ("RQ18", "RQ19", "RQ20", "HQ09", "HQ10")
    critical_cases_pass = all(
        trial_summary.get(case_id, {}).get("outcome") == "CONSISTENT_PASS"
        and trial_summary.get(case_id, {}).get("totalTrials", 0) >= 3
        for case_id in required_cases
    )
    canonical_ids = {
        str(record.get("caseId"))
        for record in records
        if record.get("suite") == "canonical"
    }
    canonical_consistent_fail = sorted(
        canonical_ids & set(metrics.get("consistentFailCaseIds", []))
    )
    trials_by_case: dict[str, list[int]] = {}
    for record in records:
        case_id = str(record.get("caseId"))
        run = record.get("run")
        if isinstance(run, int):
            trials_by_case.setdefault(case_id, []).append(run)
    full_trial_set = len(trials_by_case) == 90 and all(
        sorted(runs) == [1, 2, 3] for runs in trials_by_case.values()
    )
    route_scores = metrics.get("pipelinePassByRoute", {})
    stage = metrics.get("stageAccuracy", {})
    baseline = performance_baseline or {}
    baseline_calls = baseline.get("averageModelCallCount")
    baseline_p95 = baseline.get("p95LatencyMs")
    current_calls = metrics.get("averageModelCallCount")
    current_p95 = metrics.get("p95LatencyMs")
    performance_pass = (
        baseline.get("compatible") is True
        and isinstance(baseline_calls, int | float)
        and baseline_calls > 0
        and isinstance(baseline_p95, int | float)
        and baseline_p95 > 0
        and isinstance(current_calls, int | float)
        and current_calls <= baseline_calls * 1.5
        and isinstance(current_p95, int | float)
        and current_p95 <= baseline_p95 * 2
    )
    checks = [
        _check(
            "production orchestrator",
            metrics.get("promotionEligible") is True,
            metrics.get("executionMode"),
            "orchestrator",
        ),
        _check(
            "90 cases × 3 runs",
            full_trial_set,
            {
                "cases": len(trials_by_case),
                "runs": max(
                    (int(record.get("run", 0)) for record in records), default=0
                ),
                "records": len(records),
            },
            "90 cases, exactly runs 1/2/3 (270 records)",
        ),
        _check(
            "evaluation coverage",
            metrics.get("evaluationCoverage") == 1.0,
            metrics.get("evaluationCoverage"),
            "100%",
        ),
        _check(
            "strict accuracy",
            metrics.get("queryPipelineAccuracy", 0) >= 0.90,
            metrics.get("queryPipelineAccuracy"),
            ">=90%",
        ),
        _check(
            "route strict accuracy",
            all(
                route_scores.get(route, 0) >= 0.85
                for route in ("SQL", "GRAPH", "HYBRID")
            ),
            route_scores,
            "each >=85%",
        ),
        _check(
            "routing accuracy",
            metrics.get("routingAccuracy", 0) >= 0.95,
            metrics.get("routingAccuracy"),
            ">=95%",
        ),
        _check(
            "required outputs",
            metrics.get("requiredOutputsContractRate", 0) >= 0.95,
            metrics.get("requiredOutputsContractRate"),
            ">=95%",
        ),
        _check(
            "required outputs exact",
            metrics.get("requiredOutputsExactRate", 0) >= 0.85,
            metrics.get("requiredOutputsExactRate"),
            ">=85%",
        ),
        _check(
            "binding contract",
            metrics.get("bindingContractRate", 0) == 1.0,
            metrics.get("bindingContractRate"),
            "100%",
        ),
        _check(
            "execution with retry",
            isinstance(stage.get("execution"), float) and stage["execution"] >= 0.98,
            stage.get("execution"),
            ">=98%",
        ),
        _check(
            "semantic coverage and accuracy",
            metrics.get("semanticResultCoverage", 0) >= 0.90
            and metrics.get("semanticResultAccuracy", 0) >= 0.95,
            {
                "coverage": metrics.get("semanticResultCoverage"),
                "accuracy": metrics.get("semanticResultAccuracy"),
            },
            "coverage >=90%, accuracy >=95%",
        ),
        _check(
            "final result coverage and accuracy",
            metrics.get("finalResultEvaluationCoverage", 0) == 1.0
            and metrics.get("finalResultAccuracy", 0) >= 0.95,
            {
                "coverage": metrics.get("finalResultEvaluationCoverage"),
                "accuracy": metrics.get("finalResultAccuracy"),
            },
            "coverage 100%, accuracy >=95%",
        ),
        _check(
            "strict score range",
            strict_range is not None and strict_range <= 0.03,
            strict_range,
            "<=3%p",
        ),
        _check(
            "consistent pass cases",
            metrics.get("consistentPassCaseRate", 0) >= 0.85,
            metrics.get("consistentPassCaseRate"),
            ">=85%",
        ),
        _check(
            "critical hybrid cases",
            critical_cases_pass,
            {
                case_id: trial_summary.get(case_id, {}).get("outcome")
                for case_id in required_cases
            },
            "all CONSISTENT_PASS across 3 runs",
        ),
        _check(
            "canonical consistent failures",
            not canonical_consistent_fail and bool(canonical_ids),
            canonical_consistent_fail,
            "none",
        ),
        _check(
            "model call and latency budget",
            performance_pass,
            (
                {
                    "currentCalls": current_calls,
                    "baselineCalls": baseline_calls,
                    "currentP95Ms": current_p95,
                    "baselineP95Ms": baseline_p95,
                    "baselineArtifactSha256": baseline.get("artifactSha256"),
                    "compatibilityErrors": baseline.get("compatibilityErrors", []),
                }
                if performance_baseline is not None
                else "baseline unavailable"
            ),
            "calls <=1.5× and p95 latency <=2× baseline",
        ),
    ]
    return {
        "status": (
            "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
        ),
        "checks": checks,
    }
