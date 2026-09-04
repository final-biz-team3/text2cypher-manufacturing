import json
from pathlib import Path

from evaluation.gates import build_regression_gate
from evaluation.reporting import (
    build_summary,
    calculate_metrics,
    write_artifacts,
)
from evaluation.runner import EvaluationRun


def _record(case_id: str, *, passed: bool, route: str = "SQL", run: int = 1) -> dict:
    return {
        "caseId": case_id,
        "contractId": case_id,
        "suite": "canonical",
        "run": run,
        "route": route,
        "status": "PASS" if passed else "FAIL",
        "supportStatus": "FULLY_EVALUATED",
        "queryPipelinePass": passed,
        "semanticResultPass": passed,
        "finalResultEvaluated": True,
        "finalResultPass": passed,
        "checks": {
            "entity": passed,
            "routing": passed,
            "split": passed,
            "generation": passed,
            "execution": passed,
            "result": passed,
        },
        "subqueries": [
            {
                "tool": "sql",
                "status": "PASS" if passed else "FAIL",
                "checks": {"result": passed},
            }
        ],
    }


def test_metrics_calculate_stability_across_repeated_runs() -> None:
    metrics = calculate_metrics(
        [
            _record("RQ01", passed=True, run=1),
            _record("RQ01", passed=True, run=2),
            _record("RQ02", passed=True, run=1),
            _record("RQ02", passed=False, run=2),
            _record("RQ03", passed=False, run=1),
            _record("RQ03", passed=False, run=2),
        ]
    )

    assert metrics["queryPipelineAccuracy"] == 0.5
    assert metrics["caseStability"] == 0.333333
    assert metrics["stablePassCaseIds"] == ["RQ01"]
    assert metrics["persistentFailureCaseIds"] == ["RQ03"]
    assert metrics["consistentPassCaseRate"] == 0.333333
    assert metrics["caseOutcomeConsistency"] == 0.666667
    assert metrics["consistentPassCaseIds"] == ["RQ01"]
    assert metrics["variableCaseIds"] == ["RQ02"]
    assert metrics["consistentFailCaseIds"] == ["RQ03"]
    assert metrics["incompleteCaseIds"] == []
    assert metrics["caseTrialSummary"]["RQ02"] == {
        "totalTrials": 2,
        "completedTrials": 2,
        "passCount": 1,
        "passRate": 0.5,
        "outcome": "VARIABLE",
    }


def test_metrics_aggregate_model_token_usage(tmp_path: Path) -> None:
    first = _record("RQ01", passed=True)
    first.update(
        {
            "modelCallCount": 2,
            "modelTokenUsage": {
                "reportedCallCount": 2,
                "promptTokens": 1_000,
                "cachedPromptTokens": 600,
                "cacheWritePromptTokens": 100,
                "completionTokens": 200,
                "reasoningTokens": 80,
                "totalTokens": 1_200,
            },
        }
    )
    second = _record("RQ02", passed=True)
    second.update(
        {
            "modelCallCount": 3,
            "modelTokenUsage": {
                "reportedCallCount": 3,
                "promptTokens": 2_000,
                "cachedPromptTokens": 1_200,
                "cacheWritePromptTokens": 200,
                "completionTokens": 400,
                "reasoningTokens": 160,
                "totalTokens": 2_400,
            },
        }
    )

    metrics = calculate_metrics([first, second])

    assert metrics["totalModelCallCount"] == 5
    assert metrics["modelTokenUsage"] == {
        "reportedCallCount": 5,
        "callCoverage": 1.0,
        "promptTokens": 3_000,
        "cachedPromptTokens": 1_800,
        "cacheWritePromptTokens": 300,
        "completionTokens": 600,
        "reasoningTokens": 240,
        "totalTokens": 3_600,
    }

    summary = build_summary(
        EvaluationRun([first, second], {"sha256": "snapshot"}, False),
        model="test-model",
        commit="commit",
        validate_gold=False,
        working_tree_dirty=False,
    )
    write_artifacts(tmp_path, summary, [first, second])
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 총 모델 호출 수 | 5 |" in report
    assert "| 토큰 usage 수집 호출 | 5 (100.0%) |" in report
    assert "| 입력 토큰 (캐시 hit / 캐시 write) | 3,000 (1,800 / 300) |" in report
    assert "| 출력 토큰 (추론 포함) | 600 (240) |" in report
    assert "| 총 토큰 | 3,600 |" in report


def test_report_summarizes_repeated_trial_outcomes(tmp_path: Path) -> None:
    records = [
        _record("RQ01", passed=True, run=1),
        _record("RQ01", passed=True, run=2),
        _record("RQ02", passed=True, run=1),
        _record("RQ02", passed=False, run=2),
        _record("RQ03", passed=False, run=1),
        _record("RQ03", passed=False, run=2),
    ]
    result = EvaluationRun(records, {"sha256": "snapshot"}, False)
    summary = build_summary(
        result,
        model="test-model",
        commit="commit",
        validate_gold=False,
        working_tree_dirty=False,
    )

    write_artifacts(tmp_path, summary, records)

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## 반복 실행 안정성" in report
    assert "| 전회 PASS case | 1/3 | 33.3% |" in report
    assert "| 실행별 변동 case | 1/3 | 33.3% |" in report
    assert "| 전회 FAIL case | 1/3 | 33.3% |" in report
    assert "| 결과 일관 case | 2/3 | 66.7% |" in report
    assert "전회 FAIL도 일관 case에 포함됩니다" in report
    assert "| RQ01 | 2/2 | 2/2 | 전회 PASS |" in report
    assert "| RQ02 | 2/2 | 1/2 | 실행별 변동 |" in report
    assert "| RQ03 | 2/2 | 0/2 | 전회 FAIL |" in report


def test_quality_scorecard_has_evidence_for_all_eight_areas() -> None:
    record = _record("RQ01", passed=True)
    record["executionMode"] = "orchestrator"
    record["routeDraft"] = {}
    record["subqueryPlan"] = []
    record["attemptCount"] = 1
    record["composedResult"] = {}
    record["checks"].update(
        {"requiredOutputs": True, "binding": True, "composition": True}
    )
    summary = build_summary(
        EvaluationRun([record], {"sha256": "snapshot"}, False),
        model="test-model",
        commit="commit",
        validate_gold=False,
        working_tree_dirty=False,
    )

    scorecard = summary["qualityScorecard"]
    assert summary["regressionGate"]["status"] == "FAIL"
    assert len(scorecard["areas"]) == 8
    assert all(len(area["controls"]) == 4 for area in scorecard["areas"])
    assert all(
        control["status"] in {"PASS", "FAIL"} and control["evidence"]
        for area in scorecard["areas"]
        for control in area["controls"]
    )
    assert scorecard["passesThreshold"] is False
    assert any("blind" in item.casefold() for item in scorecard["criticalFailures"])


def test_metrics_separate_pipeline_from_result_coverage() -> None:
    record = _record("RQ02", passed=False)
    record["status"] = "PASS"
    record["queryPipelinePass"] = True
    record["checks"]["routing"] = True
    record["checks"]["result"] = True
    record["subqueries"][0]["checks"]["result"] = True
    record["semanticResultPass"] = True
    record["finalResultPass"] = True

    metrics = calculate_metrics([record])

    assert metrics["queryPipelineAccuracy"] == 1.0
    assert metrics["semanticResultCoverage"] == 1.0
    assert metrics["semanticResultAccuracy"] == 1.0
    assert metrics["finalResultAccuracy"] == 1.0
    assert metrics["routingAccuracy"] == 1.0
    assert metrics["routingAccuracyByRoute"]["SQL"] == 1.0
    assert metrics["pipelinePassByRoute"]["SQL"] == 1.0
    assert metrics["sqlPartialCoverage"] == 1.0
    assert metrics["sqlPartialAccuracy"] == 1.0


def test_unmapped_output_contract_is_not_counted_as_a_semantic_wrong_answer() -> None:
    record = _record("RQ01", passed=False)
    record["checks"]["resultContract"] = False
    record["checks"]["result"] = None
    record["subqueries"][0]["checks"]["result"] = None
    record["semanticResultPass"] = None
    record["finalResultPass"] = None
    record["queryPipelinePass"] = False

    metrics = calculate_metrics([record])

    assert metrics["semanticResultCoverage"] == 0.0
    assert metrics["semanticResultAccuracy"] == 0.0
    assert metrics["finalResultEvaluationCoverage"] == 0.0


def test_infrastructure_errors_reduce_coverage_but_not_accuracy() -> None:
    passed = _record("RQ01", passed=True)
    error = {
        "caseId": "RQ02",
        "contractId": "RQ02",
        "suite": "canonical",
        "route": "SQL",
        "status": "ERROR",
        "supportStatus": "FULLY_EVALUATED",
        "queryPipelinePass": False,
        "finalResultEvaluated": True,
        "finalResultPass": False,
    }

    metrics = calculate_metrics([passed, error])

    assert metrics["evaluationCoverage"] == 0.5
    assert metrics["queryPipelineAccuracy"] == 1.0
    assert metrics["finalResultAccuracy"] == 1.0


def test_hybrid_infrastructure_errors_do_not_reduce_split_accuracy() -> None:
    passed = _record("RQ18", passed=True, route="HYBRID")
    error = {
        "caseId": "RQ19",
        "contractId": "RQ19",
        "suite": "canonical",
        "route": "HYBRID",
        "status": "ERROR",
        "supportStatus": "QUERY_EVALUATED_FINAL_JOIN_PENDING",
    }

    metrics = calculate_metrics([passed, error])

    assert metrics["evaluationCoverage"] == 0.5
    assert metrics["hybridSplitAccuracy"] == 1.0


def test_suite_scores_keep_canonical_and_robustness_separate() -> None:
    canonical = _record("RQ01", passed=True)
    robustness = _record("RB01", passed=False)
    robustness["contractId"] = "RQ01"
    robustness["suite"] = "robustness"

    metrics = calculate_metrics([canonical, robustness])

    assert metrics["suiteScores"]["canonical"]["queryPipelineAccuracy"] == 1.0
    assert metrics["suiteScores"]["robustness"]["queryPipelineAccuracy"] == 0.0


def test_source_mode_marks_execution_retry_and_composition_not_applicable(
    tmp_path: Path,
) -> None:
    record = _record("RQ01", passed=True)
    record["executionMode"] = "source"
    metrics = calculate_metrics([record])

    assert metrics["promotionEligible"] is False
    assert metrics["stageAccuracy"]["execution"] is None
    assert metrics["stageAccuracy"]["composition"] is None
    assert metrics["stageAccuracy"]["finalResult"] is None
    assert metrics["firstAttemptExecutionRate"] is None
    assert metrics["retryRecoveryRate"] is None
    assert metrics["finalResultAccuracy"] is None

    summary = build_summary(
        EvaluationRun([record], {"sha256": "snapshot"}, False),
        model="test-model",
        commit="commit",
        validate_gold=False,
        working_tree_dirty=False,
    )
    write_artifacts(tmp_path, summary, [record])
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 평가 경로 | source |" in report
    assert "| 승격 지표 사용 | 아니오 |" in report
    assert "| 최초 실행 성공률 | N/A |" in report
    assert "| retry 복구율 | N/A |" in report


def test_performance_gate_compares_compatible_baseline() -> None:
    metrics = {
        "averageModelCallCount": 4.5,
        "p95LatencyMs": 190.0,
        "pipelinePassByRoute": {},
        "stageAccuracy": {},
        "caseTrialSummary": {},
    }
    baseline = {
        "compatible": True,
        "averageModelCallCount": 3.0,
        "p95LatencyMs": 100.0,
        "artifactSha256": "abc",
    }

    gate = build_regression_gate([], metrics, baseline)
    check = next(
        item
        for item in gate["checks"]
        if item["name"] == "model call and latency budget"
    )

    assert check["status"] == "PASS"
    assert check["actual"]["baselineArtifactSha256"] == "abc"


def test_report_is_compact_and_keeps_query_details_in_evaluation_json(
    tmp_path: Path,
) -> None:
    record = _record("RQ01", passed=False)
    record.update(
        {
            "question": "받은 질문",
            "planningError": "join key missing",
            "planningResponse": '{"tool_plan":["sql"]}',
            "failureReasons": ["RESULT_VALUE_MISMATCH"],
            "queryPipelinePass": False,
            "semanticResultPass": False,
            "subqueries": [
                {
                    "id": "sql_product_cost",
                    "tool": "sql",
                    "status": "FAIL",
                    "expectedQuestion": "기대 책임",
                    "requiredOutputs": ["productId"],
                    "goldFile": "RQ01.sql",
                    "businessRules": ["제품명을 정확히 찾는다."],
                    "upstreamInputs": {},
                    "failureCategory": "RESULT_VALUE_MISMATCH",
                    "error": "RESULT_HASH_MISMATCH",
                    "generatedQuery": "SELECT 1 AS productId",
                    "candidateSample": [{"productId": "candidate"}],
                    "goldSample": [{"productId": "gold"}],
                    "checks": {"result": False},
                }
            ],
        }
    )
    result = EvaluationRun([record], {"sha256": "snapshot"}, False)
    summary = build_summary(
        result,
        model="test-model",
        commit="d5ad7a7123456789",
        validate_gold=False,
        working_tree_dirty=False,
    )
    summary["generatedAt"] = "2026-08-27T00:35:00+00:00"
    for legacy_name in ("summary.json", "cases.jsonl", "junit.xml"):
        (tmp_path / legacy_name).write_text("legacy", encoding="utf-8")

    write_artifacts(tmp_path, summary, [record])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## 실행 정보" in report
    assert "| 실행 시각 | 2026-08-27 09:35:00 KST |" in report
    assert "| 커밋 | d5ad7a7 |" in report
    assert "| 작업 상태 | clean |" in report
    assert "| 평가 suite | canonical |" in report
    assert "| 평가 질의 | RQ01 (1개) |" in report
    assert "| Route | SQL |" in report
    assert "| 반복 실행 | 1회 |" in report
    assert "## 핵심 점수" in report
    assert "| 채점 실행 완료 (인프라 정상) | 1/1 | 100% |" in report
    assert "| 엄격 파이프라인 PASS | 0/1 | 0% |" in report
    assert "| 의미 결과 비교 가능 | 1/1 | 100% |" in report
    assert "| 검증된 의미 PASS | 0/1 | 0% |" in report
    assert "| 비교 가능한 결과 중 정확도 | 0/1 | 0% |" in report
    assert "## Route별 엄격 PASS" in report
    assert "| SQL | 0/1 | 0% |" in report
    assert "| RQ01 | 1 | SQL |" in report
    assert "결과값 불일치" in report
    assert "RESULT_VALUE_MISMATCH" not in report
    assert "SELECT 1 AS productId" not in report
    assert '"productId": "candidate"' not in report
    assert "받은 질문" not in report

    assert {path.name for path in tmp_path.iterdir()} == {
        "evaluation.json",
        "report.md",
    }
    evaluation = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    case = evaluation["cases"][0]
    assert case["subqueries"][0]["generatedQuery"] == "SELECT 1 AS productId"
    assert case["subqueries"][0]["candidateSample"] == [{"productId": "candidate"}]
    assert evaluation["summary"]["workingTreeDirty"] is False


def test_report_shows_clean_and_dirty_working_tree_status(tmp_path: Path) -> None:
    for dirty, label in ((False, "clean"), (True, "dirty")):
        output_dir = tmp_path / label
        record = _record("RQ01", passed=True)
        result = EvaluationRun([record], {"sha256": "snapshot"}, False)
        summary = build_summary(
            result,
            model="test-model",
            commit="commit",
            validate_gold=False,
            working_tree_dirty=dirty,
        )

        write_artifacts(output_dir, summary, [record])

        evaluation = json.loads(
            (output_dir / "evaluation.json").read_text(encoding="utf-8")
        )
        report = (output_dir / "report.md").read_text(encoding="utf-8")
        assert evaluation["summary"]["workingTreeDirty"] is dirty
        assert f"| 작업 상태 | {label} |" in report
        assert ("공식 기준선으로 사용하지 마세요" in report) is dirty


def test_report_final_accuracy_excludes_results_that_could_not_be_compared(
    tmp_path: Path,
) -> None:
    passed = _record("RQ01", passed=True)
    not_compared = _record("RQ02", passed=False)
    not_compared["finalResultPass"] = None
    not_compared["semanticResultPass"] = None
    not_compared["checks"]["result"] = None
    result = EvaluationRun([passed, not_compared], {"sha256": "snapshot"}, False)
    summary = build_summary(
        result,
        model="test-model",
        commit="commit",
        validate_gold=False,
        working_tree_dirty=False,
    )

    write_artifacts(tmp_path, summary, [passed, not_compared])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 최종 결과 평가 대상 | 2/2 | 100% |" in report
    assert "| 최종 결과 비교 가능 | 1/2 | 50% |" in report
    assert "| 최종 결과 정확도 | 1/1 | 100% |" in report


def test_gold_report_shows_validation_status_route_and_partial_count(
    tmp_path: Path,
) -> None:
    record = {
        "caseId": "RQ18",
        "contractId": "RQ18",
        "suite": "canonical",
        "run": 1,
        "question": "Gold 검증 질문",
        "route": "HYBRID",
        "supportStatus": "QUERY_EVALUATED_FINAL_JOIN_PENDING",
        "status": "GOLD_VALIDATED",
        "subqueries": [{"status": "PASS"}, {"status": "PASS"}],
    }
    result = EvaluationRun([record], {"sha256": "snapshot"}, False)
    summary = build_summary(
        result,
        model=None,
        commit="commit",
        validate_gold=True,
        working_tree_dirty=True,
    )

    write_artifacts(tmp_path, summary, [record])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 모델 | gold-only |" in report
    assert "| 작업 상태 | dirty |" in report
    assert "| Gold 검증 완료 | 1/1 | 100% |" in report
    assert "| Gold 부분 쿼리 PASS | 2/2 | 100% |" in report
    assert "| Case ID | Run | Route |" in report
    assert "| RQ ID |" not in report
    assert "| RQ18 | 1 | HYBRID | - | - | - | PASS | PASS | - | - |" in report
    assert "Gold 검증 질문" not in report


def test_report_shows_summary_and_case_infrastructure_errors(tmp_path: Path) -> None:
    record = {
        "caseId": "RQ01",
        "contractId": "RQ01",
        "suite": "canonical",
        "run": 1,
        "question": "질문",
        "route": "SQL",
        "supportStatus": "FULLY_EVALUATED",
        "status": "ERROR",
        "error": "API unavailable",
    }
    result = EvaluationRun([record], {}, True)
    summary = build_summary(
        result,
        model="test-model",
        commit="commit",
        validate_gold=False,
        working_tree_dirty=False,
    )
    summary["error"] = "DB unavailable"

    write_artifacts(tmp_path, summary, [record])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 모델 | test-model |" in report
    assert "| 인프라 오류 | DB unavailable |" in report
    assert "| 채점 실행 완료 (인프라 정상) | 0/1 | 0% |" in report
    assert "| RQ01 | 1 | SQL | - | - | - | - | - | - | API unavailable |" in report
