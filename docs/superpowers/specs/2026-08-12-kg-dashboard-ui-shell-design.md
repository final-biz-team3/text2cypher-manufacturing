# 제조 지식그래프 대시보드 — UI 틀(shell) 설계

## 배경

백엔드(FastAPI + Neo4j 파이프라인)가 아직 완성되지 않은 상태에서, 프론트엔드 UI 골격을 먼저 만들어 둔다. 디자인 근거는 두 가지를 종합했다:

1. 사용자가 제공한 PDF 목업(`Graph_Screens.pdf`) — 질문→Cypher 생성→그래프/표 결과를 보여주는 3개 스크린샷
2. 사용자가 제공한 디자인 핸드오프 zip(`design_handoff_manufacturing_kg_dashboard/README.md`) — 컬러 토큰, 컴포넌트 목록, Zustand 스토어 설계, zod 스키마, 화면별 레이아웃이 상세히 정의된 hi-fi 스펙 문서. 두 소스가 겹치는 부분은 후자를 최종 기준으로 삼는다.

두 소스 사이의 충돌 하나는 사용자가 명시적으로 해소했다: 디자인 핸드오프 문서의 "User Mode / 개발자 모드" 화면 전환은 채택하지 않는다. 챗봇형 서비스이므로 화면은 하나이며, 질문 결과에 따라 내용만 달라진다. `User Mode Screen.dc.html`이 제안한 컴포넌트(`QuestionHistorySidebar`, `DataProvenancePanel`) 중 유용한 부분(스키마/이력 탭 전환, 근거 패널)만 흡수한다.

## 목표

- 실제 API 연동 없이, 이후 백엔드가 붙었을 때 데이터만 주입하면 되는 순수 프레젠테이션 컴포넌트 골격을 만든다.
- 디자인 핸드오프 문서가 지정한 스택(React+Vite+TS, Zustand, Tailwind+shadcn/ui, react-force-graph-2d, Axios, React Query, zod, Vitest)을 프로젝트에 세팅한다.
- 실제 그래프 시각화(react-force-graph-2d 연동), 실제 API 호출, 에러/평가 대시보드 화면은 이번 범위에서 제외한다.

## 화면 구조

단일 화면(`Dashboard`)이 내부 상태로 두 뷰를 전환한다. 상태는 Zustand의 `activeScreen: 'idle' | 'success'`로 관리한다 (`'loading' | 'error'`는 타입에는 남겨두되 이번 범위의 UI는 만들지 않는다 — 아래 "이번 범위에서 제외" 참고).

- **idle** (질문 전): `QueryInputBar` + 예시 질문 카드 3x2 그리드(`ExampleQuestionCard`) + 스키마 미리보기 SVG(`SchemaGraphDiagram`, placeholder)
- **success** (질문 후): `NaturalLanguageAnswerBox` + `PathGraphCanvas`(placeholder 박스) + `ResultsTable`(가변 컬럼) + `EvidencePanel`(접기/펴기, 내부에 `SelfCorrectionTimeline` + `CypherCard`) + `FollowUpChips`

공통 레이아웃(두 뷰 모두에 존재):
- `TopBar` — 서비스명, 부제, `ConnectionStatusBadge`, `ReadOnlyBadge`, `ThemeToggle`
- `SchemaSidebar` — 240px 고정폭, "스키마" / "질문 이력" 탭 전환(`Tabs`). 스키마 탭: 노드 라벨 아코디언 + 관계 타입 목록(둘 다 mock 데이터, 실데이터 없어도 UI 자체는 실제로 펼침/접힘 동작). 이력 탭: 빈 리스트 placeholder.

## 이번 범위에서 제외

- 실제 `react-force-graph-2d` 렌더링 — 라이브러리는 설치하되 `PathGraphCanvas`는 "그래프 시각화 영역" 텍스트가 있는 placeholder 박스로 둔다. 데이터가 붙는 시점에 `dagMode="lr"` 옵션으로 실제 구현한다.
- 실제 axios/React Query API 호출 — `QueryClientProvider`와 axios 인스턴스만 세팅, 실제 `useQuery`/`useMutation` 훅은 백엔드 계약 확정 후 다음 단계에서 작성한다.
- 에러 상태 화면(자기수정 3회 실패), 평가 대시보드 화면(`Dashboard.dc.html` 화면 2, 4) — 이번 "틀" 범위 밖.
- 사용자/개발자 모드 토글 — 채택하지 않음(위 "배경" 참고).

## 컴포넌트 목록과 책임

모든 컴포넌트는 props로 데이터를 받는 순수 프레젠테이션 컴포넌트로 작성한다. 목데이터는 스토리북 없이 `screens/Dashboard.tsx`에서 로컬 상수로 주입한다.

| 컴포넌트 | 위치 | 책임 |
|---|---|---|
| `TopBar` | `components/layout` | 서비스명, 배지 2개, 테마 토글 |
| `SchemaSidebar` | `components/layout` | 스키마/이력 탭 전환, 노드 아코디언, 관계 목록 |
| `QueryInputBar` | `components/query` | 입력창 + 질문하기 버튼(클릭 가능하나 요청 미전송) |
| `ExampleQuestionCard` | `components/query` | 예시 질문 카드, 클릭 시 입력창에 텍스트만 채움 |
| `NaturalLanguageAnswerBox` | `components/query` | 자연어 답변 텍스트, 엔티티 강조 스타일 |
| `FollowUpChips` | `components/query` | 후속 질문 pill 버튼 |
| `PathGraphCanvas` | `components/graph` | 그래프 시각화 placeholder 박스 + 줌 컨트롤 버튼(비활성) |
| `ResultsTable` | `components/result` | 가변 컬럼 테이블(`columns: {key,label}[]`, `rows: Record<string,string>[]` props) |
| `EvidencePanel` | `components/result` | 접기/펴기 컨테이너 |
| `SelfCorrectionTimeline` | `components/result` | 단계별 dot(success/fail/warn) + 제목 + 소요시간 |
| `CypherCard` | `components/result` | 코드블록(다크 고정 배경) + 복사 버튼 + 접기 토글 |
| `MetricCard` | `components/result` | 라벨 + 큰 수치 + 보조설명 (집계형 응답 대비 준비, idle/success 어느 쪽에도 아직 안 쓰이지만 타입/컴포넌트는 미리 만들어 둠) |

## 상태 관리

`store/useUiStore.ts` (Zustand, UI 전용 상태만 — 서버 상태는 React Query 몫):

```ts
interface UiStore {
  theme: 'light' | 'dark';
  activeScreen: 'idle' | 'loading' | 'success' | 'error';
  selectedNodeId: string | null;
  evidencePanelOpen: boolean;
  cypherCollapsed: boolean;
  historyTab: 'schema' | 'history';
  setTheme: (t: 'light' | 'dark') => void;
  setActiveScreen: (s: UiStore['activeScreen']) => void;
  setSelectedNodeId: (id: string | null) => void;
  toggleEvidencePanel: () => void;
  toggleCypherCollapsed: () => void;
  setHistoryTab: (tab: 'schema' | 'history') => void;
}
```

`activeScreen`은 이번 범위에서 `'idle'`↔`'success'`만 실제로 전환된다(질문하기 버튼 클릭 시 mock 성공 결과로 전환). `'loading'`/`'error'`는 타입에 존재하되 대응 UI는 다음 단계 작업.

## 데이터 타입 (zod)

`src/types/query.ts`에 아래 스키마와 추론 타입만 정의한다(실제 API 응답 파싱에는 아직 사용하지 않음, 다음 단계에서 axios 응답에 `.parse()` 적용):

- `SelfCorrectionStepSchema` — `status: 'success'|'fail'|'warn'`, `title`, `detail`, `elapsedMs`
- `QueryResultSchema` — `answer`, `cypher`, `columns`, `rows`, `timeline: SelfCorrectionStepSchema[]`

## 폴더 구조

```
src/
  types/
    query.ts        # zod 스키마 + 추론 타입
  lib/
    api.ts           # axios 인스턴스 (baseURL만, 실제 호출 없음)
    utils.ts          # cn() (shadcn 컨벤션)
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
      CypherCard.tsx
      MetricCard.tsx
  screens/
    Dashboard.tsx      # idle/success 조합 + mock 데이터 주입
  App.tsx
```

## 스택 세팅 항목

- Tailwind CSS 설치 및 `tailwind.config` — `theme.extend.colors`에 디자인 토큰(Okabe-Ito 노드 5색 + 뉴트럴 라이트/다크) 등록, `darkMode: 'class'`
- shadcn/ui 초기화, 사용 컴포넌트만 추가: Button, Input, Card, Badge, Table, Tabs
- Zustand 설치
- Axios 설치 + `src/lib/api.ts` 인스턴스(baseURL은 `.env`의 `VITE_API_BASE_URL` 참조, 실제 요청 함수는 아직 작성 안 함)
- React Query 설치 + `QueryClientProvider`를 `App.tsx`에 세팅
- zod 설치
- Vitest + React Testing Library 설치, `App.tsx` 렌더 스모크 테스트 1개
- Pretendard 폰트(npm 패키지) 설치, self-host
- react-force-graph-2d 설치만 (사용은 다음 단계)

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

## 테스트

- Vitest + RTL 세팅
- `Dashboard.tsx` 스모크 테스트: idle 상태 렌더 확인, "질문하기" 클릭 시 success 상태로 전환되고 `NaturalLanguageAnswerBox`가 나타나는지 확인

## 브랜치

`feat/kg-dashboard-ui-shell` — `dev`에서 분기
