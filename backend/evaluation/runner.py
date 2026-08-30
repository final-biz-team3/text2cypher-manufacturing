"""프로덕션 오케스트레이터 구성요소를 사용하는 평가 실행기."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import psycopg
from neo4j.exceptions import (
    AuthError,
    DatabaseUnavailable,
    ServiceUnavailable,
    SessionExpired,
)
from openai import OpenAIError

from agents.cypher.generator import generate_cypher
from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.serializer import serialize_graph_schema
from agents.sql.generator import generate_sql
from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.serializer import serialize_sql_schema
from core.postgres import get_pool
from evaluation.contracts import (
    collect_input_bindings,
    compare_execution_contract,
    entity_matches,
)
from evaluation.errors import InfrastructureError, QuerySafetyError, ResultContractError
from evaluation.models import (
    EvaluationCase,
    EvaluationContract,
    EvaluationManifest,
    ExpectedSubquery,
    FinalResultContract,
)
from evaluation.normalization import normalize_rows, normalized_sha256
from evaluation.observability import CountingOpenAIClient
from evaluation.safety import validate_read_only_cypher, validate_read_only_sql
from orchestrator.composition import compose_results
from orchestrator.errors import AppError
from orchestrator.graph import build_orchestrator_graph
from orchestrator.nodes.resolve_entity import make_resolve_entity_node
from orchestrator.nodes.route_query import RoutePlanError, make_route_query_node
from orchestrator.planning import BomShortageTransform

_CONNECTION_ERRORS = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
    AuthError,
    DatabaseUnavailable,
    ServiceUnavailable,
    SessionExpired,
)


@dataclass(frozen=True)
class EvaluationRun:
    records: list[dict[str, Any]]
    snapshot: dict[str, Any]
    infrastructure_error: bool


class EvaluationRunner:
    """라우팅과 query generator를 우회하지 않고 source별 Gold와 비교한다."""

    def __init__(
        self,
        manifest: EvaluationManifest,
        database: Any,
        openai_client: Any | None,
        *,
        project_root: Path,
        loop: asyncio.AbstractEventLoop | None = None,
        execution_mode: str = "orchestrator",
    ) -> None:
        if execution_mode not in {"orchestrator", "source"}:
            raise ValueError(
                f"지원하지 않는 evaluation execution mode: {execution_mode}"
            )
        self.manifest = manifest
        self.database = database
        self.execution_mode = execution_mode
        self.openai_client: Any = (
            CountingOpenAIClient(openai_client) if openai_client is not None else None
        )
        sql_schema = load_sql_schema(project_root / "schema" / "sql_schema.yaml")
        graph_schema = load_graph_schema(project_root / "schema" / "graph_schema.yaml")
        if graph_schema.query_policy is None:
            raise InfrastructureError("graph schema에 query policy가 없습니다.")
        self.sql_schema_text = serialize_sql_schema(sql_schema)
        self.graph_schema_text = serialize_graph_schema(graph_schema)
        self.graph_query_policy = graph_schema.query_policy
        # resolve_entity/route_query 노드는 async다 - 이 클래스(그리고
        # 아래를 호출하는 _evaluate_case)는 전부 동기라, 호출부 입장에서는
        # 평범한 함수처럼 보이게 감싼다. 감쌀 때 매번 asyncio.run()을 쓰면 안
        # 된다 - asyncio.run()은 호출마다 새 이벤트 루프를 만들고 끝나면 닫는데,
        # get_pool()이 반환하는 AsyncConnectionPool은 내부 락/큐가 "풀을 연
        # 시점의 루프"에 묶여 있다. cli.py가 이미 닫힌 루프에서 연 풀을, 여기서
        # 매번 새로 만드는 다른 루프가 재사용하려 들면 다른 루프에 붙은
        # 객체를 건드리는 에러가 난다. 그래서 cli.py가 풀을 열 때 쓴 것과
        # 동일한 loop를 주입받아 run_until_complete로 재사용한다. resolve_entity의
        # DB 조회는 database.postgres(평가 전용 sync 커넥션)가 아니라
        # core.postgres.get_pool()을 쓴다 - resolve_entity.py가 이제
        # `async with pool.connection()` 형태의 풀 객체를 요구하기 때문에,
        # 앱 전체가 쓰는 것과 같은(읽기 전용) 풀을 그대로 재사용한다.
        self._loop = loop
        self.resolve_entity: Callable[[Any], Any] | None
        self.route_query: Callable[[Any], Any] | None
        self.orchestrator_graph: Any | None = None
        if self.openai_client is not None:
            assert loop is not None, "openai_client가 있으면 loop도 필요합니다."
            resolve_entity_node = make_resolve_entity_node(
                self.openai_client, get_pool(), graph_schema
            )
            route_query_node = make_route_query_node(self.openai_client)
            self.resolve_entity = lambda state: loop.run_until_complete(
                resolve_entity_node(state)
            )
            self.route_query = lambda state: loop.run_until_complete(
                route_query_node(state)
            )
            if execution_mode == "orchestrator":
                self.orchestrator_graph = build_orchestrator_graph(
                    self.openai_client, get_pool()
                )
        else:
            self.resolve_entity = None
            self.route_query = None

    def _execute(
        self,
        tool: str,
        query: str,
        parameters: dict[str, Any],
        max_rows: int,
    ) -> list[dict[str, Any]]:
        if tool == "sql":
            return self.database.execute_sql(query, parameters, max_rows=max_rows)
        return self.database.execute_cypher(query, parameters, max_rows=max_rows)

    def validate_snapshot(self) -> dict[str, Any]:
        """승인 건수, fixture business key와 단일 syncRunId를 확인한다."""
        actual_checks: dict[str, Any] = {}
        try:
            for check in self.manifest.snapshot_checks:
                rows = self._execute(
                    check.source,
                    check.query,
                    check.parameters,
                    1,
                )
                if len(rows) != 1 or "value" not in rows[0]:
                    raise InfrastructureError(
                        f"snapshot check {check.name}은 value 1행을 반환해야 합니다."
                    )
                actual = rows[0]["value"]
                actual_checks[check.name] = actual
                if actual != check.expected:
                    raise InfrastructureError(
                        f"DB snapshot 불일치: {check.name}: "
                        f"expected={check.expected!r}, actual={actual!r}"
                    )
            sync_run_ids = self.database.sync_run_ids()
        except InfrastructureError:
            raise
        except Exception as exc:
            raise InfrastructureError(f"DB snapshot 검증 실패: {exc}") from exc

        if len(sync_run_ids) != 1 or sync_run_ids[0] is None:
            raise InfrastructureError(
                f"Neo4j syncRunId가 하나로 고정되지 않았습니다: {sync_run_ids!r}"
            )
        sync_run_id = sync_run_ids[0]
        expected_sync_run_id = self.manifest.snapshot_sync_run_id
        if expected_sync_run_id is not None and sync_run_id != expected_sync_run_id:
            raise InfrastructureError(
                "Neo4j syncRunId가 승인 snapshot과 일치하지 않습니다."
            )
        payload = {"checks": actual_checks, "syncRunId": sync_run_id}
        return {
            **payload,
            "sha256": normalized_sha256([payload]),
        }

    @staticmethod
    def _gold_query(expected: ExpectedSubquery) -> str:
        try:
            return expected.gold_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise InfrastructureError(
                f"Gold 쿼리를 읽을 수 없습니다: {expected.gold_file}: {exc}"
            ) from exc

    def _gold_source_result(
        self,
        expected: ExpectedSubquery,
        parameters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        query = self._gold_query(expected)
        try:
            rows = self._execute(
                expected.tool,
                query,
                parameters,
                expected.max_rows,
            )
            normalized = normalize_rows(
                rows,
                required_outputs=expected.required_outputs,
                aliases=expected.aliases,
                ordering=expected.ordering,
            )
        except Exception as exc:
            raise InfrastructureError(
                f"Gold 실행 실패 ({expected.id}, {expected.gold_file.name}): {exc}"
            ) from exc
        return rows, normalized, normalized_sha256(normalized)

    def _gold_result(
        self,
        expected: ExpectedSubquery,
        parameters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        _, normalized, gold_hash = self._gold_source_result(expected, parameters)
        return normalized, gold_hash

    @staticmethod
    def _expected_transform(
        contract: EvaluationContract, case: EvaluationCase
    ) -> BomShortageTransform | None:
        final = contract.final_result
        if final is None or final.transform is None:
            return None
        production_qty = case.parameters.get("productionQty")
        if isinstance(production_qty, bool) or not isinstance(
            production_qty, int | float
        ):
            raise InfrastructureError(
                f"{case.case_id} final transform productionQty가 없습니다."
            )
        return {"type": "bom_shortage_v1", "productionQty": production_qty}

    @staticmethod
    def _final_gold_rows(final: FinalResultContract) -> list[dict[str, Any]]:
        try:
            raw = json.loads(final.gold_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InfrastructureError(
                f"Final Gold를 읽을 수 없습니다: {final.gold_file}: {exc}"
            ) from exc
        if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
            raise InfrastructureError(
                f"Final Gold는 객체 행 배열이어야 합니다: {final.gold_file}"
            )
        try:
            normalized = normalize_rows(
                raw,
                required_outputs=final.required_outputs,
                aliases={},
                ordering=final.ordering,
            )
        except ResultContractError as exc:
            raise InfrastructureError(
                f"Final Gold 결과 계약 오류 ({final.gold_file.name}): {exc}"
            ) from exc
        actual_hash = normalized_sha256(normalized)
        if len(normalized) != final.row_count or actual_hash != final.sha256:
            raise InfrastructureError(
                f"Final Gold hash/rowCount 불일치: {final.gold_file.name}"
            )
        return normalized

    def _validate_composed_gold(
        self,
        contract: EvaluationContract,
        case: EvaluationCase,
        source_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        final = contract.final_result
        if final is None:
            return None
        composed = compose_results(
            [item.planning_shape() for item in contract.subqueries],
            source_results,
            row_limit=max(final.row_count + 1, 1),
            result_transform=self._expected_transform(contract, case),
        )
        if composed.get("error") is not None:
            raise InfrastructureError(
                f"Final Gold production composer 실패 ({case.case_id}): "
                f"{composed['error']}"
            )
        if (
            composed.get("mode") != final.mode
            or composed.get("transform") != final.transform
            or composed.get("truncated") is True
        ):
            raise InfrastructureError(
                f"Final Gold composition 계약 불일치: {case.case_id}"
            )
        expected_rows = self._final_gold_rows(final)
        try:
            actual_rows = normalize_rows(
                composed["rows"],
                required_outputs=final.required_outputs,
                aliases={},
                ordering=final.ordering,
            )
        except ResultContractError as exc:
            raise InfrastructureError(
                f"Final Gold composer 결과 계약 오류 ({case.case_id}): {exc}"
            ) from exc
        actual_hash = normalized_sha256(actual_rows)
        if actual_hash != final.sha256 or actual_rows != expected_rows:
            raise InfrastructureError(
                f"Final Gold composer 결과 불일치: {case.case_id}"
            )
        return {
            "mode": final.mode,
            "transform": final.transform,
            "goldFile": final.gold_file.name,
            "goldHash": final.sha256,
            "rowCount": len(actual_rows),
            "status": "PASS",
        }

    @staticmethod
    def _score_final_result(
        final: FinalResultContract,
        composed: Any,
    ) -> tuple[bool, dict[str, Any]]:
        comparison: dict[str, Any] = {
            "modePass": isinstance(composed, dict)
            and composed.get("mode") == final.mode,
            "transformPass": isinstance(composed, dict)
            and composed.get("transform") == final.transform,
            "rowCountPass": isinstance(composed, dict)
            and composed.get("total_count") == final.row_count
            and composed.get("truncated") is False,
            "resultContractPass": False,
            "resultPass": False,
        }
        if not isinstance(composed, dict) or composed.get("error") is not None:
            return False, comparison
        try:
            normalized = normalize_rows(
                composed.get("rows", []),
                required_outputs=final.required_outputs,
                aliases={},
                ordering=final.ordering,
            )
        except ResultContractError as exc:
            comparison["error"] = str(exc)
            return False, comparison
        comparison["resultContractPass"] = True
        candidate_hash = normalized_sha256(normalized)
        comparison["candidateHash"] = candidate_hash
        comparison["goldHash"] = final.sha256
        comparison["candidateRowCount"] = len(normalized)
        comparison["goldRowCount"] = final.row_count
        comparison["candidateSample"] = normalized[:5]
        comparison["resultPass"] = candidate_hash == final.sha256
        passed = all(
            comparison[key] is True
            for key in (
                "modePass",
                "transformPass",
                "rowCountPass",
                "resultContractPass",
                "resultPass",
            )
        )
        return passed, comparison

    def validate_gold(self, cases: list[EvaluationCase]) -> EvaluationRun:
        """모델 호출 없이 모든 Gold와 dependency binding을 실행한다."""
        snapshot = self.validate_snapshot()
        records: list[dict[str, Any]] = []
        for case in cases:
            contract = self.manifest.contracts[case.contract_id]
            upstream: dict[str, list[dict[str, Any]]] = {}
            source_results: dict[str, dict[str, Any]] = {}
            subquery_records: list[dict[str, Any]] = []
            for expected in contract.subqueries:
                inputs = collect_input_bindings(expected.input_bindings, upstream)
                parameters = {**case.parameters, **inputs}
                raw_rows, normalized, gold_hash = self._gold_source_result(
                    expected, parameters
                )
                upstream[expected.id] = normalized
                source_results[expected.tool] = {
                    "result": raw_rows,
                    "error": None,
                    "attempts": [],
                    "empty_reason": "NO_DATA" if not normalized else None,
                }
                subquery_records.append(
                    {
                        "id": expected.id,
                        "tool": expected.tool,
                        "expectedQuestion": expected.question,
                        "businessRules": list(expected.business_rules),
                        "requiredOutputs": list(expected.required_outputs),
                        "goldFile": expected.gold_file.name,
                        "status": "PASS",
                        "upstreamInputs": inputs,
                        "goldHash": gold_hash,
                        "rowCount": len(normalized),
                    }
                )
            final_record = self._validate_composed_gold(contract, case, source_results)
            records.append(
                {
                    "caseId": case.case_id,
                    "contractId": case.contract_id,
                    "suite": case.suite,
                    "run": 1,
                    "question": case.question,
                    "route": contract.route,
                    "supportStatus": contract.support_status,
                    "status": "GOLD_VALIDATED",
                    "subqueries": subquery_records,
                    "finalResult": final_record,
                }
            )
        return EvaluationRun(records, snapshot, False)

    def _run_async(self, coro: Any) -> Any:
        """__init__에서 주입받은 loop가 있으면 그걸 재사용하고(같은 loop에
        묶인 커넥션 풀과의 불일치를 막기 위해), 없으면(예: 테스트에서
        object.__new__로 __init__을 건너뛴 경우) asyncio.run()으로 대체한다
        - 그 경우엔 공유 풀이 얽혀 있지 않아 매번 새 loop를 써도 안전하다."""
        loop = getattr(self, "_loop", None)
        if loop is not None:
            return loop.run_until_complete(coro)
        return asyncio.run(coro)

    def _generate_query(
        self,
        expected: ExpectedSubquery,
        actual: dict[str, Any],
        entity: Any,
        inputs: dict[str, list[Any]],
    ) -> str:
        # generate_sql/generate_cypher는 async다 - 이 메서드는 동기 호출부
        # (_evaluate_subqueries)에서 그대로 쓸 수 있어야 해서 _run_async로
        # 감싼다(왜 매번 asyncio.run()을 쓰면 안 되는지는 __init__의
        # resolve_entity/route_query 주석 참고 - 동일하게 적용됨).
        context = entity
        if inputs:
            context = {"resolvedEntities": entity, "upstreamBindings": inputs}
        # expected의 업무 규칙·출력 계약은 채점 전용이다. 후보 생성에 넣으면
        # production에는 없는 정답 힌트를 제공하게 된다.
        if expected.tool == "sql":
            query = self._run_async(
                generate_sql(
                    self.openai_client,
                    query=actual["question"],
                    entity=context,
                    schema_text=self.sql_schema_text,
                )
            )
            validate_read_only_sql(query)
            return query
        query = self._run_async(
            generate_cypher(
                self.openai_client,
                query=actual["question"],
                entity=context,
                schema_text=self.graph_schema_text,
                query_policy=self.graph_query_policy,
            )
        )
        validate_read_only_cypher(query)
        return query

    def _evaluate_subqueries(
        self,
        contract: EvaluationContract,
        case: EvaluationCase,
        actual_subqueries: Any,
        entity: Any,
        id_mapping: dict[str, str],
    ) -> list[dict[str, Any]]:
        actual_by_id = {
            item.get("id"): item for item in actual_subqueries if isinstance(item, dict)
        }
        upstream: dict[str, list[dict[str, Any]]] = {}
        statuses: dict[str, str] = {}
        records: list[dict[str, Any]] = []

        for expected in contract.subqueries:
            dependency_failed = any(
                statuses.get(dependency) != "PASS" for dependency in expected.depends_on
            )
            if dependency_failed:
                statuses[expected.id] = "BLOCKED_BY_DEPENDENCY"
                records.append(
                    {
                        "id": expected.id,
                        "tool": expected.tool,
                        "expectedQuestion": expected.question,
                        "businessRules": list(expected.business_rules),
                        "requiredOutputs": list(expected.required_outputs),
                        "goldFile": expected.gold_file.name,
                        "status": "BLOCKED_BY_DEPENDENCY",
                        "failureCategory": "DEPENDENCY_BLOCKED",
                        "generatedQuery": None,
                        "upstreamInputs": {},
                        "checks": {
                            "generation": None,
                            "readOnly": None,
                            "execution": None,
                            "resultContract": None,
                            "result": None,
                        },
                    }
                )
                continue

            actual = actual_by_id.get(id_mapping.get(expected.id))
            if not isinstance(actual, dict) or actual.get("tool") != expected.tool:
                statuses[expected.id] = "FAIL"
                records.append(
                    {
                        "id": expected.id,
                        "tool": expected.tool,
                        "expectedQuestion": expected.question,
                        "businessRules": list(expected.business_rules),
                        "requiredOutputs": list(expected.required_outputs),
                        "goldFile": expected.gold_file.name,
                        "status": "FAIL",
                        "failureCategory": "SUBQUERY_INTEGRATION_CONTRACT_MISMATCH",
                        "generatedQuery": None,
                        "upstreamInputs": {},
                        "error": "SUBQUERY_INTEGRATION_CONTRACT_MISMATCH",
                        "checks": {
                            "generation": False,
                            "readOnly": None,
                            "execution": None,
                            "resultContract": None,
                            "result": None,
                        },
                    }
                )
                continue

            inputs = collect_input_bindings(expected.input_bindings, upstream)
            record: dict[str, Any] = {
                "id": expected.id,
                "tool": expected.tool,
                "question": actual.get("question"),
                "expectedQuestion": expected.question,
                "businessRules": list(expected.business_rules),
                "requiredOutputs": list(expected.required_outputs),
                "goldFile": expected.gold_file.name,
                "status": "FAIL",
                "generatedQuery": None,
                "upstreamInputs": inputs,
                "checks": {
                    "generation": False,
                    "readOnly": False,
                    "execution": False,
                    "resultContract": None,
                    "result": None,
                },
            }
            try:
                generated_query = self._generate_query(expected, actual, entity, inputs)
                record["generatedQuery"] = generated_query
                record["checks"]["generation"] = True
                record["checks"]["readOnly"] = True
            except OpenAIError as exc:
                raise InfrastructureError(f"OpenAI query 생성 실패: {exc}") from exc
            except QuerySafetyError as exc:
                record["failureCategory"] = "READ_ONLY_VIOLATION"
                record["error"] = str(exc)
                statuses[expected.id] = "FAIL"
                records.append(record)
                continue
            except ValueError as exc:
                record["failureCategory"] = "QUERY_GENERATION_ERROR"
                record["error"] = str(exc)
                statuses[expected.id] = "FAIL"
                records.append(record)
                continue
            except Exception as exc:
                if isinstance(exc, _CONNECTION_ERRORS):
                    raise InfrastructureError(f"DB/API 연결 실패: {exc}") from exc
                record["failureCategory"] = "QUERY_GENERATION_ERROR"
                record["error"] = str(exc)
                statuses[expected.id] = "FAIL"
                records.append(record)
                continue

            try:
                candidate_rows = self._execute(
                    expected.tool,
                    generated_query,
                    {},
                    expected.max_rows,
                )
                record["checks"]["execution"] = True
                candidate_normalized = normalize_rows(
                    candidate_rows,
                    required_outputs=expected.required_outputs,
                    aliases=expected.aliases,
                    ordering=expected.ordering,
                )
                record["checks"]["resultContract"] = True
            except psycopg.errors.QueryCanceled as exc:
                record["failureCategory"] = "QUERY_TIMEOUT"
                record["error"] = str(exc)
                statuses[expected.id] = "FAIL"
                records.append(record)
                continue
            except _CONNECTION_ERRORS as exc:
                raise InfrastructureError(f"후보 쿼리 DB 연결 실패: {exc}") from exc
            except ResultContractError as exc:
                record["checks"]["resultContract"] = False
                record["failureCategory"] = "RESULT_CONTRACT_MISMATCH"
                record["error"] = str(exc)
                statuses[expected.id] = "FAIL"
                records.append(record)
                continue
            except Exception as exc:
                record["failureCategory"] = "QUERY_EXECUTION_ERROR"
                record["error"] = str(exc)
                statuses[expected.id] = "FAIL"
                records.append(record)
                continue

            parameters = {**case.parameters, **inputs}
            gold_normalized, gold_hash = self._gold_result(expected, parameters)
            candidate_hash = normalized_sha256(candidate_normalized)
            result_pass = candidate_hash == gold_hash
            record.update(
                {
                    "status": "PASS" if result_pass else "FAIL",
                    "candidateHash": candidate_hash,
                    "goldHash": gold_hash,
                    "candidateRowCount": len(candidate_normalized),
                    "goldRowCount": len(gold_normalized),
                }
            )
            record["checks"]["result"] = result_pass
            if not result_pass:
                record["failureCategory"] = "RESULT_VALUE_MISMATCH"
                record["error"] = "RESULT_HASH_MISMATCH"
                record["candidateSample"] = candidate_normalized[:5]
                record["goldSample"] = gold_normalized[:5]
            statuses[expected.id] = record["status"]
            records.append(record)
            if result_pass:
                upstream[expected.id] = candidate_normalized
        return records

    def _evaluate_case_source(
        self, case: EvaluationCase, run_number: int
    ) -> dict[str, Any]:
        if self.resolve_entity is None or self.route_query is None:
            raise InfrastructureError("모델 평가에는 OpenAI client가 필요합니다.")
        contract = self.manifest.contracts[case.contract_id]
        record: dict[str, Any] = {
            "caseId": case.case_id,
            "contractId": case.contract_id,
            "suite": case.suite,
            "run": run_number,
            "question": case.question,
            "route": contract.route,
            "supportStatus": contract.support_status,
        }
        try:
            entity_result = self.resolve_entity({"query": case.question})
            entity = entity_result.get("entity")
        except OpenAIError as exc:
            raise InfrastructureError(f"OpenAI entity 확정 실패: {exc}") from exc
        except psycopg.Error as exc:
            raise InfrastructureError(f"entity 확정 DB 실패: {exc}") from exc
        except (AppError, ValueError) as exc:
            record.update(
                {
                    "entity": None,
                    "entityError": str(exc),
                    "checks": {"entity": False},
                }
            )
            entity = None
        except Exception as exc:
            raise InfrastructureError(f"entity 확정 API 오류: {exc}") from exc
        entity_pass = entity_matches(contract.expected_entities, entity)
        record["entity"] = entity

        try:
            plan = self.route_query({"query": case.question, "entity": entity})
        except OpenAIError as exc:
            raise InfrastructureError(f"OpenAI route 생성 실패: {exc}") from exc
        except RoutePlanError as exc:
            plan = {"tool_plan": exc.tool_plan, "subqueries": []}
            record["planningError"] = str(exc)
            record["planningResponse"] = exc.raw_response
        except ValueError as exc:
            plan = {"tool_plan": None, "subqueries": []}
            record["planningError"] = str(exc)
        except Exception as exc:
            raise InfrastructureError(f"route 생성 API 오류: {exc}") from exc

        comparison = compare_execution_contract(
            contract,
            case,
            plan.get("tool_plan"),
            plan.get("subqueries"),
            plan.get("resultTransform"),
        )
        subquery_records = self._evaluate_subqueries(
            contract,
            case,
            plan.get("subqueries", []),
            entity,
            comparison["idMapping"],
        )
        all_subqueries_pass = all(item["status"] == "PASS" for item in subquery_records)
        result_values = [item["checks"].get("result") for item in subquery_records]
        if any(value is False for value in result_values):
            semantic_result_pass: bool | None = False
        elif result_values and all(value is True for value in result_values):
            semantic_result_pass = True
        else:
            semantic_result_pass = None
        result_contract_values = [
            item["checks"].get("resultContract") for item in subquery_records
        ]
        if any(value is False for value in result_contract_values):
            result_contract_pass: bool | None = False
        elif result_contract_values and all(
            value is True for value in result_contract_values
        ):
            result_contract_pass = True
        else:
            result_contract_pass = None
        query_pipeline_pass = (
            entity_pass
            and comparison["routingPass"]
            and comparison["splitPass"]
            and all_subqueries_pass
        )
        generated = [
            item
            for item in subquery_records
            if item["status"] != "BLOCKED_BY_DEPENDENCY"
        ]
        checks = {
            "entity": entity_pass,
            "routing": comparison["routingPass"],
            "split": comparison["splitPass"],
            "generation": bool(generated)
            and all(item["checks"]["generation"] is True for item in generated),
            "execution": bool(generated)
            and all(item["checks"]["execution"] is True for item in generated),
            "resultContract": result_contract_pass,
            "result": semantic_result_pass,
        }
        failure_reasons: list[str] = []
        if not entity_pass:
            failure_reasons.append("ENTITY_MISMATCH")
        if not comparison["routingPass"]:
            failure_reasons.append("ROUTE_MISMATCH")
        if not comparison["splitPass"]:
            failure_reasons.append("SUBQUERY_INTEGRATION_CONTRACT_MISMATCH")
        failure_reasons.extend(
            str(item["failureCategory"])
            for item in subquery_records
            if item.get("failureCategory") not in {None, "DEPENDENCY_BLOCKED"}
        )
        failure_reasons = list(dict.fromkeys(failure_reasons))
        source_mode_final_evaluated = (
            contract.support_status == "FULLY_EVALUATED"
            and contract.final_result is None
        )
        record.update(
            {
                "toolPlan": plan.get("tool_plan"),
                "subqueryPlan": plan.get("subqueries"),
                "contractComparison": comparison,
                "subqueries": subquery_records,
                "checks": checks,
                "failureReasons": failure_reasons,
                "queryPipelinePass": query_pipeline_pass,
                "semanticResultPass": semantic_result_pass,
                # source mode는 하위 query를 각각 진단할 뿐 composition을 실행하지
                # 않는다. 명시적인 finalResult 계약이 있는 HYBRID를 source hash가
                # 맞았다는 이유만으로 최종 결과까지 통과한 것으로 기록하지 않는다.
                "finalResultEvaluated": source_mode_final_evaluated,
                "finalResultPass": (
                    semantic_result_pass if source_mode_final_evaluated else None
                ),
                "status": "PASS" if query_pipeline_pass else "FAIL",
            }
        )
        return record

    def _invoke_orchestrator(self, query: str) -> dict[str, Any]:
        graph = self.orchestrator_graph
        if graph is None:
            raise InfrastructureError("orchestrator evaluation graph가 없습니다.")

        async def collect_updates() -> dict[str, Any]:
            state: dict[str, Any] = {"query": query}
            try:
                async for update in graph.astream(
                    {"query": query}, stream_mode="updates"
                ):
                    if not isinstance(update, dict):
                        continue
                    for node_update in update.values():
                        if isinstance(node_update, dict):
                            state.update(node_update)
            except Exception as exc:
                # 라우팅/엔티티 단계가 실패해도 그 전에 완료된 production state를
                # 평가 레코드에 남긴다. 예외의 분류와 전파 여부는 호출부가 결정한다.
                exc.__dict__["evaluation_state"] = state
                raise
            return state

        return self._run_async(collect_updates())

    @staticmethod
    def _planning_measurements(
        contract: EvaluationContract,
        actual_subqueries: Any,
        id_mapping: dict[str, str],
    ) -> tuple[bool, bool]:
        actual_by_id = (
            {
                item.get("id"): item
                for item in actual_subqueries
                if isinstance(item, dict)
            }
            if isinstance(actual_subqueries, list)
            else {}
        )
        required_outputs_pass = len(actual_by_id) == len(contract.subqueries)
        binding_pass = len(actual_by_id) == len(contract.subqueries)
        for expected in contract.subqueries:
            actual_id = id_mapping.get(expected.id)
            actual = actual_by_id.get(actual_id)
            if not isinstance(actual, dict):
                required_outputs_pass = False
                binding_pass = False
                continue
            outputs = actual.get("requiredOutputs")
            required_outputs_pass = required_outputs_pass and (
                isinstance(outputs, list)
                and len(outputs) == len(set(outputs))
                and set(expected.required_outputs).issubset(outputs)
            )
            translated_bindings = {
                key: f"{id_mapping.get(source.split('.', 1)[0])}.{source.split('.', 1)[1]}"
                for key, source in expected.input_bindings.items()
            }
            binding_pass = binding_pass and (
                actual.get("inputBindings", {}) == translated_bindings
            )
        return required_outputs_pass, binding_pass

    @staticmethod
    def _attempt_execution_pass(attempt: dict[str, Any]) -> bool:
        error = attempt.get("error")
        return (
            error in (None, "EMPTY_RESULT")
            or isinstance(error, str)
            and "필수 alias" in error
        )

    def _evaluate_orchestrator_sources(
        self,
        contract: EvaluationContract,
        case: EvaluationCase,
        state: dict[str, Any],
        id_mapping: dict[str, str],
    ) -> list[dict[str, Any]]:
        """production source 결과를 source Gold와 비교한다.

        candidate는 이미 실제 계획의 binding으로 실행된 뒤다. 여기서 expected
        계약은 오직 정규화·Gold 실행·채점에만 사용한다.
        """
        actual_subqueries = state.get("subqueries", [])
        actual_by_id = {
            item.get("id"): item for item in actual_subqueries if isinstance(item, dict)
        }
        gold_upstream: dict[str, list[dict[str, Any]]] = {}
        records: list[dict[str, Any]] = []
        for expected in contract.subqueries:
            actual = actual_by_id.get(id_mapping.get(expected.id))
            source_field = "sql_result" if expected.tool == "sql" else "graph_result"
            query_field = "sql_query" if expected.tool == "sql" else "cypher_query"
            source = state.get(source_field)
            inputs = collect_input_bindings(expected.input_bindings, gold_upstream)
            parameters = {**case.parameters, **inputs}
            gold_normalized, gold_hash = self._gold_result(expected, parameters)
            gold_upstream[expected.id] = gold_normalized
            record: dict[str, Any] = {
                "id": expected.id,
                "actualId": actual.get("id") if isinstance(actual, dict) else None,
                "tool": expected.tool,
                "expectedQuestion": expected.question,
                "requiredOutputs": list(expected.required_outputs),
                "goldFile": expected.gold_file.name,
                "generatedQuery": state.get(query_field),
                "upstreamInputs": inputs,
                "status": "FAIL",
                "checks": {
                    "generation": isinstance(state.get(query_field), str),
                    "readOnly": None,
                    "safety": None,
                    "execution": False,
                    "resultContract": None,
                    "result": None,
                },
            }
            if not isinstance(actual, dict):
                record.update(
                    {
                        "failureCategory": "SUBQUERY_INTEGRATION_CONTRACT_MISMATCH",
                        "error": "실제 계획에 대응하는 subquery가 없습니다.",
                    }
                )
                records.append(record)
                continue
            if not isinstance(source, dict):
                record.update(
                    {
                        "failureCategory": "DEPENDENCY_BLOCKED",
                        "error": "production 실행 결과가 없습니다.",
                    }
                )
                records.append(record)
                continue

            attempts = source.get("attempts", [])
            record["attempts"] = attempts if isinstance(attempts, list) else []
            safety_blocked = any(
                isinstance(attempt, dict)
                and isinstance(attempt.get("error"), str)
                and "안전 정책" in attempt["error"]
                for attempt in record["attempts"]
            )
            record["checks"]["safety"] = not safety_blocked
            record["checks"]["readOnly"] = not safety_blocked
            rows = source.get("result")
            if source.get("error") is not None or not isinstance(rows, list):
                record.update(
                    {
                        "failureCategory": (
                            "READ_ONLY_VIOLATION"
                            if safety_blocked
                            else "QUERY_EXECUTION_ERROR"
                        ),
                        "error": source.get("error") or "result가 배열이 아닙니다.",
                    }
                )
                records.append(record)
                continue
            record["checks"]["execution"] = True
            try:
                candidate_normalized = normalize_rows(
                    rows,
                    required_outputs=expected.required_outputs,
                    aliases=expected.aliases,
                    ordering=expected.ordering,
                )
                record["checks"]["resultContract"] = True
            except ResultContractError as exc:
                record.update(
                    {
                        "failureCategory": "RESULT_CONTRACT_MISMATCH",
                        "error": str(exc),
                    }
                )
                record["checks"]["resultContract"] = False
                records.append(record)
                continue

            candidate_hash = normalized_sha256(candidate_normalized)
            result_pass = candidate_hash == gold_hash
            record.update(
                {
                    "status": "PASS" if result_pass else "FAIL",
                    "candidateHash": candidate_hash,
                    "goldHash": gold_hash,
                    "candidateRowCount": len(candidate_normalized),
                    "goldRowCount": len(gold_normalized),
                }
            )
            record["checks"]["result"] = result_pass
            if not result_pass:
                record.update(
                    {
                        "failureCategory": "RESULT_VALUE_MISMATCH",
                        "error": "RESULT_HASH_MISMATCH",
                        "candidateSample": candidate_normalized[:5],
                        "goldSample": gold_normalized[:5],
                    }
                )
            records.append(record)
        return records

    def _evaluate_case_orchestrator(
        self, case: EvaluationCase, run_number: int
    ) -> dict[str, Any]:
        if not isinstance(self.openai_client, CountingOpenAIClient):
            raise InfrastructureError(
                "모델 평가에는 counting OpenAI client가 필요합니다."
            )
        self.openai_client.reset_case()
        started = perf_counter()
        contract = self.manifest.contracts[case.contract_id]
        record: dict[str, Any] = {
            "caseId": case.case_id,
            "contractId": case.contract_id,
            "suite": case.suite,
            "run": run_number,
            "question": case.question,
            "route": contract.route,
            "supportStatus": contract.support_status,
        }
        state: dict[str, Any] = {"query": case.question}
        pipeline_exception: Exception | None = None
        try:
            state = self._invoke_orchestrator(case.question)
        except (OpenAIError, *_CONNECTION_ERRORS) as exc:
            raise InfrastructureError(
                f"production orchestrator 연결 실패: {exc}"
            ) from exc
        except (AppError, RoutePlanError, ValueError) as exc:
            pipeline_exception = exc
            state = getattr(exc, "evaluation_state", state)
            if isinstance(exc, RoutePlanError):
                state["tool_plan"] = exc.tool_plan
                state.setdefault("subqueries", [])
                record["planningResponse"] = exc.raw_response
            record["planningError"] = str(exc)
        except Exception as exc:
            raise InfrastructureError(
                f"production orchestrator 실행 실패: {exc}"
            ) from exc

        entity = state.get("entity")
        entity_pass = entity_matches(contract.expected_entities, entity)
        plan_tool_plan = state.get("tool_plan")
        actual_subqueries = state.get("subqueries", [])
        comparison = compare_execution_contract(
            contract,
            case,
            plan_tool_plan,
            actual_subqueries,
            state.get("resultTransform"),
        )
        required_outputs_pass, binding_pass = self._planning_measurements(
            contract, actual_subqueries, comparison["idMapping"]
        )
        subquery_records = self._evaluate_orchestrator_sources(
            contract, case, state, comparison["idMapping"]
        )

        attempted_sources = [item for item in subquery_records if item.get("attempts")]
        attempts = [
            attempt
            for item in attempted_sources
            for attempt in item.get("attempts", [])
            if isinstance(attempt, dict)
        ]
        first_attempt_execution_pass = bool(attempted_sources) and all(
            self._attempt_execution_pass(item["attempts"][0])
            for item in attempted_sources
        )
        final_execution_pass = bool(subquery_records) and all(
            item.get("checks", {}).get("execution") is True for item in subquery_records
        )
        recovered_by_retry = any(
            len(item.get("attempts", [])) > 1
            and item.get("checks", {}).get("execution") is True
            and item["attempts"][0].get("error") is not None
            for item in subquery_records
        )
        result_contract_values = [
            item.get("checks", {}).get("resultContract") for item in subquery_records
        ]
        result_contract_pass: bool | None
        if any(value is False for value in result_contract_values):
            result_contract_pass = False
        elif result_contract_values and all(
            value is True for value in result_contract_values
        ):
            result_contract_pass = True
        else:
            result_contract_pass = None
        result_values = [
            item.get("checks", {}).get("result") for item in subquery_records
        ]
        if any(value is False for value in result_values):
            source_result_pass: bool | None = False
        elif result_values and all(value is True for value in result_values):
            source_result_pass = True
        else:
            source_result_pass = None

        composed = state.get("composed_result")
        composition_pass = isinstance(composed, dict) and composed.get("error") is None
        final_evaluated = (
            contract.final_result is not None
            or contract.support_status == "FULLY_EVALUATED"
        )
        final_comparison: dict[str, Any] | None = None
        final_contract_pass: bool | None = None
        final_result_pass: bool | None
        if contract.final_result is not None:
            final_result_pass, final_comparison = self._score_final_result(
                contract.final_result, composed
            )
            final_contract_pass = final_comparison["resultContractPass"]
            composed_result_pass: bool | None = final_result_pass
        else:
            composed_result_pass = (
                source_result_pass if final_evaluated and composition_pass else None
            )
            final_result_pass = composed_result_pass if final_evaluated else None
        safety_values = [
            item.get("checks", {}).get("safety") for item in subquery_records
        ]
        safety_pass = not any(value is False for value in safety_values)
        generation_pass = bool(subquery_records) and all(
            item.get("checks", {}).get("generation") is True
            for item in subquery_records
        )

        if not entity_pass:
            failure_stage = "ENTITY"
        elif not comparison["routingPass"]:
            failure_stage = "ROUTING"
        elif (
            not comparison["splitPass"] or not required_outputs_pass or not binding_pass
        ):
            failure_stage = "PLANNING"
        elif not generation_pass:
            failure_stage = "GENERATION"
        elif not safety_pass:
            failure_stage = "SAFETY"
        elif not final_execution_pass:
            failure_stage = "EXECUTION"
        elif result_contract_pass is not True:
            failure_stage = "RESULT_CONTRACT"
        elif not composition_pass:
            failure_stage = "COMPOSITION"
        elif final_contract_pass is False:
            failure_stage = "RESULT_CONTRACT"
        elif source_result_pass is not True or (
            final_evaluated and final_result_pass is not True
        ):
            failure_stage = "RESULT_SEMANTIC"
        else:
            failure_stage = None

        query_pipeline_pass = failure_stage is None
        failure_reasons = list(
            dict.fromkeys(
                str(item["failureCategory"])
                for item in subquery_records
                if item.get("failureCategory") is not None
            )
        )
        if pipeline_exception is not None and not failure_reasons:
            failure_reasons.append(type(pipeline_exception).__name__)
        checks = {
            "entity": entity_pass,
            "routing": comparison["routingPass"],
            "split": comparison["splitPass"],
            "requiredOutputs": required_outputs_pass,
            "binding": binding_pass,
            "generation": generation_pass,
            "safety": safety_pass,
            "execution": final_execution_pass,
            "resultContract": result_contract_pass,
            "composition": composition_pass,
            "result": source_result_pass,
            "finalResult": final_result_pass,
        }
        record.update(
            {
                "entity": entity,
                "toolPlan": plan_tool_plan,
                "subqueryPlan": actual_subqueries,
                "contractComparison": comparison,
                "subqueries": subquery_records,
                "composedResult": composed,
                "finalResultComparison": final_comparison,
                "checks": checks,
                "failureReasons": failure_reasons,
                "entityPass": entity_pass,
                "routePass": comparison["routingPass"],
                "splitPass": comparison["splitPass"],
                "requiredOutputsPass": required_outputs_pass,
                "bindingPass": binding_pass,
                "firstAttemptExecutionPass": first_attempt_execution_pass,
                "recoveredByRetry": recovered_by_retry,
                "attemptCount": len(attempts),
                "sourceResultPass": source_result_pass,
                "composedResultPass": composed_result_pass,
                "finalResultPass": final_result_pass,
                "failureStage": failure_stage,
                "queryPipelinePass": query_pipeline_pass,
                "semanticResultPass": source_result_pass,
                "finalResultEvaluated": final_evaluated,
                "status": "PASS" if query_pipeline_pass else "FAIL",
                **self.openai_client.snapshot(),
                "elapsedMs": round((perf_counter() - started) * 1000, 3),
            }
        )
        return record

    def _evaluate_case(self, case: EvaluationCase, run_number: int) -> dict[str, Any]:
        """선택한 실행 모드로 한 case를 평가한다.

        object.__new__ 기반 기존 단위 테스트는 execution_mode가 없으므로 예전
        source 경로를 사용한다. 실제 CLI 기본값은 orchestrator다.
        """
        if getattr(self, "execution_mode", "source") == "orchestrator":
            return self._evaluate_case_orchestrator(case, run_number)
        return self._evaluate_case_source(case, run_number)

    def run(self, cases: list[EvaluationCase], runs: int) -> EvaluationRun:
        snapshot = self.validate_snapshot()
        records: list[dict[str, Any]] = []
        infrastructure_error = False
        for case in cases:
            for run_number in range(1, runs + 1):
                try:
                    records.append(self._evaluate_case(case, run_number))
                except InfrastructureError as exc:
                    infrastructure_error = True
                    contract = self.manifest.contracts[case.contract_id]
                    records.append(
                        {
                            "caseId": case.case_id,
                            "contractId": case.contract_id,
                            "suite": case.suite,
                            "run": run_number,
                            "question": case.question,
                            "route": contract.route,
                            "supportStatus": contract.support_status,
                            "status": "ERROR",
                            "error": str(exc),
                            "queryPipelinePass": False,
                            "semanticResultPass": None,
                            "finalResultEvaluated": (
                                contract.support_status == "FULLY_EVALUATED"
                            ),
                            "finalResultPass": None,
                        }
                    )
        return EvaluationRun(records, snapshot, infrastructure_error)
