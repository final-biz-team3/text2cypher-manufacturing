# 정형 MVP PostgreSQL → Neo4j 매핑

## 목적

이 문서는 적재 담당자가 PostgreSQL 원본에서 Neo4j 정형 그래프를 구현할 때 사용하는 컬럼 단위 계약이다. PostgreSQL은 원본 기준 저장소이고 Neo4j는 관계 탐색용 read model이다.

## 노드 매핑

| 순서 | Neo4j 노드 | PostgreSQL 원본 | Neo4j 고유키 | 적재 속성 |
|---:|---|---|---|---|
| 1 | `Product` | `production.product` | `productId ← productid` | `name ← name`, `sellableFinishedGood ← finishedgoodsflag`, `sourceModifiedAt ← modifieddate` |
| 2 | `Supplier` | `purchasing.vendor` | `supplierId ← businessentityid` | `name ← name`, `active ← activeflag`, `sourceModifiedAt ← modifieddate` |
| 3 | `WorkOrder` | `production.workorder` | `workOrderId ← workorderid` | `sourceModifiedAt ← modifieddate` |
| 4 | `RoutingOperation` | `production.workorderrouting` | `routingOperationKey` 합성 | `sequence ← operationsequence`, `sourceModifiedAt ← modifieddate` |
| 5 | `Location` | `production.location` | `locationId ← locationid` | `name ← name`, `sourceModifiedAt ← modifieddate` |
| 6 | `ScrapReason` | `production.scrapreason` | `scrapReasonId ← scrapreasonid` | `name ← name`, `sourceModifiedAt ← modifieddate` |

### RoutingOperation 합성키

```text
routingOperationKey
= workorderid + "-" + productid + "-" + operationsequence
```

현재 고정 스냅샷에서는 유일하지만, 운영 DB에서 공정 순서가 변경될 수 있으면 안정적인 원본 키를 별도로 마련해야 한다.

## 관계 매핑

### SUPPLIES

```text
(Supplier)-[:SUPPLIES]->(Product)
```

| 항목 | 값 |
|---|---|
| 원본 | `purchasing.productvendor` |
| Supplier 연결 | `productvendor.businessentityid = vendor.businessentityid` |
| Product 연결 | `productvendor.productid = product.productid` |
| 조건 | `vendor.activeflag = true` |
| 관계 키 | `supplierId-productId` |

비활성 공급업체 노드는 유지하지만 비활성 공급 관계는 생성하지 않는다. 따라서 공급중단 질문은 현재 활성 공급업체의 가상 중단만 지원한다.

### REQUIRES_COMPONENT

```text
(assembly:Product)-[:REQUIRES_COMPONENT]->(component:Product)
```

| Neo4j | PostgreSQL |
|---|---|
| 시작 Product | `productassemblyid` |
| 도착 Product | `componentid` |
| `bomId` | `billofmaterialsid` |
| `quantityPerAssembly` | `perassemblyqty` |
| `startDate` | `startdate::date` |
| `endDate` | `enddate::date`, NULL 허용 |

BOM 행은 종료 여부와 관계없이 모두 적재한다. 조회할 때 경로의 모든 관계에 다음 조건을 적용한다.

```text
startDate <= bomAsOfDate
AND (endDate IS NULL OR bomAsOfDate < endDate)
```

### PRODUCES

```text
(WorkOrder)-[:PRODUCES]->(Product)
```

- 원본: `production.workorder`
- 연결: `workorder.productid = product.productid`

### HAS_OPERATION

```text
(WorkOrder)-[:HAS_OPERATION]->(RoutingOperation)
```

- 원본: `production.workorderrouting`
- 연결: `workorderrouting.workorderid = workorder.workorderid`

### PERFORMED_AT

```text
(RoutingOperation)-[:PERFORMED_AT]->(Location)
```

- 원본: `production.workorderrouting`
- 연결: `workorderrouting.locationid = location.locationid`

### SCRAPPED_DUE_TO

```text
(WorkOrder)-[:SCRAPPED_DUE_TO]->(ScrapReason)
```

생성 조건:

```text
workorder.scrappedqty > 0
AND workorder.scrapreasonid IS NOT NULL
```

폐기 수량은 Neo4j 관계 속성에 저장하지 않는다. Q20의 폐기 수량 정답은 PostgreSQL에서 조회한다.

## 속성을 최소화한 이유

| 제외한 값 | 기준 저장소 | 이유 |
|---|---|---|
| 현재 재고 | PostgreSQL | 자주 변경되고 집계가 필요함 |
| 가격·원가 | PostgreSQL | 그래프 경로 탐색에 필요하지 않음 |
| 판매·구매 수량 | PostgreSQL | 집계 중심 데이터 |
| 폐기 수량 | PostgreSQL | Q20의 정답 기준을 하나로 유지 |
| 실제 공정 시간 | PostgreSQL | 현재 Q15·Q20은 공정 순서와 작업장만 요구 |

## 온톨로지 적재 경계

`Term`, `BusinessConcept`, `QuestionIntent`, `EntityType`, `QueryTemplate` 구조와 인덱스는 설계에 포함한다. 하지만 실제 seed 값과 Gold query registry는 아직 확정되지 않았으므로 업무 데이터 적재와 분리한다.

```text
1차 적재: 업무 노드 6종 + 업무 관계 6종
2차 적재: 온톨로지 seed + QueryTemplate registry
```

