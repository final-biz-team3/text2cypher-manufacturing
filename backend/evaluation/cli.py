"""`python -m evaluation` 명령행 진입점."""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from core.postgres import bootstrap_postgres, close_pool, open_pool
from evaluation.database import ReadOnlyDatabaseExecutor
from evaluation.errors import ConfigurationError, InfrastructureError
from evaluation.models import EvaluationCase, load_manifest
from evaluation.reporting import build_summary, write_artifacts
from evaluation.runner import EvaluationRun, EvaluationRunner

# resolve_entity/route_query/generate_sql/generate_cypher가 전부 async라
# 이 CLI(전체 동기)에서도 asyncio.run()으로 다리를 놓아 호출한다(runner.py
# 참고). psycopg의 async 모드는 Windows 기본 ProactorEventLoop를 지원하지
# 않으므로 여기서도 main.py/conftest.py와 동일하게 정책을 고정한다.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "queries" / "evaluation" / "manifest.json"
_RQ_ID = re.compile(r"^RQ(\d{2})$")
_RQ_RANGE = re.compile(r"^(RQ\d{2})-(RQ\d{2})$")


def _parse_ids(value: str) -> set[str] | None:
    if value.casefold() == "all":
        return None
    selected: set[str] = set()
    for part in value.split(","):
        part = part.strip().upper()
        range_match = _RQ_RANGE.fullmatch(part)
        if range_match:
            start_match = _RQ_ID.fullmatch(range_match.group(1))
            end_match = _RQ_ID.fullmatch(range_match.group(2))
            assert start_match is not None and end_match is not None
            start = int(start_match.group(1))
            end = int(end_match.group(1))
            if start > end:
                raise argparse.ArgumentTypeError(f"잘못된 ID 범위: {part}")
            selected.update(f"RQ{number:02d}" for number in range(start, end + 1))
            continue
        if not _RQ_ID.fullmatch(part):
            raise argparse.ArgumentTypeError(f"잘못된 query ID: {part}")
        selected.add(part)
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RQ01~RQ20 text-to-query 평가")
    parser.add_argument(
        "--suite", choices=("canonical", "robustness", "all"), default="canonical"
    )
    parser.add_argument("--ids", default="all")
    parser.add_argument(
        "--routes", choices=("SQL", "GRAPH", "HYBRID", "all"), default="all"
    )
    parser.add_argument("--case-file", type=Path)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL"))
    parser.add_argument("--base-url")
    parser.add_argument("--validate-gold", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "artifacts" / "t2c-eval"
    )
    return parser


def _select_cases(
    cases: tuple[EvaluationCase, ...],
    contracts: dict[str, Any],
    *,
    suite: str,
    ids: set[str] | None,
    route: str,
) -> list[EvaluationCase]:
    if ids is not None:
        available_ids = set(contracts) | {case.case_id for case in cases}
        unknown_ids = sorted(ids - available_ids)
        if unknown_ids:
            raise ConfigurationError(
                "manifest에 없는 query ID: " + ", ".join(unknown_ids)
            )
    selected = [
        case
        for case in cases
        if (suite == "all" or case.suite == suite)
        and (ids is None or case.contract_id in ids or case.case_id in ids)
        and (route == "all" or contracts[case.contract_id].route == route)
    ]
    if not selected:
        raise ConfigurationError("선택 조건에 맞는 평가 case가 없습니다.")
    return selected


def _commit_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _error_artifacts(
    output_dir: Path,
    message: str,
    *,
    model: str | None,
    validate_gold: bool,
) -> None:
    result = EvaluationRun([], {}, True)
    summary = build_summary(
        result,
        model=None if validate_gold else model,
        commit=_commit_sha(),
        validate_gold=validate_gold,
    )
    summary["error"] = message
    write_artifacts(output_dir, summary, [])


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ids = _parse_ids(args.ids)
        if args.runs < 1:
            raise ConfigurationError("--runs는 1 이상이어야 합니다.")
        manifest = load_manifest(args.manifest.resolve(), args.case_file)
        cases = _select_cases(
            manifest.cases,
            manifest.contracts,
            suite=args.suite,
            ids=ids,
            route=args.routes,
        )
        if args.validate_gold:
            client = None
        else:
            if not args.model:
                raise ConfigurationError("OPENAI_MODEL 또는 --model이 필요합니다.")
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ConfigurationError("OPENAI_API_KEY가 필요합니다.")
            os.environ["OPENAI_MODEL"] = args.model
            client = AsyncOpenAI(api_key=api_key, base_url=args.base_url)

        database = ReadOnlyDatabaseExecutor.from_environment()
        if client is not None:
            asyncio.run(bootstrap_postgres())
            asyncio.run(open_pool())
        try:
            runner = EvaluationRunner(
                manifest,
                database,
                client,
                project_root=PROJECT_ROOT,
            )
            result = (
                runner.validate_gold(cases)
                if args.validate_gold
                else runner.run(cases, args.runs)
            )
        finally:
            database.close()
            if client is not None:
                asyncio.run(close_pool())

        summary = build_summary(
            result,
            model=args.model if not args.validate_gold else None,
            commit=_commit_sha(),
            validate_gold=args.validate_gold,
        )
        write_artifacts(args.output_dir, summary, result.records)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        if result.infrastructure_error:
            return 2
        return 0
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    except (ConfigurationError, InfrastructureError) as exc:
        message = str(exc)
        print(f"evaluation error: {message}", file=sys.stderr)
        _error_artifacts(
            args.output_dir,
            message,
            model=args.model,
            validate_gold=args.validate_gold,
        )
        return 2
    except Exception as exc:
        message = f"unexpected evaluation infrastructure error: {exc}"
        print(message, file=sys.stderr)
        _error_artifacts(
            args.output_dir,
            message,
            model=args.model,
            validate_gold=args.validate_gold,
        )
        return 2
    return 2
