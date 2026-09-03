"""선행 결과 행을 후속 input binding으로 변환한다."""

from typing import Any


def collect_input_bindings(
    bindings: dict[str, str],
    upstream_rows: dict[str, Any],
) -> dict[str, list[Any]]:
    """선행 결과 행의 순서와 중복도를 바꾸지 않고 투영한다."""
    result: dict[str, list[Any]] = {}
    for input_name, source in bindings.items():
        dependency_id, field = source.split(".", 1)
        if dependency_id not in upstream_rows:
            raise ValueError(
                f"input binding 실행 계획에 dependency '{dependency_id}' 결과가 없습니다."
            )
        rows = upstream_rows[dependency_id]
        if not isinstance(rows, list):
            raise ValueError(
                f"input binding dependency '{dependency_id}' 결과는 list여야 합니다."
            )

        projected: list[Any] = []
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(
                    f"input binding dependency '{dependency_id}'의 "
                    f"{row_index}번 행은 객체여야 합니다."
                )
            if field not in row:
                raise ValueError(
                    f"input binding dependency '{dependency_id}'의 "
                    f"{row_index}번 행에 source field '{field}'가 없습니다."
                )
            projected.append(row[field])
        result[input_name] = projected
    return result
