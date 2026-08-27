"""SQL/Cypher 쿼리 가드(orchestrator)와 evaluation 하네스가 함께 쓰는 금지 키워드
목록과 텍스트 마스킹 함수. 한쪽만 업데이트해서 목록이 어긋나는 걸 막기 위해
단일 소스로 둔다(이전에 EXECUTE/ANALYZE 등이 한쪽에만 반영돼 있던 드리프트가
실제로 있었음)."""

SQL_WRITE_KEYWORDS = frozenset(
    {
        "ALTER",
        "ANALYZE",
        "CALL",
        "CLUSTER",
        "COMMENT",
        "COPY",
        "CREATE",
        "DELETE",
        "DO",
        "DROP",
        "EXECUTE",
        "GRANT",
        # SELECT ... INTO는 새 테이블을 만드는 쓰기 작업이라 SELECT 형태여도 차단한다.
        "INTO",
        "INSERT",
        "LOCK",
        "MERGE",
        "REFRESH",
        "REINDEX",
        "REVOKE",
        "TRUNCATE",
        "UPDATE",
        "VACUUM",
    }
)

# 단어 단위 집합으로 통일한다("DETACH DELETE"/"LOAD CSV" 같은 구문 매칭은 공백
# 개수·개행에 따라 우회될 수 있어(실제 발견됨), 구성 단어 각각을 막는 방식이 더 안전하다.
CYPHER_WRITE_KEYWORDS = frozenset(
    {
        "CALL",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "FOREACH",
        "LOAD",
        "MERGE",
        "REMOVE",
        "SET",
    }
)


def mask_query_text(query: str) -> str:
    """문자열 리터럴·따옴표 식별자·주석 안의 내용을 공백으로 가린다.
    키워드/세미콜론 검사가 값이나 주석 안 텍스트를 코드로 오인하지 않도록
    검사 직전에만 적용한다(실행에는 원본 쿼리를 그대로 쓴다)."""
    output: list[str] = []
    index = 0
    length = len(query)
    while index < length:
        char = query[index]
        next_char = query[index + 1] if index + 1 < length else ""
        if char == "-" and next_char == "-":
            index += 2
            while index < length and query[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and next_char in {"/", "*"}:
            terminator = "\n" if next_char == "/" else "*/"
            index += 2
            while index < length:
                if terminator == "\n" and query[index] in "\r\n":
                    break
                if terminator == "*/" and query[index : index + 2] == "*/":
                    output.extend((" ", " "))
                    index += 2
                    break
                output.append(" ")
                index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            output.append(" ")
            index += 1
            while index < length:
                output.append(" ")
                if query[index] == quote:
                    if index + 1 < length and query[index + 1] == quote:
                        output.append(" ")
                        index += 2
                        continue
                    index += 1
                    break
                if query[index] == "\\" and index + 1 < length:
                    output.append(" ")
                    index += 2
                    continue
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)
