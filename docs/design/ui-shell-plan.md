# 제조 지식그래프 대시보드 UI 틀 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백엔드 없이도 확인 가능한 "제조 지식그래프 어시스턴트" 프론트엔드 UI 틀(shell)을 `frontend/` 에 만든다. 질문 입력 전(idle)과 질문 결과(success) 두 상태를 오가는 단일 화면이며, 그래프 시각화 자리만 placeholder이고 나머지는 PDF 예시(LOT-2041)를 mock 데이터로 채운다.

**Architecture:** 기존 Vite(React 19 + TS) 스캐폴드 위에 Tailwind CSS v4 + shadcn/ui(프리미티브)로 스타일링하고, Zustand 스토어 하나로 UI 상태(테마·화면전환·패널토글·탭)만 관리한다. 모든 화면 컴포넌트는 props 기반 순수 프레젠테이션 컴포넌트이며, `screens/Dashboard.tsx`가 mock 데이터를 로컬 상수로 주입해 조립한다.

**Tech Stack:** React 19, Vite, TypeScript, Tailwind CSS v4(`@tailwindcss/vite`), shadcn/ui(radix 베이스), Zustand 5.

## Global Constraints

- 설계 문서: [`docs/design/ui-shell.md`](ui-shell.md) — 모든 태스크는 이 문서와 상충하지 않아야 한다.
- 스택은 React+Vite+TS, Zustand, Tailwind+shadcn/ui **만** 설치한다. Axios, React Query, zod, react-force-graph-2d, Vitest는 이번 범위에서 설치하지 않는다(YAGNI — 실제 사용 시점에 추가).
- 브랜치 `feat/kg-dashboard-ui-shell` 위에서 작업한다(`dev`에서 분기).
- 화면은 단일 화면(`Dashboard`)이며 `activeScreen: 'idle' | 'loading' | 'success' | 'error'` 타입 중 이번 범위는 `'idle'`↔`'success'` 전환만 실제로 구현한다.
- `PathGraphCanvas`는 그래프 시각화 placeholder 박스로만 구현한다 — 실제 그래프 렌더링 없음.
- 디자인 토큰(색상 hex, 사이드바 폭 240px 등)은 설계 문서에 정의된 값을 그대로 사용한다.
- 모든 화면 컴포넌트는 props로 데이터를 받는 순수 프레젠테이션 컴포넌트로 작성한다(내부에서 데이터를 하드코딩하지 않는다 — mock 데이터는 `screens/Dashboard.tsx`에서만 주입).
- 각 태스크의 검증은 `npx tsc -b --noEmit`, `npm run lint`, `npm run build` 세 명령이 모두 에러 없이 끝나는 것으로 확인한다(테스트 프레임워크 없음 — 설계 문서의 YAGNI 결정).

---

### Task 1: 브랜치 생성

**Files:** 없음(git 작업만)

- [ ] **Step 1: dev 최신 상태 확인 후 브랜치 생성**

```bash
git checkout dev
git pull origin dev
git checkout -b feat/kg-dashboard-ui-shell
```

- [ ] **Step 2: 브랜치 확인**

```bash
git branch --show-current
```

Expected: `feat/kg-dashboard-ui-shell`

---

### Task 2: Tailwind v4 + shadcn/ui 기반 설치, Vite 데모 보일러플레이트 제거

**Files:**
- Create: `frontend/components.json` (shadcn CLI가 생성)
- Create: `frontend/src/lib/utils.ts` (shadcn CLI가 생성)
- Create: `frontend/src/components/ui/button.tsx`, `input.tsx`, `card.tsx`, `badge.tsx`, `table.tsx`, `tabs.tsx` (shadcn CLI가 생성)
- Modify: `frontend/package.json` (의존성 추가)
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/tsconfig.json`, `frontend/tsconfig.app.json`
- Modify: `frontend/eslint.config.js`
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/App.css`, `frontend/src/assets/react.svg`, `frontend/src/assets/vite.svg`, `frontend/public/icons.svg`

**Interfaces:**
- Produces: `cn()` from `@/lib/utils`, shadcn 프리미티브(`Button`, `Input`, `Card`, `Badge`, `Table*`, `Tabs*`) — 이후 모든 태스크가 이 프리미티브를 사용한다. `@/*` → `frontend/src/*` 경로 별칭.

- [ ] **Step 1: Tailwind v4와 Vite 플러그인 설치**

```bash
cd frontend
npm install tailwindcss @tailwindcss/vite
```

- [ ] **Step 2: `vite.config.ts`에 Tailwind 플러그인과 `@` 경로 별칭 추가**

`frontend/vite.config.ts` 전체를 다음으로 교체:

```ts
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
})
```

- [ ] **Step 3: 경로 별칭을 tsconfig에도 등록**

`frontend/tsconfig.json` 전체를 다음으로 교체(설치된 TypeScript ~6.0에서 `baseUrl`은 deprecated이므로 `paths`만 사용):

```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ],
  "compilerOptions": {
    "paths": {
      "@/*": ["./src/*"]
    }
  }
}
```

`frontend/tsconfig.app.json`에서 `compilerOptions`의 `"tsBuildInfoFile"` 다음 줄에 추가:

```json
    "paths": {
      "@/*": ["./src/*"]
    },
```

- [ ] **Step 4: `src/index.css`를 Tailwind import만 남기고 비우기**

`frontend/src/index.css` 전체를 다음으로 교체(shadcn init이 다음 단계에서 이 파일을 다시 갱신한다):

```css
@import "tailwindcss";
```

- [ ] **Step 5: shadcn/ui 초기화 (Vite 템플릿, radix 베이스, Nova 프리셋)**

```bash
npx shadcn@latest init -t vite -b radix -p nova -y
```

이 명령은 `components.json`을 만들고, `src/lib/utils.ts`(`cn()`)와 `src/components/ui/button.tsx`를 생성하고, `src/index.css`를 shadcn 기본 디자인 토큰(OKLCH)으로 갱신하고, `class-variance-authority`/`clsx`/`tailwind-merge`/`lucide-react`/`radix-ui`/`tw-animate-css`/`@fontsource-variable/geist`를 `package.json`에 추가한다. (색상 토큰은 Task 3에서 우리 팔레트로 교체하고, Geist 폰트는 이번 태스크 Step 7에서 제거한다.)

- [ ] **Step 6: 나머지 shadcn 프리미티브 추가**

```bash
npx shadcn@latest add input card badge table tabs -y
```

Expected: `src/components/ui/input.tsx`, `card.tsx`, `badge.tsx`, `table.tsx`, `tabs.tsx` 생성됨.

- [ ] **Step 7: 사용하지 않는 Geist 폰트 패키지 제거**

```bash
npm uninstall @fontsource-variable/geist
```

(Task 3에서 Pretendard CDN 링크로 교체한다.)

- [ ] **Step 8: eslint가 shadcn 생성 파일을 오탐하지 않도록 예외 추가**

shadcn이 생성한 `src/components/ui/*.tsx` 파일들은 컴포넌트와 `cva` variant 함수를 한 파일에서 함께 export하는데, 이는 `eslint-plugin-react-refresh`의 `only-export-components` 규칙과 충돌한다. `frontend/eslint.config.js`의 `export default defineConfig([...])` 배열 마지막에 항목 추가:

```js
  {
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
```

- [ ] **Step 9: Vite 데모 보일러플레이트 제거**

```bash
cd frontend
rm src/App.css src/assets/react.svg src/assets/vite.svg public/icons.svg
```

`frontend/src/App.tsx` 전체를 다음으로 교체(다음 태스크들이 이어서 채워 나갈 임시 스모크 화면):

```tsx
import { Button } from '@/components/ui/button'

function App() {
  return (
    <div className="p-4">
      <Button>test</Button>
    </div>
  )
}

export default App
```

- [ ] **Step 10: 빌드로 전체 파이프라인 검증**

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료. `dist/` 산출물에 `index.html`, css, js 번들 생성.

- [ ] **Step 11: 커밋**

```bash
cd frontend
git add -A
git commit -m "Chore: Tailwind v4 + shadcn/ui 기반 설치, Vite 데모 보일러플레이트 제거"
```

---

### Task 3: 디자인 토큰 적용 (컬러 팔레트, Pretendard 폰트)

**Files:**
- Modify: `frontend/src/index.css`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: Task 2의 shadcn `@theme` 구조(`src/index.css`의 `@theme inline` 블록)
- Produces: Tailwind 유틸리티 클래스 `bg-bg`, `bg-panel`, `bg-panel-2`, `border-border-strong`, `text-text`, `text-text-muted`, `text-text-faint`, `bg-accent-bg`, `bg-info`/`text-info`, `bg-success`, `bg-fail`, `bg-warn`, `bg-code`/`text-code-text`, `bg-node-lot`/`bg-node-process`/`bg-node-equipment`/`bg-node-material`/`bg-node-defect` — 이후 모든 컴포넌트 태스크가 이 클래스들을 사용한다. shadcn 시맨틱 토큰(`background`, `foreground`, `card`, `primary` 등)도 같은 팔레트로 매핑되어 shadcn 프리미티브가 자동으로 새 색상을 반영한다.

- [ ] **Step 1: `src/index.css`를 디자인 토큰으로 교체**

`frontend/src/index.css` 전체를 다음으로 교체:

```css
@import "tailwindcss";
@import "tw-animate-css";

@custom-variant dark (&:is(.dark *));

@theme inline {
  --font-sans: Pretendard, -apple-system, 'Malgun Gothic', sans-serif;
  --font-mono: ui-monospace, Menlo, monospace;

  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);

  --color-bg: var(--bg);
  --color-panel: var(--panel);
  --color-panel-2: var(--panel-2);
  --color-border-strong: var(--border-strong);
  --color-text: var(--text);
  --color-text-muted: var(--text-muted);
  --color-text-faint: var(--text-faint);
  --color-accent-bg: var(--accent-bg);
  --color-info: var(--info);
  --color-success: var(--success);
  --color-fail: var(--fail);
  --color-warn: var(--warn);
  --color-code: var(--code);
  --color-code-text: var(--code-text);

  --color-node-lot: var(--node-lot);
  --color-node-process: var(--node-process);
  --color-node-equipment: var(--node-equipment);
  --color-node-material: var(--node-material);
  --color-node-defect: var(--node-defect);
}

:root {
  --background: #f4f5f6;
  --foreground: #1a1d21;
  --card: #ffffff;
  --card-foreground: #1a1d21;
  --popover: #ffffff;
  --popover-foreground: #1a1d21;
  --primary: #2f6fb0;
  --primary-foreground: #ffffff;
  --secondary: #fafbfc;
  --secondary-foreground: #1a1d21;
  --muted: #fafbfc;
  --muted-foreground: #6b7280;
  --accent: #eef2fb;
  --accent-foreground: #2f6fb0;
  --destructive: #d14343;
  --border: #e1e4e8;
  --input: #e1e4e8;
  --ring: #2f6fb0;

  --bg: #f4f5f6;
  --panel: #ffffff;
  --panel-2: #fafbfc;
  --border-strong: #c7cbd1;
  --text: #1a1d21;
  --text-muted: #6b7280;
  --text-faint: #9aa1ab;
  --accent-bg: #eef2fb;
  --info: #2f6fb0;
  --success: #2f9e5c;
  --fail: #d14343;
  --warn: #c98a2e;
  --code: #1c1f24;
  --code-text: #e7eaed;

  --node-lot: #0072b2;
  --node-process: #009e73;
  --node-equipment: #e69f00;
  --node-material: #cc79a7;
  --node-defect: #d55e00;

  --radius: 0.625rem;
}

.dark {
  --background: #15181b;
  --foreground: #e7eaed;
  --card: #1d2126;
  --card-foreground: #e7eaed;
  --popover: #1d2126;
  --popover-foreground: #e7eaed;
  --primary: #5b9bd9;
  --primary-foreground: #15181b;
  --secondary: #20242a;
  --secondary-foreground: #e7eaed;
  --muted: #20242a;
  --muted-foreground: #9aa3ad;
  --accent: #232a3a;
  --accent-foreground: #5b9bd9;
  --destructive: #f07171;
  --border: #2b3036;
  --input: #2b3036;
  --ring: #5b9bd9;

  --bg: #15181b;
  --panel: #1d2126;
  --panel-2: #20242a;
  --border-strong: #3a4048;
  --text: #e7eaed;
  --text-muted: #9aa3ad;
  --text-faint: #6b727b;
  --accent-bg: #232a3a;
  --info: #5b9bd9;
  --success: #5fc98a;
  --fail: #f07171;
  --warn: #e8b563;
}

@layer base {
  * {
    @apply border-border outline-ring/50;
  }
  body {
    @apply bg-background text-foreground;
  }
  html {
    @apply font-sans;
  }
}
```

주의: `--code`/`--code-text`는 `.dark`에서 재정의하지 않는다(Cypher 코드블록은 테마 무관 고정 배경). `--node-*`도 Okabe-Ito 팔레트로 테마 무관 고정이라 `.dark`에서 재정의하지 않는다.

- [ ] **Step 2: Pretendard 폰트 CDN 링크 추가, 타이틀 변경**

`frontend/index.html` 전체를 다음으로 교체:

```html
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link
      rel="stylesheet"
      as="style"
      crossorigin
      href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css"
    />
    <title>공정 지식그래프 어시스턴트</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 3: 검증**

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료.

- [ ] **Step 4: 커밋**

```bash
cd frontend
git add -A
git commit -m "Style: Okabe-Ito 기반 디자인 토큰과 Pretendard 폰트 적용"
```

---

### Task 4: 타입 정의와 Zustand UI 스토어

**Files:**
- Create: `frontend/src/types/query.ts`
- Create: `frontend/src/store/useUiStore.ts`

**Interfaces:**
- Produces:
  - `SelfCorrectionStep { id: string; status: 'success' | 'fail' | 'warn'; title: string; detail: string; elapsedMs: number }`
  - `ResultColumn { key: string; label: string }`
  - `QueryResult { answer: string; cypher: string; columns: ResultColumn[]; rows: Record<string, string>[]; timeline: SelfCorrectionStep[] }`
  - `NodeLabel = 'Lot' | 'Process' | 'Equipment' | 'Material' | 'Defect'`
  - `SchemaNode { label: NodeLabel; glyph: string; description: string; properties: string[] }`
  - `SchemaRelationship { name: string; description: string }`
  - `useUiStore` (Zustand hook) — state: `theme: 'light' | 'dark'`, `activeScreen: 'idle' | 'loading' | 'success' | 'error'`, `selectedNodeId: string | null`, `evidencePanelOpen: boolean`, `cypherCollapsed: boolean`, `historyTab: 'schema' | 'history'`; actions: `setTheme`, `setActiveScreen`, `setSelectedNodeId`, `toggleEvidencePanel`, `toggleCypherCollapsed`, `setHistoryTab`

- [ ] **Step 1: Zustand 설치**

```bash
cd frontend
npm install zustand
```

- [ ] **Step 2: 타입 정의**

`frontend/src/types/query.ts` 새로 작성:

```ts
export interface SelfCorrectionStep {
  id: string
  status: 'success' | 'fail' | 'warn'
  title: string
  detail: string
  elapsedMs: number
}

export interface ResultColumn {
  key: string
  label: string
}

export interface QueryResult {
  answer: string
  cypher: string
  columns: ResultColumn[]
  rows: Record<string, string>[]
  timeline: SelfCorrectionStep[]
}

export type NodeLabel = 'Lot' | 'Process' | 'Equipment' | 'Material' | 'Defect'

export interface SchemaNode {
  label: NodeLabel
  glyph: string
  description: string
  properties: string[]
}

export interface SchemaRelationship {
  name: string
  description: string
}
```

- [ ] **Step 3: Zustand 스토어 작성**

`frontend/src/store/useUiStore.ts` 새로 작성:

```ts
import { create } from 'zustand'

export type Theme = 'light' | 'dark'
export type ActiveScreen = 'idle' | 'loading' | 'success' | 'error'
export type SidebarTab = 'schema' | 'history'

interface UiStore {
  theme: Theme
  activeScreen: ActiveScreen
  selectedNodeId: string | null
  evidencePanelOpen: boolean
  cypherCollapsed: boolean
  historyTab: SidebarTab
  setTheme: (theme: Theme) => void
  setActiveScreen: (screen: ActiveScreen) => void
  setSelectedNodeId: (id: string | null) => void
  toggleEvidencePanel: () => void
  toggleCypherCollapsed: () => void
  setHistoryTab: (tab: SidebarTab) => void
}

export const useUiStore = create<UiStore>((set) => ({
  theme: 'light',
  activeScreen: 'idle',
  selectedNodeId: null,
  evidencePanelOpen: false,
  cypherCollapsed: false,
  historyTab: 'schema',
  setTheme: (theme) => set({ theme }),
  setActiveScreen: (activeScreen) => set({ activeScreen }),
  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),
  toggleEvidencePanel: () => set((s) => ({ evidencePanelOpen: !s.evidencePanelOpen })),
  toggleCypherCollapsed: () => set((s) => ({ cypherCollapsed: !s.cypherCollapsed })),
  setHistoryTab: (historyTab) => set({ historyTab }),
}))
```

- [ ] **Step 4: App.tsx에서 테마를 `<html>`에 반영해 스토어가 실제로 쓰이는지 확인**

`frontend/src/App.tsx` 전체를 다음으로 교체:

```tsx
import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

function App() {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return (
    <div className="p-4">
      <Button onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme}</Button>
    </div>
  )
}

export default App
```

- [ ] **Step 5: 검증**

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료.

- [ ] **Step 6: 커밋**

```bash
cd frontend
git add -A
git commit -m "Feat: 쿼리 결과 타입과 Zustand UI 스토어 추가"
```

---

### Task 5: 레이아웃 컴포넌트 (TopBar, SchemaSidebar)

**Files:**
- Create: `frontend/src/components/layout/TopBar.tsx`
- Create: `frontend/src/components/layout/SchemaSidebar.tsx`

**Interfaces:**
- Consumes: `useUiStore`(Task 4), `Badge`/`Button`/`Tabs*`(Task 2), 디자인 토큰 클래스(Task 3)
- Produces: `TopBar()`(props 없음), `SchemaSidebar()`(props 없음) — Task 9의 `Dashboard`가 그대로 렌더링한다.

- [ ] **Step 1: TopBar 작성**

`frontend/src/components/layout/TopBar.tsx` 새로 작성:

```tsx
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUiStore } from '@/store/useUiStore'

export function TopBar() {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-panel px-4">
      <div className="flex items-baseline gap-2">
        <span className="text-[15px] font-bold text-text">공정 지식그래프 어시스턴트</span>
        <span className="text-xs text-text-muted">품질 분석 · Neo4j 지식그래프</span>
      </div>
      <div className="flex items-center gap-2">
        <Badge
          variant="outline"
          className="gap-1.5 rounded-full border-border-strong px-3 py-1 text-[11.5px] font-normal text-text"
        >
          <span className="size-1.5 rounded-full bg-success" />
          Neo4j 연결됨 · bolt://prod-kg-01
        </Badge>
        <Badge
          variant="outline"
          className="rounded-full border-border-strong px-3 py-1 text-[11.5px] font-normal text-text"
        >
          READ 전용 · 쓰기 작업 차단됨
        </Badge>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
        >
          {theme === 'light' ? '다크모드' : '라이트모드'}
        </Button>
      </div>
    </header>
  )
}
```

- [ ] **Step 2: SchemaSidebar 작성**

`frontend/src/components/layout/SchemaSidebar.tsx` 새로 작성:

```tsx
import { useState } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useUiStore } from '@/store/useUiStore'
import type { SchemaNode, SchemaRelationship } from '@/types/query'

const SCHEMA_NODES: SchemaNode[] = [
  { label: 'Lot', glyph: 'L', description: '생산 배치 단위', properties: ['lot_id', 'product_code', 'created_at'] },
  { label: 'Process', glyph: 'P', description: '공정 단계', properties: ['process_name', 'sequence'] },
  { label: 'Equipment', glyph: 'EQ', description: '설비', properties: ['equipment_id', 'line'] },
  { label: 'Material', glyph: 'M', description: '투입 자재', properties: ['material_code', 'lot_no'] },
  { label: 'Defect', glyph: 'D', description: '불량 기록', properties: ['defect_code', 'severity', 'detected_at'] },
]

const RELATIONSHIPS: SchemaRelationship[] = [
  { name: 'FOLLOWS', description: '공정 순서' },
  { name: 'PROCESSED_AT', description: '설비 투입' },
  { name: 'HAS_DEFECT', description: '불량 발생' },
  { name: 'CONSUMES', description: '자재 소모' },
]

const NODE_COLOR_CLASS: Record<SchemaNode['label'], string> = {
  Lot: 'bg-node-lot',
  Process: 'bg-node-process',
  Equipment: 'bg-node-equipment',
  Material: 'bg-node-material',
  Defect: 'bg-node-defect',
}

export function SchemaSidebar() {
  const historyTab = useUiStore((s) => s.historyTab)
  const setHistoryTab = useUiStore((s) => s.setHistoryTab)
  const [openNode, setOpenNode] = useState<string | null>(null)

  return (
    <aside className="flex w-[240px] shrink-0 flex-col overflow-y-auto border-r border-border bg-panel">
      <Tabs value={historyTab} onValueChange={(v) => setHistoryTab(v as 'schema' | 'history')}>
        <TabsList variant="line" className="w-full px-2 pt-2">
          <TabsTrigger value="schema" className="flex-1">
            스키마
          </TabsTrigger>
          <TabsTrigger value="history" className="flex-1">
            질문 이력
          </TabsTrigger>
        </TabsList>
        <TabsContent value="schema" className="flex flex-col gap-4 p-3">
          <div className="flex flex-col gap-1">
            <p className="px-1 text-[11px] font-semibold uppercase text-text-faint">노드</p>
            {SCHEMA_NODES.map((node) => {
              const isOpen = openNode === node.label
              return (
                <div key={node.label}>
                  <button
                    type="button"
                    onClick={() => setOpenNode(isOpen ? null : node.label)}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-panel-2"
                  >
                    <span
                      className={`flex size-[18px] shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white ${NODE_COLOR_CLASS[node.label]}`}
                    >
                      {node.glyph}
                    </span>
                    <span className="flex-1 text-[12.5px] font-semibold text-text">{node.label}</span>
                    <span className={`text-text-faint transition-transform ${isOpen ? 'rotate-180' : ''}`}>▾</span>
                  </button>
                  {isOpen ? (
                    <div className="ml-7 flex flex-col gap-0.5 pb-1.5 text-[11px] text-text-faint">
                      <p>{node.description}</p>
                      <p className="font-mono">{node.properties.join(', ')}</p>
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
          <div className="flex flex-col gap-1">
            <p className="px-1 text-[11px] font-semibold uppercase text-text-faint">관계 타입</p>
            {RELATIONSHIPS.map((rel) => (
              <div key={rel.name} className="px-2 py-1">
                <p className="font-mono text-[11.5px] text-text">{rel.name}</p>
                <p className="text-[11px] text-text-faint">{rel.description}</p>
              </div>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="history" className="p-3">
          <p className="px-1 text-[12px] text-text-faint">아직 질문 이력이 없습니다.</p>
        </TabsContent>
      </Tabs>
    </aside>
  )
}
```

- [ ] **Step 3: App.tsx에서 두 컴포넌트가 실제로 렌더링되는지 임시 확인**

`frontend/src/App.tsx`에서 return 블록을 다음으로 교체(다음 태스크에서 다시 교체될 임시 배치):

```tsx
import { useEffect } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { SchemaSidebar } from '@/components/layout/SchemaSidebar'
import { useUiStore } from '@/store/useUiStore'

function App() {
  const theme = useUiStore((s) => s.theme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <SchemaSidebar />
      </div>
    </div>
  )
}

export default App
```

- [ ] **Step 4: 검증**

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료.

- [ ] **Step 5: 커밋**

```bash
cd frontend
git add -A
git commit -m "Feat: TopBar와 스키마/이력 탭 전환 SchemaSidebar 추가"
```

---

### Task 6: 질문 영역 컴포넌트 (QueryInputBar, ExampleQuestionCard, NaturalLanguageAnswerBox, FollowUpChips)

**Files:**
- Create: `frontend/src/components/query/QueryInputBar.tsx`
- Create: `frontend/src/components/query/ExampleQuestionCard.tsx`
- Create: `frontend/src/components/query/NaturalLanguageAnswerBox.tsx`
- Create: `frontend/src/components/query/FollowUpChips.tsx`

**Interfaces:**
- Produces:
  - `QueryInputBar({ value: string; onChange: (v: string) => void; onSubmit: () => void })`
  - `ExampleQuestionCard({ kind: '경로추적' | '집계'; question: string; path: { glyph: string; label: string }[]; onClick: () => void })`
  - `NaturalLanguageAnswerBox({ answer: string })`
  - `FollowUpChips({ questions: string[]; onSelect: (q: string) => void })`
  - Task 9의 `Dashboard`가 이 네 컴포넌트를 조립한다.

- [ ] **Step 1: QueryInputBar 작성**

`frontend/src/components/query/QueryInputBar.tsx` 새로 작성:

```tsx
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

interface QueryInputBarProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
}

export function QueryInputBar({ value, onChange, onSubmit }: QueryInputBarProps) {
  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="예: LOT-2041에서 발생한 불량의 원인 경로를 찾아줘"
        className="h-[46px] flex-1 rounded-[10px] px-4 text-base"
      />
      <Button type="submit" className="h-[46px] shrink-0 whitespace-nowrap rounded-[10px] px-5">
        질문하기
      </Button>
    </form>
  )
}
```

- [ ] **Step 2: ExampleQuestionCard 작성**

`frontend/src/components/query/ExampleQuestionCard.tsx` 새로 작성:

```tsx
interface ExampleQuestionCardProps {
  kind: '경로추적' | '집계'
  question: string
  path: { glyph: string; label: string }[]
  onClick: () => void
}

export function ExampleQuestionCard({ kind, question, path, onClick }: ExampleQuestionCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex flex-col gap-2 rounded-[10px] border border-border bg-panel p-3 text-left transition-colors hover:border-border-strong"
    >
      <span className="text-[10px] font-bold uppercase text-info">{kind}</span>
      <span className="line-clamp-2 text-[12.5px] text-text">{question}</span>
      <div className="flex flex-wrap gap-1">
        {path.map((node) => (
          <span
            key={node.label}
            className="flex items-center gap-1 rounded-full bg-panel-2 px-2 py-0.5 text-[10px] text-text-muted"
          >
            <span className="flex size-[11px] items-center justify-center rounded-full bg-info text-[7px] font-bold text-white">
              {node.glyph}
            </span>
            {node.label}
          </span>
        ))}
      </div>
    </button>
  )
}
```

- [ ] **Step 3: NaturalLanguageAnswerBox 작성**

`frontend/src/components/query/NaturalLanguageAnswerBox.tsx` 새로 작성:

```tsx
interface NaturalLanguageAnswerBoxProps {
  answer: string
}

export function NaturalLanguageAnswerBox({ answer }: NaturalLanguageAnswerBoxProps) {
  return (
    <div className="rounded-[10px] border border-info bg-accent-bg p-4 text-[13.5px] leading-relaxed text-text">
      {answer}
    </div>
  )
}
```

- [ ] **Step 4: FollowUpChips 작성**

`frontend/src/components/query/FollowUpChips.tsx` 새로 작성:

```tsx
interface FollowUpChipsProps {
  questions: string[]
  onSelect: (question: string) => void
}

export function FollowUpChips({ questions, onSelect }: FollowUpChipsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {questions.map((q) => (
        <button
          key={q}
          type="button"
          onClick={() => onSelect(q)}
          className="rounded-full border border-border bg-panel px-3 py-1.5 text-[12px] text-text hover:border-border-strong"
        >
          {q}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 5: 검증**

이번 태스크는 App.tsx를 건드리지 않는다(Task 9에서 한 번에 조립). 컴파일만 확인:

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료(신규 파일은 아직 아무 곳에서도 import되지 않으므로 `noUnusedLocals`류 에러와는 무관 — 파일 자체가 default export 없이도 컴파일된다).

- [ ] **Step 6: 커밋**

```bash
cd frontend
git add -A
git commit -m "Feat: 질문 입력/예시카드/답변박스/후속질문칩 컴포넌트 추가"
```

---

### Task 7: 그래프 시각화 placeholder (PathGraphCanvas)

**Files:**
- Create: `frontend/src/components/graph/PathGraphCanvas.tsx`

**Interfaces:**
- Produces: `PathGraphCanvas()`(props 없음) — Task 9의 `Dashboard`가 렌더링한다. 실제 그래프 데이터 props는 이번 범위에 없음(설계 문서 "이번 범위에서 제외" 참고 — react-force-graph-2d 연동 시 `nodes`/`edges` props를 추가한다).

- [ ] **Step 1: PathGraphCanvas 작성**

`frontend/src/components/graph/PathGraphCanvas.tsx` 새로 작성:

```tsx
import { Button } from '@/components/ui/button'

export function PathGraphCanvas() {
  return (
    <div className="relative flex h-[240px] items-center justify-center rounded-[10px] border border-border bg-panel-2">
      <p className="text-[12.5px] text-text-faint">그래프 시각화 영역 (react-force-graph-2d 연동 예정)</p>
      <div className="absolute top-2 right-2 flex gap-1">
        {['+', '-', '맞춤', '리셋'].map((label) => (
          <Button key={label} type="button" variant="outline" size="sm" disabled className="h-6 px-2 text-[11px]">
            {label}
          </Button>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 검증**

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료.

- [ ] **Step 3: 커밋**

```bash
cd frontend
git add -A
git commit -m "Feat: 그래프 시각화 placeholder PathGraphCanvas 추가"
```

---

### Task 8: 결과 영역 컴포넌트 (ResultsTable, EvidencePanel, SelfCorrectionTimeline, CypherCard)

**Files:**
- Create: `frontend/src/components/result/ResultsTable.tsx`
- Create: `frontend/src/components/result/SelfCorrectionTimeline.tsx`
- Create: `frontend/src/components/result/CypherCard.tsx`
- Create: `frontend/src/components/result/EvidencePanel.tsx`

**Interfaces:**
- Consumes: `ResultColumn`, `SelfCorrectionStep`(Task 4), `Table*`(Task 2)
- Produces:
  - `ResultsTable({ columns: ResultColumn[]; rows: Record<string, string>[] })`
  - `SelfCorrectionTimeline({ steps: SelfCorrectionStep[] })`
  - `CypherCard({ cypher: string; collapsed: boolean; onToggleCollapsed: () => void })`
  - `EvidencePanel({ open: boolean; onToggle: () => void; children: ReactNode })`
  - Task 9의 `Dashboard`가 이 네 컴포넌트를 조립한다(`EvidencePanel` 안에 `SelfCorrectionTimeline` + `CypherCard`를 children으로 넣는다).

- [ ] **Step 1: ResultsTable 작성**

`frontend/src/components/result/ResultsTable.tsx` 새로 작성:

```tsx
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { ResultColumn } from '@/types/query'

interface ResultsTableProps {
  columns: ResultColumn[]
  rows: Record<string, string>[]
}

export function ResultsTable({ columns, rows }: ResultsTableProps) {
  return (
    <div className="overflow-x-auto rounded-[10px] border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((col) => (
              <TableHead key={col.key}>{col.label}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {columns.map((col) => (
                <TableCell key={col.key}>{row[col.key] ?? '—'}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
```

- [ ] **Step 2: SelfCorrectionTimeline 작성**

`frontend/src/components/result/SelfCorrectionTimeline.tsx` 새로 작성:

```tsx
import type { SelfCorrectionStep } from '@/types/query'

const STATUS_CLASS: Record<SelfCorrectionStep['status'], string> = {
  success: 'bg-success',
  fail: 'bg-fail',
  warn: 'bg-warn',
}

interface SelfCorrectionTimelineProps {
  steps: SelfCorrectionStep[]
}

export function SelfCorrectionTimeline({ steps }: SelfCorrectionTimelineProps) {
  return (
    <ol className="flex flex-col gap-3">
      {steps.map((step) => (
        <li key={step.id} className="flex gap-2">
          <span className={`mt-1 size-2 shrink-0 rounded-full ${STATUS_CLASS[step.status]}`} />
          <div className="flex flex-col gap-0.5">
            <div className="flex items-baseline gap-2">
              <span className="text-[12.5px] font-semibold text-text">{step.title}</span>
              <span className="font-mono text-[11px] text-text-faint">{step.elapsedMs}ms</span>
            </div>
            <p className="text-[11px] text-text-muted">{step.detail}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}
```

- [ ] **Step 3: CypherCard 작성**

`frontend/src/components/result/CypherCard.tsx` 새로 작성:

```tsx
import { useState } from 'react'
import { Button } from '@/components/ui/button'

interface CypherCardProps {
  cypher: string
  collapsed: boolean
  onToggleCollapsed: () => void
}

export function CypherCard({ cypher, collapsed, onToggleCollapsed }: CypherCardProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    void navigator.clipboard.writeText(cypher)
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }

  return (
    <div className="rounded-[10px] border border-border">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-[11px] font-semibold uppercase text-text-faint">생성된 Cypher</span>
        <div className="flex gap-1">
          <Button type="button" variant="outline" size="sm" onClick={handleCopy} className="h-6 px-2 text-[11px]">
            {copied ? '복사됨' : '복사'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onToggleCollapsed}
            className="h-6 px-2 text-[11px]"
          >
            {collapsed ? '펴기' : '접기'}
          </Button>
        </div>
      </div>
      {collapsed ? null : (
        <pre className="overflow-x-auto bg-code p-3 font-mono text-[12px] leading-relaxed text-code-text">
          {cypher}
        </pre>
      )}
    </div>
  )
}
```

- [ ] **Step 4: EvidencePanel 작성**

`frontend/src/components/result/EvidencePanel.tsx` 새로 작성:

```tsx
import type { ReactNode } from 'react'

interface EvidencePanelProps {
  open: boolean
  onToggle: () => void
  children: ReactNode
}

export function EvidencePanel({ open, onToggle, children }: EvidencePanelProps) {
  return (
    <div className="rounded-[10px] border border-border bg-panel">
      <button type="button" onClick={onToggle} className="flex w-full items-center justify-between px-4 py-2.5 text-left">
        <span className="text-[12.5px] font-semibold text-text">어떻게 나온 답인지 보기</span>
        <span className="text-text-faint">{open ? '▾' : '▸'}</span>
      </button>
      {open ? <div className="grid grid-cols-2 gap-4 border-t border-border p-4">{children}</div> : null}
    </div>
  )
}
```

- [ ] **Step 5: 검증**

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료.

- [ ] **Step 6: 커밋**

```bash
cd frontend
git add -A
git commit -m "Feat: 결과 테이블/자기수정 타임라인/Cypher카드/근거패널 컴포넌트 추가"
```

---

### Task 9: Dashboard 화면 조립 (idle/success 전환, mock 데이터 주입)

**Files:**
- Create: `frontend/src/screens/Dashboard.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Task 5(`TopBar`, `SchemaSidebar`), Task 6(`QueryInputBar`, `ExampleQuestionCard`, `NaturalLanguageAnswerBox`, `FollowUpChips`), Task 7(`PathGraphCanvas`), Task 8(`ResultsTable`, `EvidencePanel`, `SelfCorrectionTimeline`, `CypherCard`), Task 4(`useUiStore`, `QueryResult`)
- Produces: `Dashboard()`(props 없음) — `App`이 렌더링하는 최종 화면.

- [ ] **Step 1: Dashboard 작성**

`frontend/src/screens/Dashboard.tsx` 새로 작성:

```tsx
import { useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { SchemaSidebar } from '@/components/layout/SchemaSidebar'
import { QueryInputBar } from '@/components/query/QueryInputBar'
import { ExampleQuestionCard } from '@/components/query/ExampleQuestionCard'
import { NaturalLanguageAnswerBox } from '@/components/query/NaturalLanguageAnswerBox'
import { FollowUpChips } from '@/components/query/FollowUpChips'
import { PathGraphCanvas } from '@/components/graph/PathGraphCanvas'
import { ResultsTable } from '@/components/result/ResultsTable'
import { EvidencePanel } from '@/components/result/EvidencePanel'
import { SelfCorrectionTimeline } from '@/components/result/SelfCorrectionTimeline'
import { CypherCard } from '@/components/result/CypherCard'
import { useUiStore } from '@/store/useUiStore'
import type { QueryResult } from '@/types/query'

const MOCK_RESULT: QueryResult = {
  answer:
    'LOT-2041은 세정을 거쳐 식각 공정에서 설비 EQ-07에 투입되었고, 해당 지점에서 불량 D-114가 기록되었습니다.',
  cypher: `MATCH (l:Lot {lot_id:'LOT-2041'})-[:PROCESSED_AT]->(p1:Process)-[:FOLLOWS]->(p2:Process)
MATCH (p2)-[:PROCESSED_AT]->(eq:Equipment)
OPTIONAL MATCH (p2)-[:HAS_DEFECT]->(d:Defect)
RETURN l, p1, p2, eq, d`,
  columns: [
    { key: 'lot', label: 'Lot' },
    { key: 'process', label: 'Process' },
    { key: 'equipment', label: 'Equipment' },
    { key: 'defect', label: 'Defect' },
  ],
  rows: [
    { lot: 'LOT-2041', process: '세정', equipment: '—', defect: '—' },
    { lot: 'LOT-2041', process: '식각', equipment: 'EQ-07', defect: 'D-114 (Major)' },
  ],
  timeline: [
    { id: '1', status: 'success', title: 'Cypher 생성 (시도 1)', detail: '스키마 기반 쿼리 생성 완료', elapsedMs: 700 },
    {
      id: '2',
      status: 'fail',
      title: '실행 (시도 1) — 실패',
      detail: '관계 오류: CAUSED_BY는 존재하지 않는 관계 타입',
      elapsedMs: 500,
    },
    {
      id: '3',
      status: 'warn',
      title: '스키마 재주입 후 재생성 (시도 2)',
      detail: '관계 타입 목록을 컨텍스트에 포함',
      elapsedMs: 600,
    },
    { id: '4', status: 'success', title: '실행 (시도 2) — 성공', detail: '1.4초 · 3행 반환', elapsedMs: 1400 },
  ],
}

const EXAMPLE_QUESTIONS: {
  kind: '경로추적' | '집계'
  question: string
  path: { glyph: string; label: string }[]
}[] = [
  {
    kind: '경로추적',
    question: 'LOT-2041에서 발생한 불량의 원인 경로를 찾아줘',
    path: [
      { glyph: 'L', label: 'LOT-2041' },
      { glyph: 'P', label: '식각' },
      { glyph: 'D', label: 'D-114' },
    ],
  },
  {
    kind: '집계',
    question: '지난 분기 작업장별 폐기 수량과 주요 폐기 사유를 알려줘',
    path: [
      { glyph: 'EQ', label: '작업장' },
      { glyph: 'D', label: '폐기 사유' },
    ],
  },
]

const FOLLOW_UP_QUESTIONS = ['이 답의 근거를 더 자세히', 'EQ-07의 최근 불량 이력은?', '같은 유형의 다른 Lot도 있어?']

export function Dashboard() {
  const [queryText, setQueryText] = useState('')
  const activeScreen = useUiStore((s) => s.activeScreen)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const evidencePanelOpen = useUiStore((s) => s.evidencePanelOpen)
  const toggleEvidencePanel = useUiStore((s) => s.toggleEvidencePanel)
  const cypherCollapsed = useUiStore((s) => s.cypherCollapsed)
  const toggleCypherCollapsed = useUiStore((s) => s.toggleCypherCollapsed)

  const handleSubmit = () => {
    if (!queryText.trim()) return
    setActiveScreen('success')
  }

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <SchemaSidebar />
        <main className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
          <QueryInputBar value={queryText} onChange={setQueryText} onSubmit={handleSubmit} />
          {activeScreen === 'idle' ? (
            <div className="grid grid-cols-3 gap-3">
              {EXAMPLE_QUESTIONS.map((example) => (
                <ExampleQuestionCard
                  key={example.question}
                  kind={example.kind}
                  question={example.question}
                  path={example.path}
                  onClick={() => setQueryText(example.question)}
                />
              ))}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <NaturalLanguageAnswerBox answer={MOCK_RESULT.answer} />
              <PathGraphCanvas />
              <ResultsTable columns={MOCK_RESULT.columns} rows={MOCK_RESULT.rows} />
              <EvidencePanel open={evidencePanelOpen} onToggle={toggleEvidencePanel}>
                <SelfCorrectionTimeline steps={MOCK_RESULT.timeline} />
                <CypherCard
                  cypher={MOCK_RESULT.cypher}
                  collapsed={cypherCollapsed}
                  onToggleCollapsed={toggleCypherCollapsed}
                />
              </EvidencePanel>
              <FollowUpChips questions={FOLLOW_UP_QUESTIONS} onSelect={setQueryText} />
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: App.tsx가 Dashboard를 렌더링하도록 교체**

`frontend/src/App.tsx` 전체를 다음으로 교체:

```tsx
import { useEffect } from 'react'
import { Dashboard } from '@/screens/Dashboard'
import { useUiStore } from '@/store/useUiStore'

function App() {
  const theme = useUiStore((s) => s.theme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  return <Dashboard />
}

export default App
```

- [ ] **Step 3: 검증**

```bash
cd frontend
npx tsc -b --noEmit
npm run lint
npm run build
```

Expected: 세 명령 모두 에러 없이 종료.

- [ ] **Step 4: 커밋**

```bash
cd frontend
git add -A
git commit -m "Feat: Dashboard 화면 조립 - idle/success 상태 전환과 mock 데이터"
```

---

### Task 10: 브라우저 수동 검증

**Files:** 없음(검증만)

- [ ] **Step 1: 개발 서버 기동**

```bash
cd frontend
npm run dev
```

- [ ] **Step 2: idle 화면 확인**

브라우저로 `http://localhost:5173`(또는 터미널에 출력된 포트) 접속. 확인 항목:
- TopBar에 서비스명, 연결 배지 2개, 다크모드 버튼이 보인다.
- 좌측에 240px 폭 스키마 사이드바가 있고 "스키마"/"질문 이력" 탭이 있다.
- "스키마" 탭에서 노드(Lot/Process/Equipment/Material/Defect) 5개를 클릭하면 속성이 아코디언으로 펼쳐지고 화살표가 회전한다.
- "질문 이력" 탭을 누르면 "아직 질문 이력이 없습니다."가 보인다.
- 중앙에 입력창과 예시 질문 카드 2개(경로추적/집계)가 3열 그리드로 보인다.
- 예시 카드를 클릭하면 입력창에 질문 텍스트가 채워진다(자동 전송되지 않음).

- [ ] **Step 3: success 화면 확인**

입력창에 텍스트가 있는 상태에서 "질문하기"를 클릭(또는 Enter). 확인 항목:
- 자연어 답변 박스(LOT-2041 관련 문장)가 강조 배경으로 보인다.
- "그래프 시각화 영역 (react-force-graph-2d 연동 예정)" placeholder 박스가 보인다.
- 결과 테이블에 Lot/Process/Equipment/Defect 2행이 보인다.
- "어떻게 나온 답인지 보기"를 클릭하면 자기수정 타임라인(성공/실패/재시도 dot 색상 구분)과 Cypher 코드블록이 좌우로 보인다.
- Cypher 카드의 "복사" 버튼 클릭 시 "복사됨"으로 바뀌었다가 약 1.4초 후 "복사"로 되돌아온다.
- Cypher 카드의 "접기"를 누르면 코드블록이 사라지고 "펴기"로 바뀐다.
- 후속 질문 칩 3개가 보이고 클릭하면 입력창에 해당 텍스트가 채워진다.

- [ ] **Step 4: 다크모드 확인**

TopBar의 "다크모드" 버튼 클릭. 배경/텍스트/패널 색상이 어두운 팔레트로 전환되는지 확인. 다시 클릭해 라이트모드로 복귀 확인.

- [ ] **Step 5: 개발 서버 종료, 최종 빌드 재확인**

```bash
cd frontend
npm run build
```

Expected: 에러 없이 `dist/` 생성.

- [ ] **Step 6: 커밋 이력 확인**

원격 push는 이 계획 범위 밖이다(사용자 확인 후 별도 진행 — explicit permission 필요 항목).

```bash
git log --oneline dev..feat/kg-dashboard-ui-shell
```

Expected: Task 1~9에서 만든 커밋들이 순서대로 보인다.

---

## Self-Review 결과

- **스펙 커버리지**: `docs/design/ui-shell.md`의 "화면 구조"(Task 9), "컴포넌트 목록"(Task 5,6,7,8), "상태 관리"(Task 4), "폴더 구조"(Task 2~9 전체), "스택 세팅 항목"(Task 2), "디자인 토큰"(Task 3), "브랜치"(Task 1) 모두 태스크로 매핑됨. `types/query.ts`(Task 4)도 스펙의 "데이터 타입" 절과 일치.
- **플레이스홀더 스캔**: `PathGraphCanvas`의 "그래프 시각화 영역" 텍스트는 스펙이 명시적으로 요구한 UI 문구이지 미완성 표시가 아님 — 문제 없음. 그 외 TBD/TODO 없음.
- **타입 일관성**: `SelfCorrectionStep`, `ResultColumn`, `QueryResult`, `SchemaNode`, `SchemaRelationship`, `useUiStore` 필드명이 Task 4 정의 이후 모든 태스크에서 동일하게 사용됨(`historyTab`/`setHistoryTab`, `evidencePanelOpen`/`toggleEvidencePanel`, `cypherCollapsed`/`toggleCypherCollapsed` 등).
- **도구 체인 검증**: Tailwind v4 + shadcn init 플래그(`-t vite -b radix -p nova`), TS 6.0의 `baseUrl` deprecation 회피, eslint의 `react-refresh/only-export-components` shadcn 충돌 해결, `vite.config.ts`의 `import.meta.dirname` 사용까지 스크래치 디렉토리에서 실제로 `npm run build`/`npx tsc -b --noEmit`/`npx eslint .`를 실행해 확인함.
