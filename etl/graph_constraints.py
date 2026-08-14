"""schema/graph_schema.yaml -> Neo4j CREATE CONSTRAINT 문 생성.

노드 키·관계 키·존재·유일성·속성 타입 4종 제약조건을 graph_schema.yaml에서
읽어 Cypher DDL로 변환한다. graph_schema.yaml이 유일한 소스이므로, 제약조건을
바꾸려면 이 파일이 아니라 graph_schema.yaml을 고치고 load_to_neo4j.py를
다시 실행하면 된다.

매핑 규칙 (graph_schema.yaml 헤더 주석과 동일):
    - 노드 키(NODE KEY):         노드의 uniqueKey/constraintName
    - 관계 키(RELATIONSHIP KEY): 관계의 naturalKey (그룹 A만 존재)
    - 존재(existence):          nullable:true가 없는 속성 (키 속성은 제외 - 이미 포함됨)
    - 유일성(uniqueness):       unique:true인 속성 (키 속성은 제외 - 이미 포함됨)
    - 속성 타입:                모든 속성의 type 필드

단독 실행하면(python etl/graph_constraints.py) DB에 접속하지 않고 생성된
Cypher 문 목록만 출력한다(검증용).
"""

import re

TYPE_KEYWORDS = {
    "STRING": "STRING",
    "INTEGER": "INTEGER",
    "FLOAT": "FLOAT",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "LOCAL_DATETIME": "LOCAL DATETIME",
}


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _node_key_statements(label: str, node: dict) -> list[str]:
    key_prop = node.get("uniqueKey")
    if not key_prop:
        return []
    name = node.get("constraintName") or f"{_snake(label)}_key"
    return [
        f"CREATE CONSTRAINT {name} IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE (n.{key_prop}) IS NODE KEY"
    ]


def _rel_key_statements(rel_type: str, rel: dict) -> list[str]:
    key_prop = rel.get("naturalKey")
    if not key_prop:
        return []
    name = f"{rel_type.lower()}_key"
    return [
        f"CREATE CONSTRAINT {name} IF NOT EXISTS "
        f"FOR ()-[r:{rel_type}]-() REQUIRE (r.{key_prop}) IS RELATIONSHIP KEY"
    ]


def _property_statements(
    name_prefix: str,
    properties: dict,
    key_prop: str | None,
    for_clause: str,
    var: str,
) -> list[str]:
    """for_clause: "FOR (n:Label)" 또는 "FOR ()-[r:TYPE]-()". var: "n" 또는 "r"."""
    statements = []
    for prop, spec in properties.items():
        prop_ref = f"{var}.{prop}"
        prop_slug = _snake(prop)

        type_name = f"{name_prefix}_{prop_slug}_type"
        neo4j_type = TYPE_KEYWORDS[spec["type"]]
        statements.append(
            f"CREATE CONSTRAINT {type_name} IF NOT EXISTS "
            f"{for_clause} REQUIRE {prop_ref} IS TYPED {neo4j_type}"
        )

        if prop == key_prop:
            continue  # NODE KEY/RELATIONSHIP KEY가 이미 존재+유일성을 포함한다

        if not spec.get("nullable"):
            exists_name = f"{name_prefix}_{prop_slug}_exists"
            statements.append(
                f"CREATE CONSTRAINT {exists_name} IF NOT EXISTS "
                f"{for_clause} REQUIRE {prop_ref} IS NOT NULL"
            )

        if spec.get("unique"):
            unique_name = f"{name_prefix}_{prop_slug}_unique"
            statements.append(
                f"CREATE CONSTRAINT {unique_name} IF NOT EXISTS "
                f"{for_clause} REQUIRE {prop_ref} IS UNIQUE"
            )

    return statements


def build_constraint_statements(schema: dict) -> list[str]:
    """파싱된 graph_schema.yaml(dict)에서 CREATE CONSTRAINT 문 목록을 만든다."""
    statements: list[str] = []

    for label, node in schema["nodes"].items():
        statements += _node_key_statements(label, node)
        statements += _property_statements(
            name_prefix=_snake(label),
            properties=node["properties"],
            key_prop=node.get("uniqueKey"),
            for_clause=f"FOR (n:{label})",
            var="n",
        )

    for rel_type, rel in schema["relationships"].items():
        statements += _rel_key_statements(rel_type, rel)
        statements += _property_statements(
            name_prefix=rel_type.lower(),
            properties=rel.get("properties") or {},
            key_prop=rel.get("naturalKey"),
            for_clause=f"FOR ()-[r:{rel_type}]-()",
            var="r",
        )

    return statements


if __name__ == "__main__":
    from pathlib import Path

    import yaml

    schema_path = Path(__file__).resolve().parent.parent / "schema" / "graph_schema.yaml"
    with schema_path.open(encoding="utf-8") as f:
        loaded_schema = yaml.safe_load(f)

    stmts = build_constraint_statements(loaded_schema)
    print(f"{len(stmts)}개 제약조건 문 생성됨\n")
    for stmt in stmts:
        print(stmt)
