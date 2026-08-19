"""구조화 MVP 적재의 사전(참조 무결성)·사후(건수/중복/fixture) 검증 함수."""

from typing import Any


def find_dangling_relationship_rows(
    relationship_rows: list[dict[str, Any]],
    *,
    from_key: str,
    to_key: str,
    from_ids: set[Any],
    to_ids: set[Any],
) -> list[dict[str, Any]]:
    """관계 행 중 시작/도착 노드가 아직 적재되지 않은(고아) 행을 찾는다.

    structured_mvp_loading_rules.md 5절: "하나라도 존재하면 관계를 조용히
    버리지 않고 적재를 실패시킨다. 실패한 business key 목록을 로그에 남긴다."
    이 함수는 그 "실패한 business key 목록"을 계산하는 순수 함수다.
    """
    return [
        row
        for row in relationship_rows
        if row[from_key] not in from_ids or row[to_key] not in to_ids
    ]
