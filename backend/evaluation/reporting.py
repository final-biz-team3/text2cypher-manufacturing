"""평가 지표와 JSON/Markdown/JUnit artifact 출력."""

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

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
) -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "model": model,
        "commit": commit,
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
    def check_label(value: Any) -> str:
        if value is True:
            return "PASS"
        if value is False:
            return "FAIL"
        return "NOT_EVALUATED"

    metrics = summary.get("metrics", {})
    gold_validation_only = summary.get("goldValidationOnly") is True
    model_label = (
        "gold-only" if gold_validation_only else summary.get("model") or "unavailable"
    )
    lines = [
        "# Text-to-query evaluation",
        "",
        f"- Model: `{model_label}`",
        f"- Commit: `{summary.get('commit')}`",
        f"- Snapshot: `{summary.get('snapshot', {}).get('sha256', 'unavailable')}`",
        "- Mode: `report-only`",
    ]
    if summary.get("error"):
        lines.append(f"- Infrastructure error: `{summary['error']}`")
    if metrics:
        lines.extend(
            [
                f"- Evaluation coverage: {metrics['evaluationCoverage']:.2%}",
                f"- Query pipeline accuracy: {metrics['queryPipelineAccuracy']:.2%}",
                f"- Semantic result coverage: {metrics['semanticResultCoverage']:.2%}",
                f"- Semantic result accuracy: {metrics['semanticResultAccuracy']:.2%}",
                f"- Verified semantic pass rate: {metrics['verifiedSemanticPassRate']:.2%}",
                f"- Final result coverage: {metrics['finalResultCoverage']:.2%}",
                f"- Final result evaluation coverage: {metrics['finalResultEvaluationCoverage']:.2%}",
                f"- Final result accuracy: {metrics['finalResultAccuracy']:.2%}",
                f"- Verified final-result pass rate: {metrics['verifiedFinalResultPassRate']:.2%}",
                f"- Routing accuracy: {metrics['routingAccuracy']:.2%}",
                f"- Hybrid split accuracy: {metrics['hybridSplitAccuracy']:.2%}",
                f"- SQL partial coverage / accuracy: {metrics['sqlPartialCoverage']:.2%} / {metrics['sqlPartialAccuracy']:.2%}",
                f"- Graph partial coverage / accuracy: {metrics['graphPartialCoverage']:.2%} / {metrics['graphPartialAccuracy']:.2%}",
            ]
        )
    lines.extend(["", "## Cases", ""])
    if gold_validation_only:
        lines.extend(
            [
                "| Case | Question | Route | Status | Validated partials |",
                "|---|---|---|---|---|",
            ]
        )
        for record in records:
            question = str(record.get("question", "")).replace("|", "\\|")
            subqueries = record.get("subqueries", [])
            passed = sum(item.get("status") == "PASS" for item in subqueries)
            lines.append(
                f"| {record.get('caseId')} | {question} | {record.get('route')} | "
                f"{record.get('status')} | {passed} / {len(subqueries)} |"
            )
    else:
        lines.extend(
            [
                "| Case | Question | Route | Entity | Routing | Integration split | Execution | Output contract | Result | Pipeline | Reasons / warnings |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for record in records:
            checks = record.get("checks", {})
            reasons = ", ".join(record.get("failureReasons", [])) or "-"
            warnings = ", ".join(record.get("contractWarnings", []))
            if warnings:
                reasons = f"{reasons} / {warnings}"
            question = str(record.get("question", "")).replace("|", "\\|")
            lines.append(
                f"| {record.get('caseId')} | {question} | {record.get('route')} | "
                f"{check_label(checks.get('entity'))} | "
                f"{check_label(checks.get('routing'))} | "
                f"{check_label(checks.get('split'))} | "
                f"{check_label(checks.get('execution'))} | "
                f"{check_label(checks.get('resultContract'))} | "
                f"{check_label(checks.get('result'))} | "
                f"{check_label(record.get('queryPipelinePass'))} | "
                f"{reasons} |"
            )
    failed_records = [
        record for record in records if record.get("status") in {"FAIL", "ERROR"}
    ]
    if failed_records:
        lines.extend(["", "## Failed case details", ""])
    for record in failed_records:
        lines.extend(
            [
                f"### {record.get('caseId')} / run {record.get('run')}",
                "",
                f"- Received question: {record.get('question', '-')}",
                f"- Status: `{record.get('status')}`",
                "",
            ]
        )
        if record.get("error"):
            lines.extend([f"- Error: `{record['error']}`", ""])
        if record.get("planningError"):
            lines.extend(
                [
                    f"- Planning failure: `{record['planningError']}`",
                    "",
                    "```json",
                    str(record.get("planningResponse", "unavailable")),
                    "```",
                    "",
                ]
            )
        for subquery in record.get("subqueries", []):
            rules = subquery.get("businessRules", [])
            lines.extend(
                [
                    f"#### {subquery.get('id')} ({subquery.get('tool')})",
                    "",
                    f"- Expected responsibility: {subquery.get('expectedQuestion', '-')}",
                    f"- Required outputs: `{', '.join(subquery.get('requiredOutputs', []))}`",
                    f"- Gold: `{subquery.get('goldFile', '-')}`",
                    f"- Business rules: {' / '.join(rules) if rules else '-'}",
                    f"- Upstream inputs: `{json.dumps(subquery.get('upstreamInputs', {}), ensure_ascii=False, default=str)}`",
                ]
            )
            if subquery.get("failureCategory"):
                lines.append(
                    f"- Failure: `{subquery['failureCategory']}` — "
                    f"{subquery.get('error', '')}"
                )
            generated_query = subquery.get("generatedQuery")
            if generated_query:
                language = "sql" if subquery.get("tool") == "sql" else "cypher"
                lines.extend(["", f"```{language}", generated_query, "```"])
            if "candidateSample" in subquery or "goldSample" in subquery:
                sample = {
                    "candidate": subquery.get("candidateSample", []),
                    "gold": subquery.get("goldSample", []),
                }
                lines.extend(
                    [
                        "",
                        "```json",
                        json.dumps(sample, ensure_ascii=False, indent=2, default=str),
                        "```",
                    ]
                )
            lines.append("")
    return "\n".join(lines) + "\n"


def write_artifacts(
    output_dir: Path,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "cases.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    (output_dir / "report.md").write_text(
        _report_markdown(summary, records), encoding="utf-8"
    )

    testsuite = ElementTree.Element(
        "testsuite",
        name="text-to-query-evaluation",
        tests=str(len(records)),
        failures=str(sum(record.get("status") == "FAIL" for record in records)),
        errors=str(sum(record.get("status") == "ERROR" for record in records)),
    )
    for record in records:
        testcase = ElementTree.SubElement(
            testsuite,
            "testcase",
            classname=f"{record.get('suite')}.{record.get('contractId')}",
            name=f"{record.get('caseId')}[run={record.get('run')}]",
        )
        if record.get("status") == "FAIL":
            failure = ElementTree.SubElement(
                testcase, "failure", message="query pipeline failed"
            )
            failure.text = json.dumps(record, ensure_ascii=False, default=str)
        elif record.get("status") == "ERROR":
            error = ElementTree.SubElement(
                testcase, "error", message=record.get("error", "infrastructure error")
            )
            error.text = record.get("error", "")
    tree = ElementTree.ElementTree(testsuite)
    ElementTree.indent(tree, space="  ")
    tree.write(output_dir / "junit.xml", encoding="utf-8", xml_declaration=True)
