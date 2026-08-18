# 제조 지식그래프 대시보드 — UI 틀(shell) 설계

## 배경

백엔드(FastAPI + Neo4j 파이프라인)가 아직 완성되지 않은 상태에서, 프론트엔드 UI 골격을 먼저 만들어 둔다. 디자인 근거는 두 가지를 종합했다:

1. 사용자가 제공한 PDF 목업(`Graph_Screens.pdf`) — 질문→Cypher 생성→그래프/표 결과를 보여주는 3개 스크린샷
2. 사용자가 제공한 디자인 핸드오프 zip(`design_handoff_manufacturing_kg_dashboard/README.md`) — 컬러 토큰, 컴포넌트 목록, Zustand 스토어 설계, zod 스키마, 화면별 레이아웃이 상세히 정의된 hi-fi 스펙 문서. 두 소스가 겹치는 부분은 후자를 최종 기준으로 삼는다.

두 소스 사이의 충돌 하나는 사용자가 명시적으로 해소했다: 디자인 핸드오프 문서의 "User Mode / 개발자 모드" 화면 전환은 채택하지 않는다. 챗봇형 서비스이므로 화면은 하나이며, 질문 결과에 따라 내용만 달라진다. `User Mode Screen.dc.html`이 제안한 컴포넌트(`QuestionHistorySidebar`, `DataProvenancePanel`) 중 유용한 부분(스키마/이력 탭 전환, 근거 패널)만 흡수한다.

## 목표

- 실제 API 연동 없이, 이후 백엔드가 붙었을 때 데이터만 주입하면 되는 순수 프레젠테이션 컴포넌트 골격을 만든다.
- 디자인 핸드오프 문서가 지정한 스택 중 **지금 실제로 쓰이는 것만** 세팅한다: React+Vite+TS, Zustand, Tailwind+shadcn/ui.
- 실제 그래프 시각화, 실제 API 호출, 에러/평가 대시보드 화면은 이번 범위에서 제외한다.

## 스코프 트리밍 원칙

설치·설정만 해두고 실제로 쓰는 코드가 없는 배관(plumbing)은 만들지 않는다. 백엔드 계약이 없는 지금 시점에 Axios·React Query·zod·react-force-graph-2d·테스트 인프라를 미리 세팅해도 검증할 동작이 없다 — 실제로 쓸 시점에 필요한 패키지를 그때 추가하는 편이 낫다(YAGNI). 이 원칙에 따라 최초 설계에서 아래를 뺐다:

- **Axios / React Query** — 호출 코드가 없는 상태로 설치만 해두면 의존성 관리 비용만 늘어난다. API contract가 확정되는 시점에 추가한다.
- **react-force-graph-2d** — 그래프 자리는 이번 범위에서 placeholder 박스이므로 설치할 이유가 없다. 실제 그래프를 붙이는 시점에 설치한다.
- **Vitest + RTL** — 정적 뼈대가 크래시 없이 마운트되는지만 확인하는 테스트는 설정 비용 대비 검증 가치가 낮다. 실제 로직(상태 전환, 데이터 가공)이 생기는 시점에 테스트 인프라를 넣는다.
- **`MetricCard` 컴포넌트** — 이번 idle/success 어느 화면에도 쓰이지 않는다. 집계형 응답을 실제로 다루는 시점에 추가한다.
- **zod → 순수 TS interface** — zod는 타입 선언과 런타임 파싱 두 가지를 하지만, TS interface는 타입 선언만 한다(컴파일 타임에만 존재). 런타임 파싱이 필요한 이유는 신뢰할 수 없는 외부 데이터가 들어올 때뿐인데, 지금은 mock 데이터를 직접 만들어 쓰므로 타입이 항상 보장된다. zod가 실제로 필요해지는 시점은 FastAPI 응답을 실제로 받는 순간이다 — 그때 `types/query.ts`의 interface를 zod schema로 옮기면 된다(구조가 동일해서 변환 비용이 낮다). 즉 지금 interface로 시작하는 것이 나중 zod 도입을 막지 않는, 순서가 맞는 선택이다.
- **Pretendard npm self-host** — 서브셋팅/번들 설정까지는 이번 범위에서 불필요. CDN 링크로 대체하고, 프로덕션 폴리싱 단계에서 self-host로 전환한다.

## 화면 구조

단일 화면(`Dashboard`)이 내부 상태로 두 뷰를 전환한다. 상태는 Zustand의 `activeScreen: 'idle' | 'success'`로 관리한다 (`'loading' | 'error'`는 타입에는 남겨두되 이번 범위의 UI는 만들지 않는다).

- **idle** (질문 전): 중앙 정렬된 안내 문구("공정 데이터에 대해 무엇이든 물어보세요") + `QueryInputBar`. 세션 `history`가 비어있으면(첫 방문) 그 아래에 예시 질문 카드 2개(`ExampleQuestionCard`, 2열 그리드)로 온보딩하고, `history`가 하나라도 있으면(재방문) 예시 카드를 숨긴다 — empty-state는 "가르치는 순간"이라는 NN/g 가이드라인과 예시/이력을 분리하는 ChatGPT·Gemini류 패턴을 따름.
- **success** (질문 후): `QueryInputBar`(상단 고정) + `NaturalLanguageAnswerBox` + `PathGraphCanvas`(placeholder 박스) + `ResultsTable`(가변 컬럼) + `EvidencePanel`(접기/펴기, 내부에 `SelfCorrectionTimeline`만) + `FollowUpChips`. 우측에 `CypherSlidePanel`이 별도로 항상 마운트되어(EvidencePanel 열림 여부와 무관) 접기/펴기 가능한 폭-전환 컬럼으로 Cypher 코드를 보여준다.

세션 질문 이력(`history: HistoryItem[]`)은 `Dashboard`의 로컬 `useState`로 관리한다(백엔드가 없어 새로고침하면 초기화됨 — 영속화는 API 연동 시점의 과제). 질문 제출마다 항목을 앞에 추가하고, `SchemaSidebar`의 "질문 이력" 탭과 idle 화면의 예시 카드 노출 여부가 이 하나의 배열을 함께 참조한다.

공통 레이아웃(두 뷰 모두에 존재):
- `TopBar` — 서비스명(클릭 시 idle 화면으로 이동하는 홈 내비게이션 겸용), 부제, 연결 상태 배지(`connected: boolean` prop, 연결 안 됐을 때는 회색/빨간 dot + "Neo4j 연결 안됨"만 표시하고 READ 전용 배지는 아예 숨김 — 백엔드가 없는 지금은 항상 `connected=false`), READ 전용 배지(연결됐을 때만 표시, `readOnly: boolean` prop), `ThemeToggle`
- `SchemaSidebar` — 240px 고정폭, "스키마" / "질문 이력" 탭 전환(`Tabs`). 스키마 탭: 노드 라벨 아코디언 + 관계 타입 목록(mock 데이터, 실데이터 없어도 UI 자체는 실제로 펼침/접힘 동작). 이력 탭: `history`가 비어있으면 placeholder 문구, 있으면 실제 질문 목록(클릭 시 입력창에 채움).

## 이번 범위에서 제외

- 실제 그래프 렌더링 — `PathGraphCanvas`는 "그래프 시각화 영역" 텍스트가 있는 placeholder 박스로 둔다. 실제 데이터가 붙는 시점에 react-force-graph-2d를 설치하고 `dagMode="lr"` 옵션으로 구현한다.
- 실제 API 호출 — 백엔드 계약 확정 후 Axios 인스턴스와 React Query 훅을 추가한다.
- 에러 상태 화면(자기수정 3회 실패), 평가 대시보드 화면(`Dashboard.dc.html` 화면 2, 4) — 이번 "틀" 범위 밖.
- 사용자/개발자 모드 토글 — 채택하지 않음(위 "배경" 참고).

## 컴포넌트 목록과 책임

모든 컴포넌트는 props로 데이터를 받는 순수 프레젠테이션 컴포넌트로 작성한다. 목데이터는 `screens/Dashboard.tsx`에서 로컬 상수로 주입한다.

| 컴포넌트 | 위치 | 책임 |
|---|---|---|
| `TopBar` | `components/layout` | 서비스명(홈 이동 버튼 겸용), 연결/READ전용 배지(연결 상태에 따라 조건부), 테마 토글 |
| `SchemaSidebar` | `components/layout` | 스키마/이력 탭 전환, 노드 아코디언, 관계 목록, 실제 세션 질문 이력 표시 |
| `QueryInputBar` | `components/query` | 입력창 + 질문하기 버튼(클릭 가능하나 요청 미전송) |
| `ExampleQuestionCard` | `components/query` | 예시 질문 카드, 클릭 시 입력창에 텍스트만 채움 |
| `NaturalLanguageAnswerBox` | `components/query` | 자연어 답변 텍스트, 엔티티 강조 스타일 |
| `FollowUpChips` | `components/query` | 후속 질문 pill 버튼 |
| `PathGraphCanvas` | `components/graph` | 그래프 시각화 placeholder 박스 + 줌 컨트롤 버튼(비활성) |
| `ResultsTable` | `components/result` | 가변 컬럼 테이블(`columns: {key,label}[]`, `rows: Record<string,string>[]` props) |
| `EvidencePanel` | `components/result` | 접기/펴기 컨테이너, 내부에 `SelfCorrectionTimeline`만 담음 |
| `SelfCorrectionTimeline` | `components/result` | 단계별 dot(success/fail/warn) + 제목 + 소요시간 |
| `CypherSlidePanel` | `components/result` | 화면 우측에 항상 마운트되는 폭-전환 슬라이드 패널. 코드블록(다크 고정 배경) + 복사 버튼 + 패널 전체 접기/펴기 토글 |

## 상태 관리

`store/useUiStore.ts` (Zustand, UI 전용 상태만):

```ts
interface UiStore {
  theme: 'light' | 'dark';
  activeScreen: 'idle' | 'loading' | 'success' | 'error';
  evidencePanelOpen: boolean;
  cypherCollapsed: boolean;
  historyTab: 'schema' | 'history';
  setTheme: (t: 'light' | 'dark') => void;
  setActiveScreen: (s: UiStore['activeScreen']) => void;
  toggleEvidencePanel: () => void;
  toggleCypherCollapsed: () => void;
  setHistoryTab: (tab: 'schema' | 'history') => void;
}
```

(`selectedNodeId`/`setSelectedNodeId`는 최종 리뷰에서 미사용 상태로 확인되어 제거했다 — 실제 그래프 캔버스가 노드 선택을 필요로 하는 시점에 다시 추가한다.)

`activeScreen`은 이번 범위에서 `'idle'`↔`'success'`만 실제로 전환된다(질문하기 버튼 클릭 시 mock 성공 결과로 전환). `'loading'`/`'error'`는 타입에 존재하되 대응 UI는 다음 단계 작업.

## 데이터 타입

`src/types/query.ts`에 순수 TS interface로 정의한다(런타임 파싱 없음 — 이유는 위 "스코프 트리밍 원칙" 참고):

- `SelfCorrectionStep` — `status: 'success'|'fail'|'warn'`, `title`, `detail`, `elapsedMs`
- `QueryResult` — `answer`, `cypher`, `columns`, `rows`, `timeline: SelfCorrectionStep[]`
- `HistoryItem` — `id`, `question`, `submittedAt`(timestamp)

실제 API 연동 시 이 interface들을 동일한 필드 구조의 zod schema로 옮기고 `schema.parse(response.data)`로 런타임 검증을 추가한다.

## 폴더 구조

```
src/
  types/
    query.ts        # TS interface (SelfCorrectionStep, QueryResult)
  lib/
    utils.ts         # cn() (shadcn 컨벤션)
  store/
    useUiStore.ts
  components/
    ui/               # shadcn 프리미티브 (button, input, card, badge, table, tabs)
    layout/
      TopBar.tsx
      SchemaSidebar.tsx
    query/
      QueryInputBar.tsx
      ExampleQuestionCard.tsx
      NaturalLanguageAnswerBox.tsx
      FollowUpChips.tsx
    graph/
      PathGraphCanvas.tsx
    result/
      ResultsTable.tsx
      EvidencePanel.tsx
      SelfCorrectionTimeline.tsx
      CypherSlidePanel.tsx
  screens/
    Dashboard.tsx      # idle/success 조합 + mock 데이터 주입
  App.tsx
```

## 스택 세팅 항목

- Tailwind CSS 설치 및 `tailwind.config` — `theme.extend.colors`에 디자인 토큰(Okabe-Ito 노드 5색 + 뉴트럴 라이트/다크) 등록, `darkMode: 'class'`
- shadcn/ui 초기화, 사용 컴포넌트만 추가: Button, Input, Card, Badge, Table, Tabs
- Zustand 설치
- Pretendard 폰트 CDN 링크(`index.html`)

**버그로 확인된 것**: `src/index.css`는 shadcn init이 원래 넣어주는 `@import "shadcn/tailwind.css";`(`node_modules/shadcn/dist/tailwind.css` — `data-active`/`data-horizontal`/`data-vertical` 등 shadcn 컴포넌트가 의존하는 커스텀 Tailwind variant 정의)도 반드시 포함해야 한다. 디자인 토큰 적용 단계에서 이 import를 빠뜨리면 `Tabs` 같은 컴포넌트의 `flex-direction: column`이 조용히 무시되어 좁은 사이드바에서 레이아웃이 깨진다(실사용 중 발견, `index.css`에 복구 완료).

## 디자인 토큰

### 노드 라벨 컬러 (Okabe-Ito)
| 라벨 | Hex |
|---|---|
| Lot / Product | `#0072B2` |
| Process / WorkOrder | `#009E73` |
| Equipment / Location | `#E69F00` |
| Material / Vendor | `#CC79A7` |
| Defect / ScrapReason | `#D55E00` |

### 뉴트럴 (Light / Dark)
| 토큰 | Light | Dark |
|---|---|---|
| bg | `#f4f5f6` | `#15181b` |
| panel | `#ffffff` | `#1d2126` |
| panel2 | `#fafbfc` | `#20242a` |
| border | `#e1e4e8` | `#2b3036` |
| borderStrong | `#c7cbd1` | `#3a4048` |
| text | `#1a1d21` | `#e7eaed` |
| textMuted | `#6b7280` | `#9aa3ad` |
| textFaint | `#9aa1ab` | `#6b727b` |
| accentBg | `#eef2fb` | `#232a3a` |
| info | `#2f6fb0` | `#5b9bd9` |
| success | `#2f9e5c` | `#5fc98a` |
| fail | `#d14343` | `#f07171` |
| warn | `#c98a2e` | `#e8b563` |

Cypher 코드블록은 테마 무관 고정: 배경 `#1c1f24`, 텍스트 `#e7eaed`.

### 타이포그래피 / 스페이싱
- 폰트: Pretendard
- 스케일: 9.5px~26px (마이크로 라벨 → 타이틀/수치 강조)
- 사이드바 고정폭: 240px(스키마/이력 탭)
- 카드 radius 9~10px, pill 20~24px, 원형 배지 50%

## 브랜치

`feat/kg-dashboard-ui-shell` — `dev`에서 분기
