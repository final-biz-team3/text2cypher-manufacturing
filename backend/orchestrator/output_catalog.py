"""Schema-backed canonical output aliases for execution planning."""

from dataclasses import dataclass
from typing import Literal, NamedTuple

from agents.cypher.schema.models import GraphSchema
from agents.sql.schema.models import SqlSchema

ToolName = Literal["sql", "graph"]
CalculationType = Literal["aggregate", "derived", "path"]


class DomainAlias(NamedTuple):
    """Non-physical manufacturing output alias."""

    alias: str
    meaning: str
    canonical_source: ToolName
    calculation_type: CalculationType


DOMAIN_ALIAS_REGISTRY: tuple[DomainAlias, ...] = (
    DomainAlias("activeSupplierCount", "활성 공급업체 수", "sql", "aggregate"),
    DomainAlias("purchasedProductCount", "외부 구매 제품 수", "sql", "aggregate"),
    DomainAlias("productCount", "제품 수", "sql", "aggregate"),
    DomainAlias("actualStock", "위치별 재고 합계", "sql", "aggregate"),
    DomainAlias("shortageQty", "안전재고 또는 필요량 대비 부족 수량", "sql", "derived"),
    DomainAlias("totalOrderQty", "판매 주문 수량 합계", "sql", "aggregate"),
    DomainAlias("totalRejectedQty", "구매 반려 수량 합계", "sql", "aggregate"),
    DomainAlias("priceCostGap", "정가와 표준원가의 차이", "sql", "derived"),
    DomainAlias(
        "suppliedProductCount", "공급하는 서로 다른 제품 수", "sql", "aggregate"
    ),
    DomainAlias("averageListPrice", "평균 정가", "sql", "aggregate"),
    DomainAlias("workOrderCount", "서로 다른 작업지시 수", "sql", "aggregate"),
    DomainAlias("totalScrappedQty", "폐기 수량 합계", "sql", "aggregate"),
    DomainAlias("depth", "그래프 경로 깊이", "graph", "path"),
    DomainAlias("minDepth", "노드까지의 최소 경로 깊이", "graph", "path"),
    DomainAlias("minDepthA", "첫 번째 시작점에서의 최소 깊이", "graph", "path"),
    DomainAlias("minDepthB", "두 번째 시작점에서의 최소 깊이", "graph", "path"),
    DomainAlias("pathProductIds", "경로 순서의 제품 ID 배열", "graph", "path"),
    DomainAlias("pathProductNames", "경로 순서의 제품명 배열", "graph", "path"),
    DomainAlias("sharedComponentCount", "공통 공급 부품 종류 수", "graph", "aggregate"),
)


@dataclass(frozen=True)
class AliasSpec:
    alias: str
    meaning: str
    canonical_source: ToolName
    calculation_type: str
    schema_paths: tuple[str, ...]
    search_terms: tuple[str, ...]
    owner_terms: tuple[str, ...]
    nullable: bool


@dataclass(frozen=True)
class OutputCatalog:
    """Allowed aliases and identity aliases derived from loaded schemas."""

    by_tool: dict[ToolName, dict[str, AliasSpec]]
    identity_aliases_by_tool: dict[ToolName, frozenset[str]]
    shared_join_aliases: frozenset[str]

    def allowed_aliases(self, tool: str) -> tuple[str, ...]:
        if tool == "sql":
            source: ToolName = "sql"
        elif tool == "graph":
            source = "graph"
        else:
            raise ValueError(f"지원하지 않는 output source입니다: {tool!r}")
        return tuple(sorted(self.by_tool[source]))

    def describe(self, tool: str) -> str:
        """Keep prompt material compact while retaining source semantics."""
        if tool == "sql":
            source: ToolName = "sql"
        elif tool == "graph":
            source = "graph"
        else:
            raise ValueError(f"지원하지 않는 output source입니다: {tool!r}")
        specs = self.by_tool[source]
        return "\n".join(
            f"- {alias}: {spec.meaning} ({spec.calculation_type})"
            for alias, spec in sorted(specs.items())
        )


def _add_schema_alias(
    target: dict[str, AliasSpec],
    *,
    alias: str,
    tool: ToolName,
    schema_path: str,
    search_terms: tuple[str, ...],
    owner_terms: tuple[str, ...],
    nullable: bool,
) -> None:
    existing = target.get(alias)
    if existing is None:
        target[alias] = AliasSpec(
            alias=alias,
            meaning=f"{schema_path}에서 유도한 필드",
            canonical_source=tool,
            calculation_type="physical",
            schema_paths=(schema_path,),
            search_terms=search_terms,
            owner_terms=owner_terms,
            nullable=nullable,
        )
        return
    target[alias] = AliasSpec(
        alias=existing.alias,
        meaning=existing.meaning,
        canonical_source=existing.canonical_source,
        calculation_type=existing.calculation_type,
        schema_paths=(*existing.schema_paths, schema_path),
        search_terms=tuple(dict.fromkeys((*existing.search_terms, *search_terms))),
        owner_terms=tuple(dict.fromkeys((*existing.owner_terms, *owner_terms))),
        nullable=existing.nullable or nullable,
    )


def build_output_catalog(
    sql_schema: SqlSchema,
    graph_schema: GraphSchema,
) -> OutputCatalog:
    sql: dict[str, AliasSpec] = {}
    graph: dict[str, AliasSpec] = {}
    identity_aliases_by_tool: dict[ToolName, set[str]] = {
        "sql": set(),
        "graph": set(),
    }

    for table_name, table in sql_schema.tables.items():
        owner_terms = (table_name, table_name.rsplit(".", 1)[-1], *table.aliases)
        for column_name, aliases in table.output_aliases.items():
            column = table.columns[column_name]
            for alias in aliases:
                _add_schema_alias(
                    sql,
                    alias=alias,
                    tool="sql",
                    schema_path=f"{table_name}.{column_name}",
                    search_terms=(alias, column_name, *column.aliases),
                    owner_terms=owner_terms,
                    nullable=column.nullable,
                )
                if column.primary_key:
                    identity_aliases_by_tool["sql"].add(alias)

    for node_name, node in graph_schema.nodes.items():
        owner_terms = (node_name, *node.aliases)
        for property_name, aliases in node.output_aliases.items():
            for alias in aliases:
                _add_schema_alias(
                    graph,
                    alias=alias,
                    tool="graph",
                    schema_path=f"{node_name}.{property_name}",
                    search_terms=(
                        alias,
                        property_name,
                        *node.properties[property_name].aliases,
                    ),
                    owner_terms=owner_terms,
                    nullable=not node.properties[property_name].required,
                )
                if property_name == node.unique_key:
                    identity_aliases_by_tool["graph"].add(alias)

    for relationship_name, relationship in graph_schema.relationships.items():
        owner_terms = (relationship_name, *relationship.aliases)
        for property_name, aliases in relationship.output_aliases.items():
            for alias in aliases:
                _add_schema_alias(
                    graph,
                    alias=alias,
                    tool="graph",
                    schema_path=f"{relationship_name}.{property_name}",
                    search_terms=(
                        alias,
                        property_name,
                        *relationship.properties[property_name].aliases,
                    ),
                    owner_terms=owner_terms,
                    nullable=not relationship.properties[property_name].required,
                )

    for item in DOMAIN_ALIAS_REGISTRY:
        target = sql if item.canonical_source == "sql" else graph
        if item.alias in target:
            raise ValueError(
                f"domain alias {item.alias!r}가 physical schema alias와 충돌합니다."
            )
        target[item.alias] = AliasSpec(
            alias=item.alias,
            meaning=item.meaning,
            canonical_source=item.canonical_source,
            calculation_type=item.calculation_type,
            schema_paths=(),
            search_terms=(item.alias, item.meaning),
            owner_terms=(),
            nullable=False,
        )

    if any(not aliases for aliases in identity_aliases_by_tool.values()):
        raise ValueError(
            "SQL/Graph schema에서 source별 identity alias를 유도하지 못했습니다."
        )
    shared_join_aliases = (
        identity_aliases_by_tool["sql"] & identity_aliases_by_tool["graph"]
    )
    if not shared_join_aliases:
        raise ValueError("SQL과 Graph가 공유하는 join identity alias가 없습니다.")
    return OutputCatalog(
        by_tool={"sql": sql, "graph": graph},
        identity_aliases_by_tool={
            tool: frozenset(aliases)
            for tool, aliases in identity_aliases_by_tool.items()
        },
        shared_join_aliases=frozenset(shared_join_aliases),
    )


def binding_name(identity_alias: str) -> str:
    """Convert ``productId`` to the only allowed plural binding name."""
    if not identity_alias.endswith("Id"):
        raise ValueError(f"identity alias 형식이 잘못됐습니다: {identity_alias!r}")
    return f"{identity_alias[:-2]}Ids"
