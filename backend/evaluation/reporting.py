"""평가 지표와 통합 JSON/Markdown artifact 출력."""

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from evaluation.runner import EvaluationRun


def _ratio(passed: int, total: int) -> float:
    return round(passed / total, 6) if total else 0.0


def _pipeline_accuracy(records: list[dict[str, Any]]) -> float:
    applicable = [record for record in records if record.get("status") != "ERROR"]
    return _ratio(
        sum(record.get("queryPipelinePass") is True for record in applicable),
        len(applicable),
    )


def _check_accuracy(records: list[dict[str, Any]], check: str) -> float:
    applicable = [
        record["checks"][check]
        for record in records
        if record.get("status") != "ERROR"
        and isinstance(record.get("checks"), dict)
        and isinstance(record["checks"].get(check), bool)
    ]
    return _ratio(sum(value is True for value in applicable), len(applicable))


def _semantic_accuracy(records: list[dict[str, Any]]) -> float:
    applicable = [
        record
        for record in records
        if record.get("status") != "ERROR"
        and isinstance(record.get("semanticResultPass"), bool)
    ]
    return _ratio(
        sum(record["semanticResultPass"] is True for record in applicable),
        len(applicable),
    )


def _semantic_coverage(records: list[dict[str, Any]]) -> float:
    completed = [record for record in records if record.get("status") != "ERROR"]
    applicable = [
        record
        for record in completed
        if isinstance(record.get("semanticResultPass"), bool)
    ]
    return _ratio(len(applicable), len(completed))


def _suite_score(records: list[dict[str, Any]]) -> dict[str, Any]:
    fully = [record for record in records if record.get("finalResultEvaluated")]
    final_applicable = [
        record
        for record in fully
        if record.get("status") != "ERROR"
        and isinstance(record.get("finalResultPass"), bool)
    ]
    return {
        "runs": len(records),
        "evaluationCoverage": _ratio(
            sum(record.get("status") != "ERROR" for record in records), len(records)
        ),
        "queryPipelineAccuracy": _pipeline_accuracy(records),
        "semanticResultCoverage": _semantic_coverage(records),
        "semanticResultAccuracy": _semantic_accuracy(records),
        "verifiedSemanticPassRate": _ratio(
            sum(record.get("semanticResultPass") is True for record in records),
            sum(record.get("status") != "ERROR" for record in records),
        ),
        "finalResultEvaluationCoverage": _ratio(
            len(final_applicable),
            sum(
                record.get("status") != "ERROR"
                and record.get("finalResultEvaluated") is True
                for record in records
            ),
        ),
        "finalResultAccuracy": _ratio(
            sum(record["finalResultPass"] is True for record in final_applicable),
            len(final_applicable),
        ),
        "routingAccuracy": _check_accuracy(records, "routing"),
        "splitAccuracy": _check_accuracy(records, "split"),
    }


def calculate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """반복 실행과 suite를 섞지 않고 전체 및 세부 지표를 계산한다."""
    total = len(records)
    non_error = [record for record in records if record.get("status") != "ERROR"]
    fully = [record for record in records if record.get("finalResultEvaluated")]
    final_applicable = [
        record
        for record in fully
        if record.get("status") != "ERROR"
        and isinstance(record.get("finalResultPass"), bool)
    ]
    hybrid = [
        record
        for record in records
        if record.get("route") == "HYBRID" and record.get("status") != "ERROR"
    ]

    stage_accuracy: dict[str, float] = {}
    for stage in (
        "entity",
        "routing",
        "split",
        "generation",
        "execution",
        "resultContract",
        "result",
    ):
        stage_accuracy[stage] = _check_accuracy(records, stage)

    partials: dict[str, list[bool]] = {"sql": [], "graph": []}
    partial_attempted = {"sql": 0, "graph": 0}
    partial_blocked = {"sql": 0, "graph": 0}
    for record in records:
        for subquery in record.get("subqueries", []):
            tool = subquery.get("tool")
            if tool not in partials:
                continue
            if subquery.get("status") == "BLOCKED_BY_DEPENDENCY":
                partial_blocked[tool] += 1
                continue
            partial_attempted[tool] += 1
            result = subquery.get("checks", {}).get("result")
            if isinstance(result, bool):
                partials[tool].append(result)

    records_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_case[record["caseId"]].append(record)
    stable_pass_case_ids = sorted(
        case_id
        for case_id, case_records in records_by_case.items()
        if all(record.get("queryPipelinePass") is True for record in case_records)
    )
    persistent_failure_case_ids = sorted(
        case_id
        for case_id, case_records in records_by_case.items()
        if (completed := [r for r in case_records if r.get("status") != "ERROR"])
        and all(record.get("queryPipelinePass") is not True for record in completed)
    )

    records_by_route = {
        route: [record for record in records if record.get("route") == route]
        for route in ("SQL", "GRAPH", "HYBRID")
    }
    routing_accuracy_by_route = {
        route: _check_accuracy(route_records, "routing")
        for route, route_records in records_by_route.items()
    }
    pipeline_pass_by_route = {
        route: _pipeline_accuracy(route_records)
        for route, route_records in records_by_route.items()
    }
    suite_scores = {
        suite: _suite_score(suite_records)
        for suite in sorted(
            {
                str(record["suite"])
                for record in records
                if record.get("suite") is not None
            }
        )
        for suite_records in [
            [record for record in records if record.get("suite") == suite]
        ]
    }
    selected_contracts = {record["contractId"] for record in records}
    fully_contracts = {
        record["contractId"]
        for record in records
        if record.get("supportStatus") == "FULLY_EVALUATED"
    }

    return {
        "evaluatedRuns": total,
        "evaluationCoverage": _ratio(len(non_error), total),
        "queryPipelineAccuracy": _pipeline_accuracy(records),
        "semanticResultCoverage": _semantic_coverage(records),
        "semanticResultAccuracy": _semantic_accuracy(records),
        "verifiedSemanticPassRate": _ratio(
            sum(record.get("semanticResultPass") is True for record in non_error),
            len(non_error),
        ),
        "finalResultCoverage": _ratio(len(fully_contracts), len(selected_contracts)),
        "finalResultAccuracy": _ratio(
            sum(record["finalResultPass"] is True for record in final_applicable),
            len(final_applicable),
        ),
        "finalResultEvaluationCoverage": _ratio(
            len(final_applicable),
            sum(record.get("status") != "ERROR" for record in fully),
        ),
        "verifiedFinalResultPassRate": _ratio(
            sum(record.get("finalResultPass") is True for record in fully),
            sum(record.get("status") != "ERROR" for record in fully),
        ),
        "hybridSplitAccuracy": _ratio(
            sum(record.get("checks", {}).get("split") is True for record in hybrid),
            len(hybrid),
        ),
        "sqlPartialCoverage": _ratio(len(partials["sql"]), partial_attempted["sql"]),
        "sqlPartialAccuracy": _ratio(sum(partials["sql"]), len(partials["sql"])),
        "graphPartialCoverage": _ratio(
            len(partials["graph"]), partial_attempted["graph"]
        ),
        "graphPartialAccuracy": _ratio(sum(partials["graph"]), len(partials["graph"])),
        "blockedPartialCount": partial_blocked,
        "stageAccuracy": stage_accuracy,
        "routingAccuracy": stage_accuracy["routing"],
        "routingAccuracyByRoute": routing_accuracy_by_route,
        "pipelinePassByRoute": pipeline_pass_by_route,
        "caseStability": _ratio(len(stable_pass_case_ids), len(records_by_case)),
        "stablePassCaseIds": stable_pass_case_ids,
        "persistentFailureCaseIds": persistent_failure_case_ids,
        "fullyEvaluatedCount": len(fully_contracts),
        "suiteScores": suite_scores,
    }


def build_summary(
    result: EvaluationRun,
    *,
    model: str | None,
    commit: str,
    validate_gold: bool,
    working_tree_dirty: bool | None = None,
) -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "model": model,
        "commit": commit,
        "workingTreeDirty": working_tree_dirty,
        "goldValidationOnly": validate_gold,
        "snapshot": result.snapshot,
        "evaluationSet": {
            "caseIds": sorted({str(record["caseId"]) for record in result.records}),
            "contracts": sorted(
                {str(record["contractId"]) for record in result.records}
            ),
            "suites": sorted({str(record["suite"]) for record in result.records}),
            "runs": max(
                (int(record.get("run", 0)) for record in result.records), default=0
            ),
        },
        "metrics": calculate_metrics(result.records) if not validate_gold else {},
        "infrastructureError": result.infrastructure_error,
    }


def _report_markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    failure_labels = {
        "ENTITY_MISMATCH": "엔티티 불일치",
        "ROUTE_MISMATCH": "라우팅 불일치",
        "SUBQUERY_INTEGRATION_CONTRACT_MISMATCH": "서브쿼리 분할·연결 계약 불일치",
        "READ_ONLY_VIOLATION": "읽기 전용 정책 위반",
        "QUERY_GENERATION_ERROR": "쿼리 생성 오류",
        "QUERY_TIMEOUT": "쿼리 시간 초과",
        "RESULT_CONTRACT_MISMATCH": "결과 필드 계약 불일치",
        "QUERY_EXECUTION_ERROR": "쿼리 실행 오류",
        "RESULT_VALUE_MISMATCH": "결과값 불일치",
        "DEPENDENCY_BLOCKED": "선행 단계 실패로 미실행",
    }

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    def check_label(value: Any) -> str:
        if value is True:
            return "PASS"
        if value is False:
            return "FAIL"
        return "-"

    def percentage(passed: int, total: int) -> str:
        if total == 0:
            return "-"
        value = passed / total * 100
        precision = 0 if value.is_integer() else 1
        return f"{value:.{precision}f}%"

    def score_row(label: str, passed: int, total: int) -> str:
        return f"| {label} | {passed}/{total} | {percentage(passed, total)} |"

    def failure_reason(record: dict[str, Any]) -> str:
        reasons = [
            failure_labels.get(str(reason), str(reason))
            for reason in record.get("failureReasons", [])
        ]
        for key in ("planningError", "error"):
            if record.get(key):
                reasons.append(str(record[key]))
        if not reasons:
            reasons.extend(
                failure_labels.get(
                    str(subquery["failureCategory"]),
                    str(subquery["failureCategory"]),
                )
                for subquery in record.get("subqueries", [])
                if subquery.get("failureCategory")
            )
        return cell(", ".join(dict.fromkeys(reasons)) or "-")

    try:
        generated_at = datetime.fromisoformat(str(summary.get("generatedAt")))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        generated_at_label = generated_at.astimezone(ZoneInfo("Asia/Seoul")).strftime(
            "%Y-%m-%d %H:%M:%S KST"
        )
    except (TypeError, ValueError):
        generated_at_label = "unavailable"

    commit = str(summary.get("commit") or "unknown")
    commit_label = commit[:7] if commit != "unknown" else commit
    dirty = summary.get("workingTreeDirty")
    if dirty is True:
        working_tree_label = "dirty"
    elif dirty is False:
        working_tree_label = "clean"
    else:
        working_tree_label = "unknown"
    snapshot = str(summary.get("snapshot", {}).get("sha256", "unavailable"))
    snapshot_label = f"{snapshot[:8]}…" if len(snapshot) > 8 else snapshot
    evaluation_set = summary.get("evaluationSet", {})
    case_ids = [str(case_id) for case_id in evaluation_set.get("caseIds", [])]
    rq_numbers = [
        int(case_id[2:])
        for case_id in case_ids
        if case_id.startswith("RQ") and case_id[2:].isdigit()
    ]
    if (
        len(rq_numbers) == len(case_ids)
        and len(case_ids) > 1
        and rq_numbers == list(range(rq_numbers[0], rq_numbers[-1] + 1))
    ):
        case_ids_label = f"{case_ids[0]}~{case_ids[-1]} ({len(case_ids)}개)"
    elif case_ids:
        case_ids_label = f"{', '.join(case_ids)} ({len(case_ids)}개)"
    else:
        case_ids_label = "unavailable"
    suites = ", ".join(evaluation_set.get("suites", [])) or "unavailable"
    runs = int(evaluation_set.get("runs", 0))
    route_order = ("SQL", "GRAPH", "HYBRID")
    selected_routes = [
        route
        for route in route_order
        if any(record.get("route") == route for record in records)
    ]
    routes_label = ", ".join(selected_routes) or "unavailable"

    gold_validation_only = summary.get("goldValidationOnly") is True
    model_label = (
        "gold-only" if gold_validation_only else summary.get("model") or "unavailable"
    )
    lines = [
        "# Text-to-query 평가 보고서",
        "",
        "## 실행 정보",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 실행 시각 | {generated_at_label} |",
        f"| 모델 | {cell(model_label)} |",
        f"| 커밋 | {cell(commit_label)} |",
        f"| 작업 상태 | {working_tree_label} |",
        f"| DB snapshot | {cell(snapshot_label)} |",
        f"| 평가 suite | {cell(suites)} |",
        f"| 평가 질의 | {cell(case_ids_label)} |",
        f"| Route | {routes_label} |",
        f"| 반복 실행 | {runs}회 |",
    ]
    if summary.get("error"):
        lines.append(f"| 인프라 오류 | {cell(summary['error'])} |")
    if dirty is True:
        lines.extend(
            [
                "",
                "> [!WARNING]",
                "> 커밋되지 않은 변경이 포함된 실행이므로 공식 기준선으로 사용하지 마세요.",
            ]
        )
    lines.extend(
        [
            "",
            "## 핵심 점수",
            "",
            "| 지표 | 건수 | 비율 |",
            "|---|---:|---:|",
        ]
    )
    if gold_validation_only:
        validated = sum(record.get("status") == "GOLD_VALIDATED" for record in records)
        partials = [
            subquery for record in records for subquery in record.get("subqueries", [])
        ]
        passed_partials = sum(subquery.get("status") == "PASS" for subquery in partials)
        lines.extend(
            [
                score_row("Gold 검증 완료", validated, len(records)),
                score_row("Gold 부분 쿼리 PASS", passed_partials, len(partials)),
            ]
        )
    else:
        completed = [record for record in records if record.get("status") != "ERROR"]
        pipeline_passed = sum(
            record.get("queryPipelinePass") is True for record in completed
        )
        semantic_applicable = [
            record
            for record in completed
            if isinstance(record.get("semanticResultPass"), bool)
        ]
        semantic_passed = sum(
            record.get("semanticResultPass") is True for record in semantic_applicable
        )
        final_evaluated = [
            record for record in completed if record.get("finalResultEvaluated") is True
        ]
        final_applicable = [
            record
            for record in final_evaluated
            if isinstance(record.get("finalResultPass"), bool)
        ]
        final_passed = sum(
            record.get("finalResultPass") is True for record in final_applicable
        )
        hybrid = [record for record in completed if record.get("route") == "HYBRID"]
        hybrid_split_passed = sum(
            record.get("checks", {}).get("split") is True for record in hybrid
        )
        lines.extend(
            [
                score_row("채점 실행 완료 (인프라 정상)", len(completed), len(records)),
                score_row("엄격 파이프라인 PASS", pipeline_passed, len(completed)),
                score_row(
                    "의미 결과 비교 가능", len(semantic_applicable), len(completed)
                ),
                score_row(
                    "의미 결과 정확도", semantic_passed, len(semantic_applicable)
                ),
                score_row("최종 결과 평가 대상", len(final_evaluated), len(completed)),
                score_row(
                    "최종 결과 비교 가능", len(final_applicable), len(final_evaluated)
                ),
                score_row("최종 결과 정확도", final_passed, len(final_applicable)),
                score_row("HYBRID 분할", hybrid_split_passed, len(hybrid)),
            ]
        )
        lines.extend(
            [
                "",
                "> 채점 실행 완료는 정답률이 아니라 인프라 오류 없이 채점된 비율입니다.",
                "",
                "## Route별 엄격 PASS",
                "",
                "| Route | 통과/대상 | 비율 |",
                "|---|---:|---:|",
            ]
        )
        for route in route_order:
            route_records = [
                record for record in completed if record.get("route") == route
            ]
            if route_records:
                route_passed = sum(
                    record.get("queryPipelinePass") is True for record in route_records
                )
                lines.append(score_row(route, route_passed, len(route_records)))

    lines.extend(
        [
            "",
            "## 질의별 결과",
            "",
            "| RQ ID | Run | Route | Entity | Routing | Split | Execution | Result | 엄격 PASS | 실패 사유 |",
            "|---|---:|---|---|---|---|---|---|---|---|",
        ]
    )
    for record in records:
        checks = record.get("checks", {})
        if gold_validation_only:
            gold_pass = record.get("status") == "GOLD_VALIDATED" and all(
                subquery.get("status") == "PASS"
                for subquery in record.get("subqueries", [])
            )
            entity = routing = split = "-"
            execution = result = check_label(gold_pass)
            pipeline = "-"
        else:
            entity = check_label(checks.get("entity"))
            routing = check_label(checks.get("routing"))
            split = check_label(checks.get("split"))
            execution = check_label(checks.get("execution"))
            result = check_label(checks.get("result"))
            pipeline = check_label(record.get("queryPipelinePass"))
        lines.append(
            f"| {cell(record.get('caseId', '-'))} | "
            f"{cell(record.get('run', '-'))} | {cell(record.get('route', '-'))} | "
            f"{entity} | {routing} | "
            f"{split} | {execution} | {result} | {pipeline} | "
            f"{failure_reason(record)} |"
        )
    return "\n".join(lines) + "\n"


def write_artifacts(
    output_dir: Path,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation.json").write_text(
        json.dumps(
            {"summary": summary, "cases": records},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        _report_markdown(summary, records), encoding="utf-8"
    )
    for legacy_name in ("summary.json", "cases.jsonl", "junit.xml"):
        (output_dir / legacy_name).unlink(missing_ok=True)
