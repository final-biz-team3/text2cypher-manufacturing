"""읽기 전용 정책 위반을 쿼리 생성 재시도에 전달하는 예외."""


class GeneratedQueryRejectedError(ValueError):
    """생성 쿼리가 DB 실행 전에 읽기 전용 정책에서 거부됨."""
