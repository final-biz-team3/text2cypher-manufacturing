import json
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
        "run": 1,
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
    assert "| 의미 결과 정확도 | 0/1 | 0% |" in report
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
