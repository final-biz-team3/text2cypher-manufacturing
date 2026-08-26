from pathlib import Path

from evaluation.reporting import (
    build_summary,
    calculate_metrics,
    write_artifacts,
)
from evaluation.runner import EvaluationRun


def _record(case_id: str, *, passed: bool, route: str = "SQL") -> dict:
    return {
        "caseId": case_id,
        "contractId": case_id,
        "suite": "canonical",
        "route": route,
        "status": "PASS" if passed else "FAIL",
        "supportStatus": "FULLY_EVALUATED",
        "queryPipelinePass": passed,
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
            _record("RQ01", passed=True),
            _record("RQ01", passed=True),
            _record("RQ02", passed=True),
            _record("RQ02", passed=False),
        ]
    )

    assert metrics["queryPipelineAccuracy"] == 0.75
    assert metrics["caseStability"] == 0.5
    assert metrics["stablePassCaseIds"] == ["RQ01"]
    assert metrics["persistentFailureCaseIds"] == []


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


def test_report_contains_question_gold_rules_and_failure_reason(tmp_path: Path) -> None:
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
                    "checks": {"result": False},
                }
            ],
        }
    )
    result = EvaluationRun([record], {"sha256": "snapshot"}, False)
    summary = build_summary(
        result,
        model="test-model",
        commit="commit",
        validate_gold=False,
    )

    write_artifacts(tmp_path, summary, [record])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "받은 질문" in report
    assert "Mode: `report-only`" in report
    assert "join key missing" in report
    assert '{"tool_plan":["sql"]}' in report
    assert "기대 책임" in report
    assert "RQ01.sql" in report
    assert "제품명을 정확히 찾는다." in report
    assert "RESULT_VALUE_MISMATCH" in report


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
    )

    write_artifacts(tmp_path, summary, [record])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Model: `gold-only`" in report
    assert "Gold 검증 질문" in report
    assert "HYBRID" in report
    assert "GOLD_VALIDATED" in report
    assert "2 / 2" in report
    assert "NOT_EVALUATED" not in report


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
    )
    summary["error"] = "DB unavailable"

    write_artifacts(tmp_path, summary, [record])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Model: `test-model`" in report
    assert "Infrastructure error: `DB unavailable`" in report
    assert "Error: `API unavailable`" in report
