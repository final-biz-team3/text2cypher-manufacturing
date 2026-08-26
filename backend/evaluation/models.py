"""평가 manifest를 엄격한 내부 모델로 로드한다."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluation.errors import ConfigurationError
from orchestrator.planning import Subquery, validate_subqueries

SUPPORTED_ROUTES = {"SQL", "GRAPH", "HYBRID"}
SUPPORTED_STATUSES = {
    "FULLY_EVALUATED",
    "QUERY_EVALUATED_FINAL_JOIN_PENDING",
}


@dataclass(frozen=True)
class ExpectedSubquery:
    id: str
    tool: str
    question: str
    depends_on: tuple[str, ...]
    required_outputs: tuple[str, ...]
    join_keys: tuple[str, ...]
    input_bindings: dict[str, str]
    gold_file: Path
    ordering: tuple[str, ...]
    aliases: dict[str, tuple[str, ...]]
    max_rows: int
    business_rules: tuple[str, ...] = ()

    def planning_shape(self) -> Subquery:
        value: Subquery = {
            "id": self.id,
            "tool": self.tool,
            "question": self.question,
            "dependsOn": list(self.depends_on),
            "requiredOutputs": list(self.required_outputs),
            "joinKeys": list(self.join_keys),
        }
        if self.input_bindings:
            value["inputBindings"] = dict(self.input_bindings)
        return value


@dataclass(frozen=True)
class EvaluationContract:
    id: str
    route: str
    expected_entities: Any
    support_status: str
    tool_plan: tuple[str, ...]
    subqueries: tuple[ExpectedSubquery, ...]


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    contract_id: str
    suite: str
    question: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotCheck:
    source: str
    name: str
    query: str
    expected: Any
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationManifest:
    path: Path
    contracts: dict[str, EvaluationContract]
    cases: tuple[EvaluationCase, ...]
    snapshot_checks: tuple[SnapshotCheck, ...]
    snapshot_sync_run_id: str | None


def _require_string(raw: dict[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigurationError(
            f"{context}.{key}는 비어 있지 않은 문자열이어야 합니다."
        )
    return value


def _string_tuple(raw: Any, context: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise ConfigurationError(f"{context}는 문자열 배열이어야 합니다.")
    return tuple(raw)


def _load_subquery(
    raw: dict[str, Any],
    manifest_dir: Path,
    contract_id: str,
    global_aliases: dict[str, tuple[str, ...]],
) -> ExpectedSubquery:
    context = f"contracts.{contract_id}.subqueries"
    subquery_id = _require_string(raw, "id", context)
    tool = _require_string(raw, "tool", f"{context}.{subquery_id}")
    question = _require_string(raw, "question", f"{context}.{subquery_id}")
    gold_relative = _require_string(raw, "gold", f"{context}.{subquery_id}")
    gold_file = (manifest_dir / gold_relative).resolve()
    if not gold_file.is_file():
        raise ConfigurationError(f"Gold 파일을 찾을 수 없습니다: {gold_file}")

    outputs = _string_tuple(
        raw.get("requiredOutputs"), f"{context}.{subquery_id}.requiredOutputs"
    )
    aliases_raw = raw.get("aliases", {})
    if not isinstance(aliases_raw, dict):
        raise ConfigurationError(f"{context}.{subquery_id}.aliases는 객체여야 합니다.")
    aliases: dict[str, tuple[str, ...]] = {}
    for output in outputs:
        values = aliases_raw.get(output, [])
        local_aliases = _string_tuple(
            values, f"{context}.{subquery_id}.aliases.{output}"
        )
        aliases[output] = tuple(
            dict.fromkeys((*global_aliases.get(output, ()), *local_aliases))
        )

    bindings = raw.get("inputBindings", {})
    if not isinstance(bindings, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in bindings.items()
    ):
        raise ConfigurationError(
            f"{context}.{subquery_id}.inputBindings는 문자열 매핑이어야 합니다."
        )
    max_rows = raw.get("maxRows", 100)
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
        raise ConfigurationError(f"{context}.{subquery_id}.maxRows가 잘못됐습니다.")

    return ExpectedSubquery(
        id=subquery_id,
        tool=tool,
        question=question,
        depends_on=_string_tuple(
            raw.get("dependsOn", []), f"{context}.{subquery_id}.dependsOn"
        ),
        required_outputs=outputs,
        join_keys=_string_tuple(
            raw.get("joinKeys", []), f"{context}.{subquery_id}.joinKeys"
        ),
        input_bindings=dict(bindings),
        gold_file=gold_file,
        ordering=_string_tuple(
            raw.get("ordering", []), f"{context}.{subquery_id}.ordering"
        ),
        aliases=aliases,
        max_rows=max_rows,
        business_rules=_string_tuple(
            raw.get("businessRules", []),
            f"{context}.{subquery_id}.businessRules",
        ),
    )


def _load_contract(
    raw: dict[str, Any],
    manifest_dir: Path,
    global_aliases: dict[str, tuple[str, ...]],
) -> EvaluationContract:
    contract_id = _require_string(raw, "id", "contracts")
    route = _require_string(raw, "route", f"contracts.{contract_id}")
    if route not in SUPPORTED_ROUTES:
        raise ConfigurationError(f"{contract_id}의 route가 잘못됐습니다: {route}")
    support_status = _require_string(raw, "supportStatus", f"contracts.{contract_id}")
    if support_status not in SUPPORTED_STATUSES:
        raise ConfigurationError(
            f"{contract_id}의 supportStatus가 잘못됐습니다: {support_status}"
        )
    raw_subqueries = raw.get("subqueries")
    if not isinstance(raw_subqueries, list):
        raise ConfigurationError(f"{contract_id}.subqueries는 배열이어야 합니다.")
    subqueries = tuple(
        _load_subquery(item, manifest_dir, contract_id, global_aliases)
        for item in raw_subqueries
    )
    try:
        ordered = validate_subqueries(
            [subquery.planning_shape() for subquery in subqueries]
        )
    except ValueError as exc:
        raise ConfigurationError(f"{contract_id} subquery 계약 오류: {exc}") from exc
    if [subquery.id for subquery in subqueries] != [item["id"] for item in ordered]:
        raise ConfigurationError(
            f"{contract_id}.subqueries는 의존 실행 순서로 선언해야 합니다."
        )

    tool_plan = _string_tuple(raw.get("toolPlan"), f"contracts.{contract_id}.toolPlan")
    if set(tool_plan) != {subquery.tool for subquery in subqueries}:
        raise ConfigurationError(
            f"{contract_id}의 toolPlan과 subquery 도구가 일치하지 않습니다."
        )
    if route != "HYBRID" and len(subqueries) != 1:
        raise ConfigurationError(
            f"단일 {route} 계약 {contract_id}는 subquery가 1개여야 합니다."
        )
    if route == "HYBRID" and {subquery.tool for subquery in subqueries} != {
        "sql",
        "graph",
    }:
        raise ConfigurationError(
            f"HYBRID 계약 {contract_id}에는 SQL과 GRAPH가 필요합니다."
        )

    return EvaluationContract(
        id=contract_id,
        route=route,
        expected_entities=raw.get("expectedEntities"),
        support_status=support_status,
        tool_plan=tool_plan,
        subqueries=subqueries,
    )


def _load_cases(raw_cases: Any, contract_ids: set[str]) -> tuple[EvaluationCase, ...]:
    if not isinstance(raw_cases, list):
        raise ConfigurationError("cases는 배열이어야 합니다.")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ConfigurationError("case 항목은 객체여야 합니다.")
        case_id = _require_string(raw, "caseId", "cases")
        if case_id in seen:
            raise ConfigurationError(f"중복 caseId: {case_id}")
        seen.add(case_id)
        contract_id = _require_string(raw, "contractId", f"cases.{case_id}")
        if contract_id not in contract_ids:
            raise ConfigurationError(
                f"{case_id}가 없는 contract {contract_id}를 참조합니다."
            )
        suite = _require_string(raw, "suite", f"cases.{case_id}")
        question = _require_string(raw, "question", f"cases.{case_id}")
        parameters = raw.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ConfigurationError(f"{case_id}.parameters는 객체여야 합니다.")
        cases.append(EvaluationCase(case_id, contract_id, suite, question, parameters))
    return tuple(cases)


def load_manifest(path: Path, case_file: Path | None = None) -> EvaluationManifest:
    """manifest와 선택적 외부 case 파일을 로드하고 전체 참조를 검증한다."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"manifest를 읽을 수 없습니다: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("manifest 최상위 값은 객체여야 합니다.")

    manifest_dir = path.parent
    raw_global_aliases = raw.get("fieldAliases", {})
    if not isinstance(raw_global_aliases, dict):
        raise ConfigurationError("fieldAliases는 객체여야 합니다.")
    global_aliases = {
        str(field): _string_tuple(values, f"fieldAliases.{field}")
        for field, values in raw_global_aliases.items()
    }
    raw_contracts = raw.get("contracts")
    if not isinstance(raw_contracts, list):
        raise ConfigurationError("contracts는 배열이어야 합니다.")
    contracts = {
        contract.id: contract
        for contract in (
            _load_contract(item, manifest_dir, global_aliases) for item in raw_contracts
        )
    }
    if len(contracts) != len(raw_contracts):
        raise ConfigurationError("contract id는 중복될 수 없습니다.")

    cases_raw = raw.get("cases")
    if case_file is not None:
        try:
            external = json.loads(case_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"case 파일을 읽을 수 없습니다: {case_file}: {exc}"
            ) from exc
        cases_raw = external.get("cases") if isinstance(external, dict) else external
    cases = _load_cases(cases_raw, set(contracts))

    snapshot = raw.get("snapshot", {})
    if not isinstance(snapshot, dict):
        raise ConfigurationError("snapshot은 객체여야 합니다.")
    raw_checks = snapshot.get("checks", [])
    if not isinstance(raw_checks, list):
        raise ConfigurationError("snapshot.checks는 배열이어야 합니다.")
    checks: list[SnapshotCheck] = []
    for index, check in enumerate(raw_checks):
        if not isinstance(check, dict):
            raise ConfigurationError(f"snapshot.checks[{index}]는 객체여야 합니다.")
        source = _require_string(check, "source", f"snapshot.checks[{index}]")
        if source not in {"sql", "graph"}:
            raise ConfigurationError(f"snapshot check source가 잘못됐습니다: {source}")
        parameters = check.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ConfigurationError("snapshot check parameters는 객체여야 합니다.")
        checks.append(
            SnapshotCheck(
                source=source,
                name=_require_string(check, "name", f"snapshot.checks[{index}]"),
                query=_require_string(check, "query", f"snapshot.checks[{index}]"),
                expected=check.get("expected"),
                parameters=parameters,
            )
        )

    sync_run_id = snapshot.get("syncRunId")
    if sync_run_id is not None and not isinstance(sync_run_id, str):
        raise ConfigurationError("snapshot.syncRunId는 문자열 또는 null이어야 합니다.")
    return EvaluationManifest(
        path=path.resolve(),
        contracts=contracts,
        cases=cases,
        snapshot_checks=tuple(checks),
        snapshot_sync_run_id=sync_run_id,
    )
