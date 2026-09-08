"""완료된 평가 기록을 바탕으로 승격 gate를 계산한다."""

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


def build_change_regression_gate(
    records: list[dict[str, Any]],
    stability_policy: dict[str, Any] | None,
    ratchet_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """G0 고정 분류와 직전 clean 결과를 이용해 신규 회귀만 판정한다."""
    if stability_policy is None:
        return {
            "status": "NOT_EVALUATED",
            "compatibilityErrors": ["stability policy unavailable"],
            "regressions": [],
            "rerunRequired": [],
        }

    compatibility_errors = list(stability_policy.get("compatibilityErrors", []))
    policy_cases = stability_policy.get("cases")
    if not isinstance(policy_cases, dict):
        compatibility_errors.append("stability policy cases are invalid")
        policy_cases = {}

    records_by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        case_id = record.get("caseId")
        if isinstance(case_id, str):
            records_by_case.setdefault(case_id, []).append(record)
    if set(records_by_case) != set(policy_cases):
        compatibility_errors.append("stability policy case set mismatch")

    ratchet_ids: set[str] = set()
    if ratchet_baseline is not None:
        compatibility_errors.extend(ratchet_baseline.get("compatibilityErrors", []))
        raw_ids = ratchet_baseline.get("passCaseIds", [])
        if isinstance(raw_ids, list):
            ratchet_ids = {case_id for case_id in raw_ids if isinstance(case_id, str)}

    regressions: list[dict[str, Any]] = []
    rerun_required: list[str] = []
    for case_id, case_records in sorted(records_by_case.items()):
        outcomes = [record.get("queryPipelinePass") for record in case_records]
        if any(record.get("status") == "ERROR" for record in case_records) or any(
            not isinstance(outcome, bool) for outcome in outcomes
        ):
            regressions.append(
                {"caseId": case_id, "reason": "INCOMPLETE_OR_INFRASTRUCTURE_ERROR"}
            )
            continue
        failures = sum(outcome is False for outcome in outcomes)
        policy_item = policy_cases.get(case_id, {})
        baseline_outcome = (
            policy_item.get("outcome") if isinstance(policy_item, dict) else None
        )
        if baseline_outcome == "CONSISTENT_PASS" and failures:
            regressions.append(
                {"caseId": case_id, "reason": "G0_CONSISTENT_PASS_REGRESSION"}
            )
        elif baseline_outcome == "VARIABLE" and failures:
            if len(outcomes) >= 3 and failures == len(outcomes):
                regressions.append(
                    {"caseId": case_id, "reason": "G0_VARIABLE_BECAME_CONSISTENT_FAIL"}
                )
            elif len(outcomes) < 3:
                rerun_required.append(case_id)
        if case_id in ratchet_ids and failures:
            regressions.append({"caseId": case_id, "reason": "RATCHET_PASS_REGRESSION"})

    unique_regressions = sorted(
        {(item["caseId"], item["reason"]) for item in regressions}
    )
    regression_items = [
        {"caseId": case_id, "reason": reason} for case_id, reason in unique_regressions
    ]
    if compatibility_errors or regression_items:
        status = "FAIL"
    elif rerun_required:
        status = "RERUN_REQUIRED"
    else:
        status = "PASS"
    return {
        "status": status,
        "compatibilityErrors": sorted(set(compatibility_errors)),
        "regressions": regression_items,
        "rerunRequired": sorted(set(rerun_required)),
    }


def build_answer_quality_gate(
    records: list[dict[str, Any]],
    metrics: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """fallback을 질의 실패와 분리한 채 수집률과 기준선 대비 증가를 판정한다."""
    fallback_pollution = sorted(
        str(record.get("caseId"))
        for record in records
        if isinstance((metadata := record.get("answerGeneration")), dict)
        and metadata.get("mode") == "fallback"
        and (
            record.get("planningError") is not None
            or bool(record.get("failureReasons"))
            or record.get("failureStage") is not None
        )
        and record.get("queryPipelinePass") is True
    )
    independent_checks = [
        _check(
            "fallback metadata coverage",
            metrics.get("answerMetadataCoverage") == 1.0,
            metrics.get("answerMetadataCoverage"),
            "100%",
        ),
        _check(
            "fallback does not create planning failures",
            not fallback_pollution,
            fallback_pollution,
            "none",
        ),
    ]

    baseline_rate = baseline.get("answerFallbackRate") if baseline else None
    current_rate = metrics.get("answerFallbackRate")
    baseline_compatible = baseline is not None and baseline.get("compatible") is True
    dependent_status = "NOT_EVALUATED"
    if (
        baseline_compatible
        and isinstance(baseline_rate, int | float)
        and isinstance(current_rate, int | float)
    ):
        dependent_status = "PASS" if current_rate <= baseline_rate else "FAIL"
    dependent_check = {
        "name": "fallback rate does not increase from G0",
        "status": dependent_status,
        "actual": {
            "currentRate": current_rate,
            "baselineRate": baseline_rate,
            "currentReasons": metrics.get("answerFallbackReasonCounts", {}),
            "baselineReasons": (
                baseline.get("answerFallbackReasonCounts", {}) if baseline else {}
            ),
        },
        "required": "candidate <= G0",
    }
    status = (
        "FAIL"
        if any(check["status"] == "FAIL" for check in independent_checks)
        or dependent_status == "FAIL"
        else "PASS" if dependent_status == "PASS" else "NOT_EVALUATED"
    )
    return {
        "status": status,
        "independentChecks": independent_checks,
        "g0DependentChecks": [dependent_check],
    }


def build_cost_warnings(
    metrics: dict[str, Any], baseline: dict[str, Any] | None
) -> dict[str, Any]:
    """품질 PR의 비용 증가는 승격 실패가 아니라 명시적인 WARN으로 노출한다."""
    if baseline is None or baseline.get("compatible") is not True:
        return {"status": "NOT_EVALUATED", "warnings": []}

    comparisons = (
        ("p95LatencyMs", 1.20, "p95 latency >20%"),
        ("averageModelTokensPerRun", 1.25, "average tokens/run >25%"),
    )
    warnings: list[dict[str, Any]] = []
    for key, ratio, label in comparisons:
        current = metrics.get(key)
        before = baseline.get(key)
        if (
            isinstance(current, int | float)
            and isinstance(before, int | float)
            and before > 0
            and current > before * ratio
        ):
            warnings.append(
                {"metric": key, "before": before, "current": current, "reason": label}
            )
    current_calls = metrics.get("averageModelCallCount")
    baseline_calls = baseline.get("averageModelCallCount")
    if (
        isinstance(current_calls, int | float)
        and isinstance(baseline_calls, int | float)
        and current_calls >= baseline_calls + 0.25
    ):
        warnings.append(
            {
                "metric": "averageModelCallCount",
                "before": baseline_calls,
                "current": current_calls,
                "reason": "average model calls +0.25 or more",
            }
        )
    return {"status": "WARN" if warnings else "PASS", "warnings": warnings}
