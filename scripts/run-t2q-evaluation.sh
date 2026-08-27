#!/usr/bin/env bash

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(CDPATH= cd -- "${script_dir}/.." && pwd)"
cd "${project_root}"

for argument in "$@"; do
  case "${argument}" in
    --output-dir | --output-dir=*)
      echo "--output-dir은 이 스크립트에서 지정할 수 없습니다. 기존 Python CLI를 직접 사용하세요." >&2
      exit 2
      ;;
  esac
done

if [[ -n "${EVAL_PYTHON:-}" ]]; then
  eval_python="${EVAL_PYTHON}"
elif [[ -x "${project_root}/backend/venv/bin/python" ]]; then
  eval_python="${project_root}/backend/venv/bin/python"
elif [[ -x "${project_root}/backend/venv/Scripts/python.exe" ]]; then
  eval_python="${project_root}/backend/venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  eval_python="$(command -v python3)"
else
  echo "평가에 사용할 Python을 찾을 수 없습니다." >&2
  exit 2
fi

if ! command -v "${eval_python}" >/dev/null 2>&1; then
  echo "평가에 사용할 Python을 실행할 수 없습니다: ${eval_python}" >&2
  exit 2
fi

kst_stamp="$(TZ=Asia/Seoul date +%F-%H%M%S)"
run_date="${kst_stamp%-*}"
run_time="${kst_stamp##*-}"
commit="$(git rev-parse --short=7 HEAD 2>/dev/null || true)"
commit="${commit:-unknown}"
if git_status="$(git status --porcelain --untracked-files=normal 2>/dev/null)"; then
  if [[ -n "${git_status}" ]]; then
    worktree_state="dirty"
  else
    worktree_state="clean"
  fi
else
  worktree_state="dirty"
fi

output_dir="${project_root}/artifacts/t2c-eval/${run_date}/${run_time}-${commit}-${worktree_state}"

set +e
PYTHONPATH="${project_root}/backend${PYTHONPATH:+:${PYTHONPATH}}" \
  "${eval_python}" -m evaluation \
  --suite canonical \
  --ids RQ01-RQ20 \
  --runs 1 \
  "$@" \
  --output-dir "${output_dir}"
evaluation_exit_code=$?
set -e

if [[ -f "${output_dir}/evaluation.json" ]]; then
  "${eval_python}" - "${output_dir}" <<'PY' || true
import json
import sys
from pathlib import Path


output_dir = Path(sys.argv[1])
evaluation = json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))
summary = evaluation["summary"]
records = evaluation["cases"]


def score(label: str, passed: int, total: int) -> None:
    accuracy = "-" if total == 0 else f"{passed / total:.1%}"
    print(f"  {label}: {passed}/{total} ({accuracy})")


print("\n핵심 점수")
if summary.get("goldValidationOnly"):
    validated = sum(record.get("status") == "GOLD_VALIDATED" for record in records)
    partials = [item for record in records for item in record.get("subqueries", [])]
    score("Gold 검증 완료", validated, len(records))
    score(
        "Gold 부분 쿼리 PASS",
        sum(item.get("status") == "PASS" for item in partials),
        len(partials),
    )
else:
    completed = [record for record in records if record.get("status") != "ERROR"]
    semantic_applicable = [
        record
        for record in completed
        if isinstance(record.get("semanticResultPass"), bool)
    ]
    final_evaluated = [
        record for record in completed if record.get("finalResultEvaluated") is True
    ]
    final_applicable = [
        record
        for record in final_evaluated
        if isinstance(record.get("finalResultPass"), bool)
    ]
    hybrid = [record for record in completed if record.get("route") == "HYBRID"]
    score("채점 실행 완료 (인프라 정상)", len(completed), len(records))
    score(
        "엄격 파이프라인 PASS",
        sum(record.get("queryPipelinePass") is True for record in completed),
        len(completed),
    )
    score(
        "의미 결과 비교 가능",
        len(semantic_applicable),
        len(completed),
    )
    score(
        "검증된 의미 PASS",
        sum(record.get("semanticResultPass") is True for record in completed),
        len(completed),
    )
    score(
        "비교 가능한 결과 중 정확도",
        sum(record.get("semanticResultPass") is True for record in semantic_applicable),
        len(semantic_applicable),
    )
    score("최종 결과 평가 대상", len(final_evaluated), len(completed))
    score("최종 결과 비교 가능", len(final_applicable), len(final_evaluated))
    score(
        "최종 결과 정확도",
        sum(record.get("finalResultPass") is True for record in final_applicable),
        len(final_applicable),
    )
    score(
        "HYBRID 분할",
        sum(record.get("checks", {}).get("split") is True for record in hybrid),
        len(hybrid),
    )
PY
fi

if [[ -f "${output_dir}/report.md" ]]; then
  echo "report.md: ${output_dir}/report.md"
else
  echo "report.md가 생성되지 않았습니다: ${output_dir}/report.md" >&2
fi

exit "${evaluation_exit_code}"
