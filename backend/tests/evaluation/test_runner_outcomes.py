from pathlib import Path
from types import MethodType
from typing import Any

import psycopg
import pytest

import evaluation.runner as runner_module
from agents.cypher.schema.models import GraphQueryPolicy
from evaluation.errors import InfrastructureError, QuerySafetyError
from evaluation.models import load_manifest
from evaluation.runner import EvaluationRunner
from orchestrator.nodes.route_query import RoutePlanError
from orchestrator.state import OrchestratorState

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_sql_generation_does_not_receive_gold_contract_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    expected = manifest.contracts["RQ08"].subqueries[0]
    captured: dict[str, Any] = {}

    async def generate_sql(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "SELECT 1"

    monkeypatch.setattr(runner_module, "generate_sql", generate_sql)
    runner = object.__new__(EvaluationRunner)
    runner.openai_client = object()
    runner.sql_schema_text = "schema"

    runner._generate_query(
        expected,
        {"question": "재고와 부족 수량을 조회한다."},
        {"productId": 492},
        {},
    )

    assert "business_rules" not in captured
    assert "required_outputs" not in captured


def test_cypher_generation_does_not_receive_gold_contract_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    expected = manifest.contracts["RQ17"].subqueries[0]
    captured: dict[str, Any] = {}

    async def generate_cypher(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "MATCH (n) RETURN n"

    monkeypatch.setattr(runner_module, "generate_cypher", generate_cypher)
    runner = object.__new__(EvaluationRunner)
    runner.openai_client = object()
    runner.graph_schema_text = "schema"
    runner.graph_query_policy = GraphQueryPolicy(
        bomAsOfDate="2014-08-08", bomMaxDepth=4
    )

    runner._generate_query(
        expected,
        {"question": "두 완제품의 공통 부품을 조회한다."},
        [{"productId": 765}, {"productId": 775}],
        {},
    )

    assert "business_rules" not in captured
    assert "required_outputs" not in captured


def test_runner_accepts_integral_float_id_and_unused_single_query_output_plan() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ02")
    contract = manifest.contracts[case.contract_id]
    plan = contract.subqueries[0].planning_shape()
    plan["requiredOutputs"] = ["productId", "productName", "quantity"]

    runner = object.__new__(EvaluationRunner)
    runner.manifest = manifest
    actual_entity = {
        **contract.expected_entities,
        "productId": float(contract.expected_entities["productId"]),
    }
    runner.resolve_entity = lambda state: {"entity": actual_entity}
    runner.route_query = lambda state: {"tool_plan": ["sql"], "subqueries": [plan]}

    def evaluate_subqueries(
        self: EvaluationRunner,
        contract: Any,
        case: Any,
        actual_subqueries: Any,
        entity: Any,
        id_mapping: dict[str, str],
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "sql_inventory_locations",
                "tool": "sql",
                "status": "PASS",
                "checks": {
                    "generation": True,
                    "readOnly": True,
                    "execution": True,
                    "result": True,
                },
            }
        ]

    runner._evaluate_subqueries = MethodType(  # type: ignore[method-assign]
        evaluate_subqueries, runner
    )

    record = runner._evaluate_case(case, 1)

    assert record["checks"]["entity"] is True
    assert record["checks"]["result"] is True
    assert record["semanticResultPass"] is True
    assert record["finalResultPass"] is True
    assert record["queryPipelinePass"] is True
    assert record["status"] == "PASS"
    assert record["failureReasons"] == []


def test_entity_mismatch_keeps_result_evaluation_but_fails_pipeline() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ16")
    contract = manifest.contracts[case.contract_id]
    plan = contract.subqueries[0].planning_shape()

    runner = object.__new__(EvaluationRunner)
    runner.manifest = manifest
    runner.resolve_entity = lambda state: {"entity": contract.expected_entities[0]}
    runner.route_query = lambda state: {
        "tool_plan": ["graph"],
        "subqueries": [plan],
    }

    def evaluate_subqueries(
        self: EvaluationRunner,
        contract: Any,
        case: Any,
        actual_subqueries: Any,
        entity: Any,
        id_mapping: dict[str, str],
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "graph_all_bom_paths",
                "tool": "graph",
                "status": "PASS",
                "checks": {
                    "generation": True,
                    "readOnly": True,
                    "execution": True,
                    "resultContract": True,
                    "result": True,
                },
            }
        ]

    runner._evaluate_subqueries = MethodType(  # type: ignore[method-assign]
        evaluate_subqueries, runner
    )

    record = runner._evaluate_case(case, 1)

    assert record["checks"]["entity"] is False
    assert record["semanticResultPass"] is True
    assert record["finalResultPass"] is True
    assert record["queryPipelinePass"] is False
    assert record["status"] == "FAIL"
    assert record["failureReasons"] == ["ENTITY_MISMATCH"]


def test_query_safety_failure_is_classified_without_execution() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ03")
    contract = manifest.contracts[case.contract_id]
    actual = [contract.subqueries[0].planning_shape()]
    runner = object.__new__(EvaluationRunner)

    def generate(*args: Any, **kwargs: Any) -> str:
        raise QuerySafetyError("write query")

    runner._generate_query = generate  # type: ignore[method-assign]
    runner._execute = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("execution must not be called")
    )

    records = runner._evaluate_subqueries(
        contract,
        case,
        actual,
        contract.expected_entities,
        {contract.subqueries[0].id: contract.subqueries[0].id},
    )

    assert records[0]["failureCategory"] == "READ_ONLY_VIOLATION"
    assert records[0]["checks"]["execution"] is False


def test_candidate_sql_timeout_is_a_query_failure_not_infrastructure_error() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ03")
    contract = manifest.contracts[case.contract_id]
    actual = [contract.subqueries[0].planning_shape()]
    runner = object.__new__(EvaluationRunner)
    runner._generate_query = lambda *args, **kwargs: "SELECT 1"  # type: ignore[method-assign]
    runner._execute = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        psycopg.errors.QueryCanceled("statement timeout")
    )

    records = runner._evaluate_subqueries(
        contract,
        case,
        actual,
        contract.expected_entities,
        {contract.subqueries[0].id: contract.subqueries[0].id},
    )

    assert records[0]["status"] == "FAIL"
    assert records[0]["failureCategory"] == "QUERY_TIMEOUT"
    assert records[0]["checks"]["execution"] is False


def test_unmapped_multi_output_field_is_contract_failure_not_semantic_failure() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ01")
    contract = manifest.contracts[case.contract_id]
    actual = [contract.subqueries[0].planning_shape()]
    runner = object.__new__(EvaluationRunner)
    runner._generate_query = lambda *args, **kwargs: "SELECT 1"  # type: ignore[method-assign]
    runner._execute = lambda *args, **kwargs: [{"wrong": 1}]  # type: ignore[method-assign]
    runner._gold_result = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("Gold comparison must not run without a normalized candidate")
    )

    records = runner._evaluate_subqueries(
        contract,
        case,
        actual,
        contract.expected_entities,
        {contract.subqueries[0].id: contract.subqueries[0].id},
    )

    assert records[0]["failureCategory"] == "RESULT_CONTRACT_MISMATCH"
    assert records[0]["checks"]["resultContract"] is False
    assert records[0]["checks"]["result"] is None


def test_run_records_infrastructure_errors_separately() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ03")
    runner = object.__new__(EvaluationRunner)
    runner.manifest = manifest
    runner.validate_snapshot = lambda: {"sha256": "snapshot"}  # type: ignore[method-assign]

    def evaluate_case(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise InfrastructureError("API unavailable")

    runner._evaluate_case = evaluate_case  # type: ignore[method-assign]

    result = runner.run([case], 1)

    assert result.infrastructure_error is True
    assert result.records[0]["status"] == "ERROR"


def test_invalid_route_plan_keeps_the_model_response_for_review() -> None:
    manifest = load_manifest(PROJECT_ROOT / "queries" / "evaluation" / "manifest.json")
    case = next(case for case in manifest.cases if case.case_id == "RQ03")
    runner = object.__new__(EvaluationRunner)
    runner.manifest = manifest
    runner.resolve_entity = lambda state: {"entity": None}

    def invalid_route(state: OrchestratorState) -> dict[str, Any]:
        raise RoutePlanError("invalid join key", '{"tool_plan":["sql"]}')

    runner.route_query = invalid_route

    record = runner._evaluate_case(case, 1)

    assert record["planningError"] == "invalid join key"
    assert record["planningResponse"] == '{"tool_plan":["sql"]}'
    assert record["toolPlan"] == ["sql"]
    assert record["checks"]["routing"] is True
    assert record["checks"]["split"] is False
    assert "ROUTE_MISMATCH" not in record["failureReasons"]
    assert "SUBQUERY_INTEGRATION_CONTRACT_MISMATCH" in record["failureReasons"]
    assert record["status"] == "FAIL"
