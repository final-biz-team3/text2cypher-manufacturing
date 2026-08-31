"""Evidence-backed anti-overfitting and change-safety scorecard."""

import re
from pathlib import Path
from typing import Any


def _control(
    name: str,
    passed: bool,
    evidence: str,
    reason: str,
    *,
    critical: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "critical": critical,
        "status": "PASS" if passed else "FAIL",
        "evidence": evidence,
        "deductionReason": None if passed else reason,
    }


def _area(name: str, controls: list[dict[str, Any]]) -> dict[str, Any]:
    score = sum(item["status"] == "PASS" for item in controls) * 2.5
    return {"name": name, "score": score, "controls": controls}


def _production_text(project_root: Path) -> str:
    paths = [
        path
        for directory in ("agents", "api", "orchestrator")
        for path in (project_root / "backend" / directory).rglob("*.py")
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _strict_score_range(records: list[dict[str, Any]]) -> float | None:
    by_run: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        run = record.get("run")
        if isinstance(run, int):
            by_run.setdefault(run, []).append(record)
    if len(by_run) < 3:
        return None
    scores = [
        sum(item.get("queryPipelinePass") is True for item in run_records)
        / len(run_records)
        for run_records in by_run.values()
        if run_records
    ]
    return round(max(scores) - min(scores), 6) if len(scores) >= 3 else None


def build_quality_scorecard(
    records: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    working_tree_dirty: bool | None,
    project_root: Path,
) -> dict[str, Any]:
    completed = [record for record in records if record.get("status") != "ERROR"]
    production_text = _production_text(project_root)
    cli_text = (project_root / "backend/evaluation/cli.py").read_text(encoding="utf-8")
    catalog_text = (project_root / "backend/orchestrator/output_catalog.py").read_text(
        encoding="utf-8"
    )
    runner_text = (project_root / "backend/evaluation/runner.py").read_text(
        encoding="utf-8"
    )
    unique_cases = {str(record.get("caseId")) for record in records}
    unique_contracts = {str(record.get("contractId")) for record in records}
    suites = {str(record.get("suite")) for record in records}
    run_count = max((int(record.get("run", 0)) for record in records), default=0)
    score_range = _strict_score_range(records)

    areas = [
        _area(
            "Production 경로 정합성",
            [
                _control(
                    "production orchestrator 사용",
                    bool(completed)
                    and all(
                        item.get("executionMode") == "orchestrator"
                        for item in completed
                    ),
                    "cases[].executionMode",
                    "orchestrator 경로 실행 증거가 없습니다.",
                    critical=True,
                ),
                _control(
                    "실제 retry와 composer 관측",
                    bool(completed)
                    and all("attemptCount" in item for item in completed)
                    and all("composedResult" in item for item in completed),
                    "cases[].attemptCount, cases[].composedResult",
                    "실행 또는 composition 관측값이 누락됐습니다.",
                ),
                _control(
                    "source 승격 제외",
                    metrics.get("promotionEligible")
                    == (metrics.get("executionMode") == "orchestrator"),
                    "summary.metrics.promotionEligible",
                    "execution mode와 승격 가능 여부가 일치하지 않습니다.",
                    critical=True,
                ),
                _control(
                    "DB 자원 종료 경로",
                    all(
                        token in cli_text
                        for token in (
                            "close_reader_driver()",
                            "close_pool()",
                            "database.close()",
                        )
                    ),
                    "backend/evaluation/cli.py",
                    "DB 자원 종료 호출을 확인하지 못했습니다.",
                ),
            ],
        ),
        _area(
            "Gold 격리",
            [
                _control(
                    "production 평가 경로 import 없음",
                    "queries/evaluation" not in production_text
                    and "evaluation.manifest" not in production_text,
                    "backend/tests/evaluation/test_production_tuning_guards.py",
                    "production에서 평가 경로를 참조합니다.",
                    critical=True,
                ),
                _control(
                    "평가 ID 없음",
                    re.search(r"\b(?:RQ|HQ)\d{2}\b", production_text) is None,
                    "backend/tests/evaluation/test_production_tuning_guards.py",
                    "production에 평가 ID가 있습니다.",
                    critical=True,
                ),
                _control(
                    "output registry 독립",
                    "evaluation" not in catalog_text and "manifest" not in catalog_text,
                    "backend/orchestrator/output_catalog.py",
                    "output registry가 평가 코드에 의존합니다.",
                    critical=True,
                ),
                _control(
                    "생성 입력은 planner 계약만 사용",
                    "required_outputs=actual.get" in runner_text
                    and "business_rules=expected" not in runner_text,
                    "backend/evaluation/runner.py",
                    "생성 경로의 Gold 격리를 증명하지 못했습니다.",
                    critical=True,
                ),
            ],
        ),
        _area(
            "Holdout 독립성",
            [
                _control(
                    label,
                    False,
                    "externalBlindHoldout: unavailable",
                    "외부 blind 담당자의 증거가 제공되지 않았습니다.",
                    critical=True,
                )
                for label in (
                    "외부 소유자 확인",
                    "사전 manifest hash 동결",
                    "최초 실행 전 미열람",
                    "실패 후 세트 재사용 금지",
                )
            ],
        ),
        _area(
            "계약 무결성",
            [
                _control(
                    "route draft와 execution plan 분리",
                    bool(completed)
                    and all("routeDraft" in item for item in completed)
                    and all("subqueryPlan" in item for item in completed),
                    "cases[].routeDraft, cases[].subqueryPlan",
                    "route draft 또는 완성 plan 기록이 누락됐습니다.",
                    critical=True,
                ),
                _control(
                    "planning 항목 개별 채점",
                    bool(completed)
                    and all(
                        all(
                            key in item.get("checks", {})
                            for key in (
                                "split",
                                "requiredOutputs",
                                "binding",
                            )
                        )
                        for item in completed
                    ),
                    "cases[].checks",
                    "planning 세부 채점값이 누락됐습니다.",
                    critical=True,
                ),
                _control(
                    "final 결과 전수 평가",
                    metrics.get("finalResultEvaluationCoverage") == 1.0,
                    "summary.metrics.finalResultEvaluationCoverage",
                    "final 결과 평가 coverage가 100%가 아닙니다.",
                    critical=True,
                ),
                _control(
                    "truncation 전 결과 계약 검증",
                    'composed.get("truncated") is False' in runner_text,
                    "backend/evaluation/runner.py:_score_final_result",
                    "전체 결과와 truncation 검증을 확인하지 못했습니다.",
                    critical=True,
                ),
            ],
        ),
        _area(
            "데이터셋 대표성",
            [
                _control(
                    "30개 독립 contract",
                    len(unique_contracts) >= 30,
                    "summary.evaluationSet.contracts",
                    "독립 contract가 30개 미만입니다.",
                ),
                _control(
                    "60개 robustness 변형 분리",
                    "robustness" in suites
                    and sum(case_id.startswith("RB") for case_id in unique_cases) >= 60,
                    "summary.evaluationSet.caseIds, suites",
                    "60개 robustness 변형 실행 증거가 없습니다.",
                ),
                _control(
                    "route와 family 분포 공개",
                    all(
                        route in metrics.get("pipelinePassByRoute", {})
                        for route in ("SQL", "GRAPH", "HYBRID")
                    ),
                    "summary.metrics.pipelinePassByRoute",
                    "route 분포 지표가 누락됐습니다.",
                ),
                _control(
                    "외부 blind 30개",
                    False,
                    "externalBlindHoldout: unavailable",
                    "새로운 외부 blind 30개가 제공되지 않았습니다.",
                    critical=True,
                ),
            ],
        ),
        _area(
            "반복 안정성",
            [
                _control(
                    "동일 snapshot 3회",
                    run_count >= 3 and bool(records),
                    "summary.evaluationSet.runs, summary.snapshot.sha256",
                    "동일 snapshot 3회 실행이 완료되지 않았습니다.",
                    critical=True,
                ),
                _control(
                    "strict 변동 폭 3%p 이하",
                    score_range is not None and score_range <= 0.03,
                    f"derived.strictScoreRange={score_range}",
                    "3회 strict 점수 변동 폭을 확인하지 못했거나 3%p를 넘었습니다.",
                    critical=True,
                ),
                _control(
                    "variable case 공개",
                    "variableCaseIds" in metrics and "caseTrialSummary" in metrics,
                    "summary.metrics.variableCaseIds, caseTrialSummary",
                    "변동 case 정보가 누락됐습니다.",
                ),
                _control(
                    "인프라 coverage 100%",
                    metrics.get("evaluationCoverage") == 1.0,
                    "summary.metrics.evaluationCoverage",
                    "evaluation coverage가 100%가 아닙니다.",
                    critical=True,
                ),
            ],
        ),
        _area(
            "과설계 억제",
            [
                _control(
                    "제한된 route와 transform",
                    'SUPPORTED_TOOLS = {"sql", "graph"}' in production_text
                    and "bom_shortage_v1" in production_text,
                    "backend/orchestrator/planning.py",
                    "허용 범위가 명시적으로 제한되지 않았습니다.",
                ),
                _control(
                    "평가별 모델 분기 없음",
                    re.search(r"\b(?:RQ|HQ)\d{2}\b", production_text) is None,
                    "backend/tests/evaluation/test_production_tuning_guards.py",
                    "평가별 production 분기가 있습니다.",
                    critical=True,
                ),
                _control(
                    "public chat 응답 불변",
                    (project_root / "backend/tests/api/test_chat.py").exists(),
                    "backend/tests/api/test_chat.py",
                    "public chat 계약 검증 파일이 없습니다.",
                ),
                _control(
                    "schema catalog 중심 최소 변경",
                    "DOMAIN_ALIAS_REGISTRY" in catalog_text
                    and "class OutputCatalog" in catalog_text,
                    "backend/orchestrator/output_catalog.py",
                    "output planning이 schema catalog로 제한되지 않았습니다.",
                ),
            ],
        ),
        _area(
            "변경 안전성",
            [
                _control(
                    "clean working tree",
                    working_tree_dirty is False,
                    "summary.workingTreeDirty",
                    "작업 트리가 clean이 아닙니다.",
                    critical=True,
                ),
                _control(
                    "승인 DB snapshot 확인",
                    bool(records) and metrics.get("evaluationCoverage") == 1.0,
                    "summary.snapshot.sha256, summary.metrics.evaluationCoverage",
                    "승인 snapshot 통합 실행 증거가 없습니다.",
                    critical=True,
                ),
                _control(
                    "정적 방어 테스트 존재",
                    (
                        project_root
                        / "backend/tests/evaluation/test_production_tuning_guards.py"
                    ).exists(),
                    "backend/tests/evaluation/test_production_tuning_guards.py",
                    "정적 방어 테스트가 없습니다.",
                ),
                _control(
                    "미해결 P0/P1 없음",
                    False,
                    "reviewArtifact: unavailable",
                    "최종 review artifact가 제공되지 않았습니다.",
                    critical=True,
                ),
            ],
        ),
    ]
    average = round(sum(area["score"] for area in areas) / len(areas), 3)
    critical_failures = [
        f"{area['name']}: {control['name']}"
        for area in areas
        for control in area["controls"]
        if control["critical"] and control["status"] == "FAIL"
    ]
    passes = (
        average > 9.5
        and all(area["score"] >= 9.0 for area in areas)
        and not critical_failures
    )
    return {
        "averageScore": average,
        "passesThreshold": passes,
        "criticalFailures": critical_failures,
        "areas": areas,
    }
