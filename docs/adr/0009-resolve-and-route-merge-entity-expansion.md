# 0009. resolve_entity·route_query 통합 + 엔티티 타입 확장(product/supplier/category)

## 상태

부분 대체됨 (2026-08-24) — [docs/superpowers/plans/2026-08-24-entity-resolution-generalization.md](../superpowers/plans/2026-08-24-entity-resolution-generalization.md) 참고

> 이 ADR의 결정 중 다음 두 가지는 구현 단계에서 뒤집혔다:
> 1. **노드 병합 → 분리 유지**: `resolve_entity`와 `route_query`를 합치지 않고 그대로 분리했다. 이유는 두 노드가 서로 다른 실패 모드(엔티티 확정 vs 순수 분류)를 가지고 있어서, 이후 self-correction이 "무엇 때문에 재시도하는지"를 노드 단위로 구분할 수 있어야 했기 때문이다 — 합치면 이 구분이 흐려진다.
> 2. **엔티티 타입 3종(product/supplier/category) → 스키마 기반 동적 목록(product/supplier/location/scrapReason)**: `category`(`production.productcategory`)는 두 스키마 파일(`schema/sql_schema.yaml`, `schema/graph_schema.yaml`) 어디에도 정의돼 있지 않아 이번 범위에서 제외했다. 대신 엔티티 타입을 하드코딩하지 않고 `graph_schema.yaml`의 이름 있는 노드에서 동적으로 도출하는 방식을 택했는데, 그 결과 `category`는 Neo4j 그래프 read model에 없는 노드라 **구조적으로 이 방식으로는 지원할 수 없다** — RQ07(분류명)을 지원하려면 엔티티 타입 소스를 그래프 스키마가 아닌 SQL 스키마(또는 별도 매핑)로 바꿔야 한다.
>
> 아래 원본 결정 내용은 기록으로 남기고 수정하지 않는다.

원래 확정일: 2026-08-21

## 한 줄 요약

> `resolve_entity`와 `route_query`(ADR 0008)는 둘 다 원본 질의 텍스트를 입력으로 받는데, LLM 호출을 두 번 나눠서 하고 있었다. 이번 결정으로 두 노드를 `route_query` 하나로 합쳐(이름은 기존 `route_query`를 그대로 쓴다) 구조화 출력(structured output) 한 번으로 처리하고, 동시에 엔티티 타입을 제품(product)뿐 아니라 공급업체(supplier)·분류(category)까지 일반화한다. RQ07(분류명, SQL)·RQ14(공급업체명, GRAPH)로 검증한다.

---

## 배경 — 왜 이 결정이 필요했나

ADR 0008에서 `resolve_entity`(제품명 추출 + DB 확정) → `route_query`(SQL/GRAPH 분류) 2노드 구조를 만들었다. 이후 실무 관점에서 다시 보니 두 가지가 눈에 띄었다.

1. **LLM 호출 중복**: 두 노드 다 원본 질의 텍스트를 입력으로 쓰는데, `route_query`의 SQL/GRAPH 판단은 few-shot 예시를 보면 질의 문장 자체("수치 조회냐 관계 탐색이냐")로 결정되지 확정된 entity ID가 결정적 역할을 하지 않는다. 즉 "질의 이해"라는 한 가지 작업을 API 왕복 두 번으로 나눠서 하고 있었다 — 지연시간·비용 모두 손해다. "의도분류+엔티티추출 동시 처리"는 프로덕션에서 흔한 최적화 패턴이다.
2. **엔티티 타입이 product로 고정됨**: `resolve_entity`는 `extract_product_name` 도구 하나로 제품명만 뽑는다. 그런데 `queries/query_contracts.json`의 RQ07(분류명), RQ14/RQ18(공급업체명) 같은 질의는 애초에 다른 종류의 이름을 엔티티로 받아야 한다. 지금 구조로 이걸 나중에 추가하려면 `resolve_entity`·`route_query` 둘 다 다시 손대야 한다.

두 문제가 같은 지점(노드 경계와 스키마)에서 만나므로, 합치는 작업을 엔티티 타입 확장의 준비 작업으로 삼아 한 번에 처리하기로 했다.

### 대상 질의 확장

ADR 0008의 5개(RQ01~04, RQ12)에 다음 2개를 추가해 회귀 없이 3가지 엔티티 타입 모두 검증한다.

| ID | 라우트 | 질문 템플릿 | 엔티티 타입 | fixture |
|---|---|---|---|---|
| RQ07 | SQL | `[제품분류명]`에 포함된 제품 수를 알려줘. | category | categoryId 2, "Components" |
| RQ14 | GRAPH | 공급업체 `[업체명]`가 공급하는 부품과 그 부품을 사용하는 완제품을 알려줘. | supplier | supplierId 1494, "Allenson Cycles" |

RQ01~04(product, SQL)·RQ12(product, GRAPH)는 회귀 테스트로 그대로 유지한다 — 총 7개 라이브 질의로 "product/supplier/category × SQL/GRAPH" 조합을 전부 커버한다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. 두 노드를 `route_query` 하나로 통합

`resolve_entity.py`를 제거하고, 그 역할(엔티티 확정)을 기존 `backend/orchestrator/nodes/route_query.py`의 `route_query` 노드에 흡수시킨다 — 새 노드 이름을 만들지 않고 기존 `route_query`라는 이름을 그대로 유지한다. OpenAI Structured Outputs(`response_format={"type": "json_schema", ...}`)로 한 번의 호출에서 엔티티와 라우팅을 동시에 받는다.

```json
{
  "entityType": "product" | "supplier" | "category" | null,
  "entityName": "string" | null,
  "toolPlan": ["sql"] | ["graph"] | ["sql", "graph"]
}
```

- `entityType`/`entityName`이 둘 다 `null`이면 질의가 특정 엔티티를 가리키지 않는 것(RQ03, RQ04와 동일 케이스).
- 함수 호출(Function Calling) 대신 Structured Outputs를 쓰는 이유: 지금까지는 "추출"에 Function Calling, "분류"에 일반 텍스트 응답을 썼는데, 하나로 합치면 두 값을 한 JSON에 담아야 해서 Structured Outputs(스키마 강제, 파싱 실패 위험 없음)가 더 적합하다.

### 2. 엔티티 타입별 DB 조회 일반화

`entityType`에 따라 조회할 테이블·컬럼·응답 키가 다르므로, 타입별 조회 함수를 분리하고 디스패치한다.

| entityType | 테이블 | ID 컬럼 | 이름 컬럼 | 응답 키 |
|---|---|---|---|---|
| product | `production.product` | `productid` | `name` | `productId`, `productName` |
| supplier | `purchasing.vendor` | `businessentityid` | `name` | `supplierId`, `supplierName` |
| category | `production.productcategory` | `productcategoryid` | `name` | `categoryId`, `categoryName` |

모두 정확 일치(EXACT) 조회다. 라우트가 GRAPH(RQ14)여도 엔티티 확정은 항상 PostgreSQL에서 한다 — ADR 0008에서 이미 RQ12(GRAPH, product)로 확립한 원칙("PostgreSQL이 사실의 기준 저장소")을 그대로 따른다.

### 3. `entity` 응답 형태

`OrchestratorState.entity`는 타입별로 다른 키를 담은 dict를 그대로 쓴다(예: `{"supplierId": 1494, "supplierName": "Allenson Cycles"}`) — `queries/query_contracts.json`의 `requiredAnswerFields` 명명과 맞춰, 다음 세션의 SQL/Cypher Agent가 그대로 참조할 수 있게 한다. `entityType`을 dict 안에 별도로 넣지는 않는다 — 어떤 타입인지는 dict의 키 이름(`productId` vs `supplierId` vs `categoryId`) 자체로 구분되고, `state["entity"]`를 소비하는 다음 세션 코드는 dict 원본을 그대로 SQL/Cypher 파라미터로 넘기면 되기 때문이다.

### 4. 에러 처리

`EntityNotFoundError`는 3개 타입 전부에 그대로 적용한다(타입 무관하게 "못 찾음"은 동일하게 처리). 유사 매칭·재질문(`EntityAmbiguousError`)은 ADR 0008과 마찬가지로 이번 범위에서 제외한다.

## 검토했으나 채택하지 않은 대안

**합치지 않고 엔티티 타입만 확장.** `resolve_entity`에 `extract_supplier_name`, `extract_category_name` 도구를 추가하고 `route_query`는 그대로 두는 안. 변경 범위는 작지만, `route_query`가 여전히 별도 API 왕복을 쓰는 비효율은 그대로 남고, 나중에 통합을 다시 하려면 두 노드를 또 손대야 한다. 이미 통합의 필요성을 확인한 상태에서 미루는 이유가 없어 기각했다.

**Function Calling으로 3개 도구(extract_product_name/supplier_name/category_name)를 두고 LLM이 그중 하나를 호출.** 현재 `resolve_entity`의 방식을 그대로 확장하는 안이다. 문제는 `toolPlan`을 이 방식으로 같이 받아올 수 없다는 점이다 — Function Calling은 "엔티티 이름 하나"를 위한 도구이지 별도 필드(`toolPlan`)를 함께 반환하는 데는 안 맞는다. 결국 도구 호출 결과와 별개로 `toolPlan`을 받을 두 번째 호출이 다시 필요해져서, 애초에 합치려는 목적(왕복 1번 감소)이 무산된다. Structured Outputs로 두 값을 한 JSON에 담는 게 목적에 맞다.

**엔티티 타입을 dict에 명시적으로 포함** (`{"entityType": "supplier", "id": 1494, "name": "Allenson Cycles"}`처럼 통일된 키). 타입별로 다른 키(`productId`/`supplierId`/`categoryId`)를 쓰는 대신 하나의 통일된 스키마를 쓰는 안이다. 더 일관돼 보이지만, `query_contracts.json`의 `requiredAnswerFields`가 이미 타입별로 다른 키 이름(`supplierId`, `categoryId` 등)을 쓰고 있어서, 통일된 키로 가면 다음 세션에서 SQL/Cypher Agent가 다시 타입별 이름으로 변환하는 계층이 하나 더 생긴다. 계약 파일의 명명을 그대로 따르는 게 마찰이 적어 기각했다.

## 결과 및 트레이드오프

- 질의당 LLM 호출이 1회 줄어든다(엔티티 확정+라우팅 = 1번 호출). 다음 세션에서 SQL/Cypher Agent(최대 2회 재시도)·generate_answer가 붙으면 전체 호출 수가 최대 6~7회에서 5~6회로 줄어든다.
- `resolve_entity`/`route_query`를 각각 독립적으로 테스트하던 기존 유닛 테스트(9개)는 통합된 `route_query` 테스트로 재작성해야 한다 — 테스트 파일 수는 줄지만 개별 테스트가 검증하는 범위(엔티티 종류 3개 × 있음/없음/타입아님)는 늘어난다.
- 엔티티 타입이 늘어날수록(예: 작업지시 ID) `route_query`의 스키마와 조회 디스패치에 케이스가 추가되지만, 이미 일반화된 구조라 추가 비용이 작다.

## 확실하지 않은 부분

- Structured Outputs의 `entityType` enum이 실제 질의 3종(product/supplier/category) 외의 입력(예: 작업지시 ID, RQ15/RQ20)에서 어떻게 동작하는지는 검증 전이다 — `entityType: null`로 통과시키는 게 맞는지, 다음 세션에서 재확인 필요.
- `toolPlan`이 `["sql", "graph"]`(HYBRID)로 나오는 실제 사례는 이번에도 검증 안 됨(RQ07/RQ14 둘 다 단일 라우트) — ADR 0008과 동일한 한계가 이어진다.
