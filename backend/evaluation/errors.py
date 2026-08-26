"""평가 실행기의 오류 분류."""


class EvaluationError(Exception):
    """평가 도메인의 기본 오류."""


class ConfigurationError(EvaluationError):
    """manifest 또는 환경변수 설정 오류."""


class InfrastructureError(EvaluationError):
    """API, DB 연결, snapshot 또는 Gold 실행 오류."""


class QuerySafetyError(EvaluationError):
    """후보 쿼리가 읽기 전용 정책을 위반함."""


class ResultContractError(EvaluationError):
    """결과가 필수 필드와 alias 계약을 충족하지 못함."""
