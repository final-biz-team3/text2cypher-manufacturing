# 전체 현황 대시보드·노드 상세 실행 계획

시각 구현 기준은 [전체 현황 대시보드 디자인 기준](../design/overview-dashboard-design-spec.md)을 따른다.

## 1. 목표와 제품 경계

이 프로젝트의 핵심 기능은 자연어 Chat을 통한 제조 데이터 조회다. 대시보드는 Chat을
대체하지 않고, 로그인 직후 전체 데이터 상태와 질문 가능한 업무 영역을 보여 주는
진입점으로 사용한다.

- `/dashboard`: 고정 SQL·Cypher 기반 전체 현황과 상세 탐색
- `/chat`: 기존 LLM 질문·답변, 재시도, 결과표, 대화 이력
- `/`: `/dashboard`로 이동
- 로그인 성공 후 `/dashboard`로 이동

대시보드는 `/chat` 오케스트레이터를 호출하거나 대화 이력을 생성하지 않는다. Chat의
요청·응답 계약, 프롬프트, 라우팅, 쿼리 실행, 자기수정, 답변 생성은 변경하지 않는다.
라우팅, 공통 메뉴, 질문 초안 전달, Sigma 노드 상세만 연결부 변경으로 허용한다.

## 2. 브랜치 전략

1. `feat/sigma-graph-visualization`의 Sigma 구현을 독립 커밋으로 정리한다.
2. 최신 `origin/dev` 위로 rebase하고 canonical output alias, BOM shortage, history 결과와
   Sigma 변환기의 호환성을 검증한다.
3. 안정화된 Sigma 커밋에서 `feat/overview-dashboard`를 만든다.
4. Sigma PR을 먼저 `dev`에 반영한 뒤 대시보드 브랜치를 최신 `dev` 위로 다시 정리한다.
5. 임시 pytest·pre-commit 캐시와 사용자 작업 파일은 커밋하거나 삭제하지 않는다.

## 3. 화면 구조

공통 상단 바에는 `전체 현황`, `AI Chat`, 데이터 스냅샷, PostgreSQL·Neo4j 상태,
사용자 정보를 표시한다.

대시보드는 다음 순서로 구성한다.

1. 제목, 스냅샷 안내, `AI Chat으로 이동` 버튼
2. 핵심 KPI 6개
3. 2열 분석 카드
4. 카드별 `전체 보기`, `AI에게 질문`
5. 우측 480px 상세 패널에서 목록과 엔티티 상세 표시

전체 누계 영역과 별도로 기간을 선택하는 `공정 현황` 섹션을 둔다. 이 필터는
작업지시·공정·폐기 실적만 변경하며 제품, 공급업체, 현재 재고에는 적용하지 않는다.
과거 재고 스냅샷이 없는 상태에서 전역 날짜 필터처럼 보이지 않도록 공정 섹션 안에
배치한다.

KPI 그리드는 XL 6열, LG 3열, SM 2열, 모바일 1열이다. 좁은 화면에서 상세 패널은
전체 폭 화면으로 전환한다.

## 4. 데이터 계약

모든 계산은 `queries/dashboard/contracts.json`에 SQL, NULL 처리, 정렬, 동률 규칙과
함께 등록한다. 현재 검증된 PostgreSQL 스키마와 평가 계약만 사용한다. 확인되지 않은
작업 일정, 리드타임, 신용등급, 입고 수량, 판매이익은 1차 범위에서 제외한다.

### KPI

| Key | 정의 | 단위 |
|---|---|---|
| `product_count` | `production.product` 전체 행 수 | 개 |
| `active_supplier_count` | `purchasing.vendor.activeflag=true` | 곳 |
| `purchased_product_count` | `production.product.makeflag=false` | 개 |
| `low_stock_product_count` | 제품별 `COALESCE(SUM(inventory.quantity),0) < safetystocklevel` | 개 |
| `work_order_count` | `production.workorder` 전체 행 수 | 건 |
| `scrapped_work_order_count` | `production.workorder.scrappedqty>0` | 건 |

재고 행이 없는 제품도 실제재고 0으로 포함한다.

### 분석 카드

| Key | 의미 | 결정적 정렬 |
|---|---|---|
| `low_stock_top5` | 안전재고 미달 제품과 부족량 | 부족량 DESC, productId ASC |
| `top_finished_sales` | 완제품별 판매 주문수량 합계 | 판매량 DESC, productId ASC |
| `top_rejected_suppliers` | 공급업체별 구매 반려수량 합계 | 반려수량 DESC, supplierId ASC |
| `top_scrapped_work_orders` | 폐기 작업지시, 제품, 폐기사유 | 폐기수량 DESC, workOrderId ASC |
| `busiest_locations` | 작업장별 서로 다른 작업지시 수 | 작업지시 수 DESC, locationId ASC |
| `category_price_summary` | 분류별 평균 정가와 제품 수 | 평균 정가 DESC, categoryId ASC |
| `top_suppliers_by_product_count` | 활성 공급업체별 공급 제품 수 | 제품 수 DESC, supplierId ASC |

현재 스키마에는 received quantity가 없으므로 `top_rejected_suppliers`를 반려율로
표현하지 않는다. 데이터는 스냅샷이며 `실시간`, `오늘`, `현재 생산 중`, `지연`이라는
표현을 사용하지 않는다.

## 5. 읽기 API

### `GET /dashboard/overview`

KPI와 카드별 상위 5개를 반환한다. 카드 쿼리는 병렬 실행하고 부분 실패를 허용한다.

```json
{
  "snapshot": {"syncRunId": "...", "label": "AdventureWorks 데이터 스냅샷"},
  "kpis": [{"key": "product_count", "label": "전체 제품", "value": 504, "unit": "개", "status": "ready"}],
  "cards": [{"key": "low_stock_top5", "title": "안전재고 미달 제품", "kind": "table", "status": "ready", "columns": [], "rows": [], "total": 0}],
  "errors": []
}
```

### `GET /dashboard/cards/{cardKey}`

- `page` 기본 1
- `pageSize` 기본 20, 최대 100
- 카드별 허용 `sort`, `direction`만 사용
- `columns`, `rows`, `page`, `pageSize`, `total` 반환

### `GET /dashboard/process-overview`

- `from`, `to`: `YYYY-MM-DD`, 둘을 함께 입력
- `granularity`: `day`, `month`, `year` 중 하나이며 생략 시 기간에 따라 자동 선택
- 미입력 시 실제 데이터 최신일을 포함한 최근 30일
- 실제 데이터 범위를 벗어나거나 시작일이 종료일보다 늦으면 400
- 화면에서 일별·월별·연도별 집계 기준을 선택할 수 있음
- 그래프의 집계 버튼은 조회 기간에 맞춰 조정함
  - 31일 이하: 일
  - 32~93일: 일·월
  - 94일~730일: 월
  - 731일 이상: 월·년
- 기간 변경 후 기존 집계 기준이 지원되지 않으면 첫 번째 지원 기준으로 자동 전환
- 집계 기준 미입력 시 93일 이하 기간은 일별, 그보다 긴 기간은 월별 추이로 자동 집계
- 작업지시 시작·완료, 수행 공정, 폐기 발생 작업지시, 폐기수량 KPI 반환
- 작업지시 시작·완료 및 폐기 추이와 작업장별 수행 공정 상위 7곳 반환

작업지시 시작은 `production.workorder.startdate`, 완료와 폐기 귀속은 `enddate`,
공정 수행은 `production.workorderrouting.actualstartdate`를 기준으로 한다. 전체 선택
가능 범위는 원본 데이터에서 계산하며 현재 검증 범위는 `2011-06-03`부터
`2014-06-28`까지다. `2014-08-08`은 공정 필터가 아니라 BOM 관계 유효 기준일이다.

### `GET /entities/{entityType}/{entityId}`

지원 유형은 `product`, `supplier`, `work-order`, `routing-operation`, `location`,
`scrap-reason`이다. 응답은 엔티티 식별정보, 제목별 필드 그룹, Chat 질문 액션을
반환한다.

### `GET /entities/{entityType}/{entityId}/neighbors`

- `depth=1`만 허용
- 최대 100개 노드
- `nodes`, `edges`, `truncated` 반환
- 엔티티별 고정 Cypher만 사용

잘못된 입력은 400, 비로그인은 401, 엔티티 없음은 404, DB 장애는 503으로 반환한다.
오류 응답은 `code`, 사용자용 `message`만 포함하고 내부 쿼리나 접속정보를 노출하지
않는다.

## 6. 엔티티 상세

- 제품: 번호, 분류, 제조 여부, 완제품 여부, 색상, 크기, 판매 종료일, 가격, 원가,
  안전·실제재고, 재고 위치, 공급업체, 유효 1단계 BOM
- 공급업체: 활성 여부, 공급 제품 수·목록, 반려수량 합계
- 작업지시: 제품, 폐기수량, 폐기사유, 공정 수와 작업장 순서
- 공정: 합성키, 작업지시, 제품, 공정 순서, 작업장
- 작업장: 이름, 작업지시·공정 수, 보관 제품과 재고
- 폐기사유: 이름, 작업지시 수, 폐기수량 합계, 상위 작업지시

핵심 값이 없으면 `미등록`, 부가 값은 숨김 처리한다. 통화, 수량, 날짜, Boolean은
공통 포맷터로 표시한다.

## 7. Dashboard와 Chat 연결

대시보드의 `AI에게 질문`은 React Router state로 `draftQuestion`을 `/chat`에 전달한다.
Chat은 입력창만 채우며 자동 실행하지 않는다.

Sigma 노드 선택 시 그래프 응답의 `properties`를 즉시 표시하고 엔티티 ID가 있으면
상세 API로 보강한다. API가 실패해도 기존 속성은 유지한다. 선택을 해제하면 패널을
닫고 포커스를 원래 노드 또는 실행 버튼으로 복귀시킨다.

## 8. 성능과 실패 격리

- overview 카드 쿼리 병렬 실행
- 카드별 제한시간 3초, 전체 제한시간 5초
- 서버 메모리 캐시 TTL 5분
- 로컬 warm 상태 주요 콘텐츠 목표 2초 이내
- 프론트 AbortController로 오래된 요청 취소
- 공정 기간별 응답도 5분 캐시하며 최근 128개 기간만 유지
- KPI와 카드별 스켈레톤·오류·재시도 상태 제공
- 카드 하나의 실패가 다른 카드나 Chat을 중단시키지 않음

## 9. 구현 순서

1. Dashboard 계약과 고정 쿼리
2. overview·card API와 테스트
3. `/dashboard`, `/chat` 라우팅과 공통 메뉴
4. KPI·분석 카드·상세 목록
5. 주요 6종 엔티티 API
6. Sigma 노드 상세 패널
7. Dashboard → Chat 질문 초안 전달
8. 접근성, 실패 상태, 성능, 시각 QA

## 10. 2차 범위

- 직책별 대시보드
- 사용자 개인화
- 역할별 AI 요약
- 운영 기준일 자동화와 실시간 갱신
- 추가 KPI를 위한 SQL·Neo4j 스키마 확장
