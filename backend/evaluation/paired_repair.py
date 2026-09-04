"""동일한 최초 실패 쿼리로 self-correction V1/V2를 짝지어 비교한다."""

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents.cypher.schema.loader import load_graph_schema
from agents.cypher.schema.serializer import serialize_graph_schema
from agents.generator import DEFAULT_REASONING_EFFORT
from agents.sql.schema.loader import load_sql_schema
from agents.sql.schema.serializer import serialize_sql_schema
from evaluation.database import ReadOnlyDatabaseExecutor
from evaluation.models import EvaluationCase, ExpectedSubquery, load_manifest
from evaluation.normalization import normalize_rows, normalized_sha256
from evaluation.observability import CountingOpenAIClient
from orchestrator.output_catalog import build_output_catalog
from orchestrator.subgraphs.cypher_agent import make_cypher_agent_subgraph
from orchestrator.subgraphs.sql_agent import make_sql_agent_subgraph

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "queries" / "evaluation" / "manifest.json"


@dataclass(frozen=True)
class RepairFixture:
    fixture_id: str
    case: EvaluationCase
    expected: ExpectedSubquery
    initial_query: str
    mutation: str
    entity: Any


def _replace_alias(query: str, alias: str, replacement: str) -> str:
    pattern = re.compile(
        rf"(?i)(\bAS\s+)(?:\"{re.escape(alias)}\"|`{re.escape(alias)}`|"
        rf"{re.escape(alias)}\b)"
    )
    return pattern.sub(lambda match: match.group(1) + f'"{replacement}"', query)


def build_fixtures(manifest: Any, limit: int) -> list[RepairFixture]:
    """Gold 의미는 보존하고 output alias만 깨뜨린 결정적 fixture를 만든다."""
    fixtures: list[RepairFixture] = []
    for case in manifest.cases:
        contract = manifest.contracts[case.contract_id]
        for expected in contract.subqueries:
            # upstream 결과가 필요한 hybrid 후속 쿼리는 별도 binding fixture가
            # 필요하므로 이 최초 paired 세트에서는 제외한다.
            if expected.depends_on or expected.input_bindings:
                continue
            gold = expected.gold_file.read_text(encoding="utf-8").strip()
            aliases = list(expected.required_outputs)
            for mutation, targets in (
                ("missing_first_output", aliases[:1]),
                ("missing_all_outputs", aliases),
            ):
                mutated = gold
                for alias in targets:
                    mutated = _replace_alias(mutated, alias, f"broken_{alias}")
                if mutated == gold:
                    continue
                fixtures.append(
                    RepairFixture(
                        fixture_id=f"{case.case_id}:{expected.id}:{mutation}",
                        case=case,
                        expected=expected,
                        initial_query=mutated,
                        mutation=mutation,
                        entity=contract.expected_entities,
                    )
                )
            empty_query = _force_empty(gold, expected.tool)
            if empty_query is not None:
                fixtures.append(
                    RepairFixture(
                        fixture_id=f"{case.case_id}:{expected.id}:forced_empty",
                        case=case,
                        expected=expected,
                        initial_query=empty_query,
                        mutation="forced_empty",
                        entity=contract.expected_entities,
                    )
                )
    # manifest 선언 순서대로 자르면 SQL/canonical에 치우친다. suite, tool,
    # mutation 조합을 round-robin해 동일 limit에서도 난이도와 소스를 섞는다.
    buckets: dict[tuple[str, str, str], list[RepairFixture]] = {}
    for fixture in fixtures:
        key = (fixture.case.suite, fixture.expected.tool, fixture.mutation)
        buckets.setdefault(key, []).append(fixture)
    selected: list[RepairFixture] = []
    keys = sorted(buckets)
    while len(selected) < limit and keys:
        remaining: list[tuple[str, str, str]] = []
        for key in keys:
            bucket = buckets[key]
            if bucket and len(selected) < limit:
                selected.append(bucket.pop(0))
            if bucket:
                remaining.append(key)
        keys = remaining
    return selected


def _force_empty(query: str, tool: str) -> str | None:
    """원래 의미 구조를 유지하면서 결과만 0행으로 만드는 재시도 fixture."""
    if tool == "sql":
        body = query.rstrip().rstrip(";")
        return f'SELECT * FROM (\n{body}\n) AS "pairedSource" WHERE false'
    returns = list(re.finditer(r"(?i)\bRETURN\b", query))
    if not returns:
        return None
    match = returns[-1]
    return query[: match.start()] + "WITH * WHERE false\nRETURN" + query[match.end() :]


def _normalize(
    rows: list[dict[str, Any]], expected: ExpectedSubquery
) -> list[dict[str, Any]]:
    return normalize_rows(
        rows,
        required_outputs=expected.required_outputs,
        aliases=expected.aliases,
        ordering=expected.ordering,
        strict_required_outputs=True,
    )


async def _run_fixture(
    fixture: RepairFixture,
    engine: str,
    database: ReadOnlyDatabaseExecutor,
    client: CountingOpenAIClient,
    sql_schema: Any,
    graph_schema: Any,
    sql_schema_text: str,
    graph_schema_text: str,
    semantic_context: dict[str, str],
) -> dict[str, Any]:
    expected = fixture.expected
    parameters = fixture.case.parameters

    async def execute_sql(query: str) -> Any:
        return database.execute_sql(query, parameters, max_rows=expected.max_rows)

    async def execute_cypher(query: str) -> Any:
        return database.execute_cypher(query, parameters, max_rows=expected.max_rows)

    if expected.tool == "sql":
        os.environ["SQL_REPAIR_ENGINE"] = engine
        graph = make_sql_agent_subgraph(
            client,
            execute_sql,
            sql_schema,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            semantic_context=semantic_context["sql"],
        )
        schema_text = sql_schema_text
        gold_rows = database.execute_sql(
            expected.gold_file.read_text(encoding="utf-8"),
            parameters,
            max_rows=expected.max_rows,
        )
    else:
        os.environ["CYPHER_REPAIR_ENGINE"] = engine
        assert graph_schema.query_policy is not None
        graph = make_cypher_agent_subgraph(
            client,
            execute_cypher,
            graph_schema.query_policy,
            graph_schema,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            semantic_context=semantic_context["graph"],
        )
        schema_text = graph_schema_text
        gold_rows = database.execute_cypher(
            expected.gold_file.read_text(encoding="utf-8"),
            parameters,
            max_rows=expected.max_rows,
        )

    client.reset_case()
    started = perf_counter()
    error: str | None = None
    try:
        state = await graph.ainvoke(
            {
                "query": expected.question,
                "initial_query": fixture.initial_query,
                "entity": fixture.entity,
                "schema": schema_text,
                "messages": [],
                "result": None,
                "error": None,
                "attempt_count": 0,
                "attempts": [],
                "empty_retried": False,
                "retryable": False,
                "empty_reason": None,
                "required_outputs": list(expected.required_outputs),
                "input_bindings": {},
                "business_rules": list(expected.business_rules),
            }
        )
        rows = state.get("result")
        semantic_pass = False
        if isinstance(rows, list) and state.get("error") is None:
            semantic_pass = normalized_sha256(
                _normalize(rows, expected)
            ) == normalized_sha256(_normalize(gold_rows, expected))
    except Exception as exc:  # 결과 파일에는 비밀이 없는 예외 타입만 남긴다.
        state = {}
        rows = None
        semantic_pass = False
        error = type(exc).__name__
    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    attempts = state.get("attempts", []) if isinstance(state, dict) else []
    repaired_query = None
    messages = state.get("messages", []) if isinstance(state, dict) else []
    if len(messages) > 1:
        repaired_query = messages[-1].get("content")
    return {
        "fixtureId": fixture.fixture_id,
        "contractId": fixture.case.contract_id,
        "tool": expected.tool,
        "mutation": fixture.mutation,
        "engine": engine,
        "attemptCount": len(attempts),
        "repairAttempted": len(attempts) > 1,
        "executionSuccess": state.get("error") is None and isinstance(rows, list),
        "semanticPass": semantic_pass,
        "finalIssueCode": (state.get("failure") or {}).get("code"),
        "latencyMs": elapsed_ms,
        "initialQuerySha256": normalized_sha256([{"query": fixture.initial_query}]),
        "repairedQuerySha256": (
            normalized_sha256([{"query": repaired_query}]) if repaired_query else None
        ),
        "exceptionType": error,
        **client.snapshot(),
    }


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"fixtureCount": len(records) // 2, "engines": {}}
    for engine in ("v1", "v2"):
        selected = [item for item in records if item["engine"] == engine]
        count = len(selected)
        result["engines"][engine] = {
            "runs": count,
            "executionSuccessRate": (
                sum(bool(x["executionSuccess"]) for x in selected) / count
                if count
                else 0
            ),
            "semanticPassRate": (
                sum(bool(x["semanticPass"]) for x in selected) / count if count else 0
            ),
            "averageLatencyMs": (
                round(sum(float(x["latencyMs"]) for x in selected) / count, 3)
                if count
                else 0
            ),
            "modelCalls": sum(int(x["modelCallCount"]) for x in selected),
            "inputTokens": sum(int(x["inputTokens"]) for x in selected),
            "outputTokens": sum(int(x["outputTokens"]) for x in selected),
            "estimatedCostUsd": round(
                sum(float(x["estimatedCostUsd"]) for x in selected), 10
            ),
        }
    return result


async def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_manifest(args.manifest.resolve())
    excluded_ids: set[str] = set()
    for result_path in args.exclude_results:
        document = json.loads(result_path.resolve().read_text(encoding="utf-8"))
        excluded_ids.update(
            str(record["fixtureId"])
            for record in document.get("records", [])
            if isinstance(record, dict) and record.get("fixtureId")
        )
    fixtures = [
        fixture
        for fixture in build_fixtures(manifest, 10_000)
        if fixture.fixture_id not in excluded_ids
    ][: args.limit]
    if not fixtures:
        raise RuntimeError("paired repair fixture를 만들 수 없습니다.")
    api_key = os.getenv("OPENAI_API_KEY")
    model = args.model or os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError("OPENAI_API_KEY와 OPENAI_MODEL이 필요합니다.")
    os.environ["OPENAI_MODEL"] = model
    raw_client = AsyncOpenAI(api_key=api_key, base_url=args.base_url, timeout=60.0)
    client = CountingOpenAIClient(raw_client)
    database = ReadOnlyDatabaseExecutor.from_environment(timeout_ms=args.timeout_ms)
    sql_schema = load_sql_schema(PROJECT_ROOT / "schema" / "sql_schema.yaml")
    graph_schema = load_graph_schema(PROJECT_ROOT / "schema" / "graph_schema.yaml")
    catalog = build_output_catalog(sql_schema, graph_schema)
    records: list[dict[str, Any]] = []
    try:
        for fixture in fixtures:
            for engine in ("v1", "v2"):
                records.append(
                    await _run_fixture(
                        fixture,
                        engine,
                        database,
                        client,
                        sql_schema,
                        graph_schema,
                        serialize_sql_schema(sql_schema),
                        serialize_graph_schema(graph_schema),
                        {
                            "sql": catalog.describe("sql"),
                            "graph": catalog.describe("graph"),
                        },
                    )
                )
                print(
                    f"{fixture.fixture_id} {engine}: "
                    f"semantic={records[-1]['semanticPass']} "
                    f"attempts={records[-1]['attemptCount']}"
                )
    finally:
        database.close()
        await raw_client.close()
    output = {"model": model, "summary": _summary(records), "records": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="동일 실패 쿼리 V1/V2 paired 평가")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--exclude-results",
        type=Path,
        action="append",
        default=[],
        help="이전 paired 결과의 fixtureId를 새 평가셋에서 제외한다.",
    )
    parser.add_argument("--timeout-ms", type=int, default=3000)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "self-correction" / "paired-repair.json",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
