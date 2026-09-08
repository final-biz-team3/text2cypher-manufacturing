#!/usr/bin/env bash

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(CDPATH= cd -- "${script_dir}/.." && pwd)"
requested_commit="HEAD"
evaluation_args=()

while (($#)); do
  case "$1" in
    --commit)
      if (($# < 2)); then
        echo "--commit에는 commit SHA가 필요합니다." >&2
        exit 2
      fi
      requested_commit="$2"
      shift 2
      ;;
    --commit=*)
      requested_commit="${1#--commit=}"
      shift
      ;;
    *)
      evaluation_args+=("$1")
      shift
      ;;
  esac
done

commit="$(git -C "${project_root}" rev-parse --verify "${requested_commit}^{commit}")" || {
  echo "유효한 commit을 찾을 수 없습니다: ${requested_commit}" >&2
  exit 2
}

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/t2q-clean-worktree.XXXXXX")"
worktree_path="${temporary_root}/repo"
run_log="${temporary_root}/evaluation.log"
worktree_added=false

cleanup() {
  if [[ "${worktree_added}" == true ]]; then
    git -C "${project_root}" worktree remove --force "${worktree_path}" >/dev/null 2>&1 || true
  fi
  if [[ -d "${temporary_root}" ]]; then
    rm -f "${run_log}"
    rmdir "${temporary_root}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

git -C "${project_root}" worktree add --detach "${worktree_path}" "${commit}"
worktree_added=true

if [[ ! -f "${project_root}/.env" ]]; then
  echo "원 저장소의 .env를 찾을 수 없습니다: ${project_root}/.env" >&2
  exit 2
fi
ln -s "${project_root}/.env" "${worktree_path}/.env"

if [[ -n "$(git -C "${worktree_path}" status --porcelain --untracked-files=normal)" ]]; then
  echo "detached 평가 worktree가 clean 상태가 아닙니다." >&2
  git -C "${worktree_path}" status --short >&2
  exit 2
fi

if [[ -n "${EVAL_PYTHON:-}" ]]; then
  if [[ "${EVAL_PYTHON}" = /* ]]; then
    eval_python="${EVAL_PYTHON}"
  else
    eval_python="$(command -v "${EVAL_PYTHON}")" || {
      echo "EVAL_PYTHON을 찾을 수 없습니다: ${EVAL_PYTHON}" >&2
      exit 2
    }
  fi
else
  eval_python="${project_root}/backend/venv/bin/python"
fi
if [[ ! -x "${eval_python}" ]]; then
  echo "평가 Python을 실행할 수 없습니다: ${eval_python}" >&2
  exit 2
fi

output_root="${project_root}/artifacts/t2c-eval"
mkdir -p "${output_root}"

set +e
env -u GUARD_AUDIT_LOG_PATH -u ANSWER_AUDIT_LOG_PATH \
  EVAL_PYTHON="${eval_python}" \
  EVAL_OUTPUT_ROOT="${output_root}" \
  "${worktree_path}/scripts/run-t2q-evaluation.sh" "${evaluation_args[@]}" \
  2>&1 | tee "${run_log}"
evaluation_exit_code=${PIPESTATUS[0]}
set -e

artifact_dir="$(sed -n 's/^artifact directory: //p' "${run_log}" | tail -n 1)"
if [[ -z "${artifact_dir}" || "${artifact_dir}" != "${output_root}/"* ]]; then
  echo "원 저장소 아래의 평가 artifact 경로를 확인하지 못했습니다." >&2
  exit 2
fi
for required_file in \
  evaluation.json \
  report.md \
  query_guard_audit.jsonl \
  answer_validation_audit.jsonl; do
  if [[ ! -f "${artifact_dir}/${required_file}" ]]; then
    echo "평가 artifact가 누락됐습니다: ${artifact_dir}/${required_file}" >&2
    exit 2
  fi
done

echo "clean worktree artifact: ${artifact_dir}"
exit "${evaluation_exit_code}"
