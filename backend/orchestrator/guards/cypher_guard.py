"""LLM이 생성한 Cypher를 실행 직전에 파싱해 쓰기 절·미허가 Label/RelationshipType을
차단한다. 성숙한 Cypher 파서가 없어 문자 단위 스캔으로 구현하고, Neo4j
reader 계정(execute_cypher 쪽)과 이중 방어를 이룬다. 스키마 화이트리스트는
schema/graph_schema.yaml(GraphSchema)을 그대로 재사용한다."""

import re
from collections.abc import Callable

from agents.cypher.schema.models import GraphSchema
from orchestrator.guards.result import GuardResult
from orchestrator.guards.shared import CYPHER_WRITE_KEYWORDS, mask_query_text

_FORBIDDEN_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(keyword) for keyword in CYPHER_WRITE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_UNQUOTED_NAME = re.compile(r"\w+")


def _extract_label_and_type_references(cypher: str) -> tuple[set[str], bool]:
    """':' 뒤에 오는 Label/RelationshipType 이름을 전부 모은다 - 노드 패턴
    (`(n:A:B)`), 관계 패턴의 '|' 대안(`[:A|B]`), 백틱 식별자, WHERE절/RETURN절의
    predicate 형태(`n:Label`)까지 전부 같은 방식으로 잡는다. '{...}' 맵 리터럴
    안의 콜론(키: 값)은 레이블이 아니므로 건너뛰고, 문자열 리터럴 안의 콜론도
    건너뛴다. 인식하지 못한 콜론 구문(닫히지 않은 백틱, '::' 등)을 만나면
    unresolved=True를 반환해 호출부가 fail-closed 하도록 한다."""
    names: set[str] = set()
    unresolved = False
    depth = 0
    index = 0
    length = len(cypher)
    while index < length:
        char = cypher[index]
        next_char = cypher[index + 1] if index + 1 < length else ""
        if char == "/" and next_char == "/":
            index += 2
            while index < length and cypher[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            end = cypher.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            while index < length and cypher[index] != quote:
                index += 2 if cypher[index] == "\\" and index + 1 < length else 1
            index += 1
            continue
        if char == "`":
            end = cypher.find("`", index + 1)
            if end == -1:
                unresolved = True
                break
            index = end + 1
            continue
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            depth = max(0, depth - 1)
            index += 1
            continue
        if char == ":" and depth == 0:
            index += 1
            while True:
                while index < length and cypher[index] in " \t\r\n":
                    index += 1
                if index < length and cypher[index] == "`":
                    end = cypher.find("`", index + 1)
                    if end == -1:
                        unresolved = True
                        index = length
                        break
                    names.add(cypher[index + 1 : end])
                    index = end + 1
                else:
                    match = _UNQUOTED_NAME.match(cypher[index:])
                    if match:
                        names.add(match.group(0))
                        index += match.end()
                    else:
                        unresolved = True
                        break
                lookahead = index
                while lookahead < length and cypher[lookahead] in " \t\r\n":
                    lookahead += 1
                if lookahead < length and cypher[lookahead] == "|":
                    index = lookahead + 1
                    continue
                break
            continue
        index += 1
    return names, unresolved


def make_cypher_guard(graph_schema: GraphSchema) -> Callable[[str], GuardResult]:
    """graph_schema로 초기화된 쿼리 가드 함수를 만든다."""
    allowed_names = set(graph_schema.nodes) | set(graph_schema.relationships)

    def guard(cypher: str) -> GuardResult:
        masked = mask_query_text(cypher)
        match = _FORBIDDEN_PATTERN.search(masked)
        if match:
            return GuardResult(
                False, "WRITE_KEYWORD_DETECTED", f"쓰기 키워드 감지: {match.group(1)}"
            )

        referenced_names, unresolved = _extract_label_and_type_references(cypher)
        if unresolved:
            return GuardResult(
                False,
                "UNRECOGNIZED_LABEL_SYNTAX",
                "Label/RelationshipType 구문을 해석할 수 없어 차단합니다.",
            )

        unknown = referenced_names - allowed_names
        if unknown:
            return GuardResult(
                False,
                "UNKNOWN_LABEL_OR_RELATIONSHIP",
                f"스키마에 없는 Label/RelationshipType: {', '.join(sorted(unknown))}",
            )

        return GuardResult(True)

    return guard
