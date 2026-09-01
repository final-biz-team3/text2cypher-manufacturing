import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Maximize2, Minus, Plus, RotateCcw, Search, X } from 'lucide-react'
import {
  SigmaContainer,
  useCamera,
  useRegisterEvents,
  useSetSettings,
  useSigma,
} from '@react-sigma/core'
import FA2Layout from 'graphology-layout-forceatlas2/worker'
import noverlap from 'graphology-layout-noverlap'
import { createEdgeArrowProgram } from 'sigma/rendering'
import type { MultiDirectedGraph } from 'graphology'
import { Button } from '@/components/ui/button'
import { createGraphologyGraph } from '@/lib/graph/createGraphologyGraph'
import type { GraphAttributes, GraphEdgeAttributes, GraphNodeAttributes } from '@/types/graph'

type SigmaGraphologyGraph = MultiDirectedGraph<
  GraphNodeAttributes,
  GraphEdgeAttributes,
  GraphAttributes
>

const GraphEdgeArrowProgram = createEdgeArrowProgram<
  GraphNodeAttributes,
  GraphEdgeAttributes,
  GraphAttributes
>()

interface SigmaGraphProps {
  rows: readonly Record<string, unknown>[]
}

interface SigmaGraphViewProps {
  graph: SigmaGraphologyGraph
  issueCount: number
}

interface SearchEntry {
  node: string
  label: string
  category: string
  color: string
  searchableText: string
}

interface CategorySummary {
  category: string
  color: string
  total: number
}

interface GraphRuntimeProps {
  graph: SigmaGraphologyGraph
  selectedNodeIds: ReadonlySet<string>
  hoveredNode: string | null
  hiddenCategories: ReadonlySet<string>
  searchResultNodeId: string | null
  onSelectedNodeChange: (node: string | null) => void
  onHoveredNodeChange: (node: string | null) => void
}

function GraphRuntime({
  graph,
  selectedNodeIds,
  hoveredNode,
  hiddenCategories,
  searchResultNodeId,
  onSelectedNodeChange,
  onHoveredNodeChange,
}: GraphRuntimeProps) {
  const sigma = useSigma<GraphNodeAttributes, GraphEdgeAttributes, GraphAttributes>()
  const registerEvents = useRegisterEvents<
    GraphNodeAttributes,
    GraphEdgeAttributes,
    GraphAttributes
  >()
  const setSettings = useSetSettings<GraphNodeAttributes, GraphEdgeAttributes, GraphAttributes>()
  const draggedNodeRef = useRef<string | null>(null)
  const disposeLayoutRef = useRef<(() => void) | null>(null)

  const selectedNode = selectedNodeIds.values().next().value ?? null
  const focusedNode = hoveredNode ?? selectedNode ?? searchResultNodeId
  const focusedNeighborIds = useMemo(() => {
    if (!focusedNode || !graph.hasNode(focusedNode)) return new Set<string>()
    return new Set(graph.neighbors(focusedNode))
  }, [focusedNode, graph])

  useEffect(() => {
    registerEvents({
      clickNode: ({ node }) => onSelectedNodeChange(node),
      clickStage: () => onSelectedNodeChange(null),
      enterNode: ({ node }) => onHoveredNodeChange(node),
      leaveNode: () => onHoveredNodeChange(null),
      downNode: ({ node, event }) => {
        disposeLayoutRef.current?.()
        draggedNodeRef.current = node
        sigma.getCamera().disable()
        event.preventSigmaDefault()
      },
      moveBody: ({ event }) => {
        const draggedNode = draggedNodeRef.current
        if (!draggedNode) return

        const position = sigma.viewportToGraph(event)
        graph.mergeNodeAttributes(draggedNode, position)
        sigma.refresh({ partialGraph: { nodes: [draggedNode] }, skipIndexation: true })
        event.preventSigmaDefault()
      },
      upStage: () => {
        if (!draggedNodeRef.current) return
        draggedNodeRef.current = null
        sigma.getCamera().enable()
      },
      upNode: () => {
        if (!draggedNodeRef.current) return
        draggedNodeRef.current = null
        sigma.getCamera().enable()
      },
    })
    return () => {
      draggedNodeRef.current = null
      sigma.getCamera().enable()
    }
  }, [graph, onHoveredNodeChange, onSelectedNodeChange, registerEvents, sigma])

  useEffect(() => {
    setSettings({
      nodeReducer: (node, attributes) => {
        const isHidden = hiddenCategories.has(attributes.category)
        const isSelected = selectedNodeIds.has(node)
        const isHovered = node === hoveredNode
        const isSearchResult = node === searchResultNodeId
        const isRelated = !focusedNode || node === focusedNode || focusedNeighborIds.has(node)
        return {
          ...attributes,
          hidden: isHidden || attributes.hidden,
          label: isSelected || isHovered || isSearchResult ? attributes.label : null,
          color: isSelected
            ? '#245ea8'
            : isSearchResult
              ? '#7c5cb8'
              : isRelated
                ? attributes.color
                : '#d9dde2',
          size: attributes.size * (isSelected ? 1.7 : isSearchResult ? 1.55 : isHovered ? 1.4 : 1),
          zIndex: isSelected || isHovered || isSearchResult ? 3 : 1,
        }
      },
      edgeReducer: (edge, attributes) => {
        const [source, target] = graph.extremities(edge)
        const sourceHidden = hiddenCategories.has(graph.getNodeAttribute(source, 'category'))
        const targetHidden = hiddenCategories.has(graph.getNodeAttribute(target, 'category'))
        const isRelated = !focusedNode || source === focusedNode || target === focusedNode
        return {
          ...attributes,
          color: isRelated ? '#9aa4af' : '#e1e4e8',
          size: isRelated && focusedNode ? 1.35 : 0.45,
          hidden: attributes.hidden || sourceHidden || targetHidden,
        }
      },
    })
  }, [
    focusedNeighborIds,
    focusedNode,
    graph,
    hiddenCategories,
    hoveredNode,
    searchResultNodeId,
    selectedNodeIds,
    setSettings,
  ])

  useEffect(() => {
    if (!searchResultNodeId || !graph.hasNode(searchResultNodeId)) return
    const nodeData = sigma.getNodeDisplayData(searchResultNodeId)
    if (!nodeData) return
    const camera = sigma.getCamera()
    camera.animate(
      {
        x: nodeData.x,
        y: nodeData.y,
        ratio: Math.min(camera.getState().ratio, 0.22),
      },
      { duration: 450 },
    )
  }, [graph, searchResultNodeId, sigma])

  useEffect(() => {
    if (graph.order <= 1) {
      sigma.getCamera().animatedReset({ duration: 250 })
      return
    }

    const layout = new FA2Layout<GraphNodeAttributes, GraphEdgeAttributes>(graph, {
      settings: {
        adjustSizes: true,
        barnesHutOptimize: graph.order > 100,
        barnesHutTheta: 0.6,
        edgeWeightInfluence: 1,
        gravity: 2.4,
        scalingRatio: graph.order > 500 ? 3.2 : 2.2,
        slowDown: 8,
        strongGravityMode: true,
      },
    })
    layout.start()
    let killed = false
    let finishLayout: number | null = null
    const disposeLayout = () => {
      if (finishLayout !== null) {
        window.clearTimeout(finishLayout)
        finishLayout = null
      }
      if (killed) return
      layout.stop()
      layout.kill()
      killed = true
      if (disposeLayoutRef.current === disposeLayout) disposeLayoutRef.current = null
    }
    disposeLayoutRef.current = disposeLayout

    finishLayout = window.setTimeout(() => {
      layout.stop()
      layout.kill()
      killed = true
      finishLayout = null
      if (disposeLayoutRef.current === disposeLayout) disposeLayoutRef.current = null
      noverlap.assign(graph, {
        maxIterations: 70,
        settings: { margin: 2.5, ratio: 1.12 },
      })
      sigma.refresh()
      sigma.getCamera().animatedReset({ duration: 350 })
    }, 1800)

    return () => {
      disposeLayout()
    }
  }, [graph, sigma])

  return null
}

interface CameraControlsProps {
  onResetInteraction: () => void
}

function CameraControls({ onResetInteraction }: CameraControlsProps) {
  const { zoomIn, zoomOut, reset } = useCamera({ duration: 220, factor: 1.5 })
  return (
    <div className="absolute right-3 bottom-3 z-20 flex flex-col overflow-hidden rounded-lg border border-border/80 bg-panel/90 shadow-sm backdrop-blur-sm">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="rounded-none border-b border-border/70"
        onClick={() => zoomIn()}
        aria-label="그래프 확대"
      >
        <Plus />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="rounded-none border-b border-border/70"
        onClick={() => zoomOut()}
        aria-label="그래프 축소"
      >
        <Minus />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="rounded-none border-b border-border/70"
        onClick={() => reset()}
        aria-label="전체 그래프 맞춤"
      >
        <Maximize2 />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="rounded-none"
        onClick={() => {
          onResetInteraction()
          reset()
        }}
        aria-label="그래프 초기화"
      >
        <RotateCcw />
      </Button>
    </div>
  )
}

interface SearchOverlayProps {
  query: string
  isSearching: boolean
  isResultListVisible: boolean
  results: readonly SearchEntry[]
  onQueryChange: (query: string) => void
  onClear: () => void
  onSelect: (entry: SearchEntry) => void
}

function SearchOverlay({
  query,
  isSearching,
  isResultListVisible,
  results,
  onQueryChange,
  onClear,
  onSelect,
}: SearchOverlayProps) {
  const hasQuery = query.trim().length > 0
  return (
    <div className="absolute top-3 left-3 z-20 w-[min(280px,calc(100%-1.5rem))]">
      <div className="flex h-9 items-center gap-2 rounded-lg border border-border/80 bg-panel/92 px-2.5 shadow-sm backdrop-blur-sm focus-within:border-border-strong focus-within:ring-2 focus-within:ring-ring/15">
        <Search className="size-3.5 shrink-0 text-text-faint" aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="노드 ID, 이름 또는 타입 검색"
          className="min-w-0 flex-1 bg-transparent text-[11.5px] text-text outline-none placeholder:text-text-faint"
          aria-label="그래프 노드 검색"
        />
        {hasQuery ? (
          <button
            type="button"
            onClick={onClear}
            className="rounded p-0.5 text-text-faint transition-colors hover:bg-muted hover:text-text"
            aria-label="검색어 지우기"
          >
            <X className="size-3.5" />
          </button>
        ) : null}
      </div>
      {hasQuery && isResultListVisible ? (
        <div className="mt-1.5 max-h-48 overflow-y-auto rounded-lg border border-border/80 bg-panel/95 p-1 shadow-md backdrop-blur-sm">
          {isSearching ? (
            <p className="px-2 py-2 text-[11px] text-text-faint">검색 중…</p>
          ) : results.length > 0 ? (
            results.map((entry) => (
              <button
                key={entry.node}
                type="button"
                onClick={() => onSelect(entry)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted focus-visible:bg-muted"
              >
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: entry.color }}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate text-[11px] text-text">{entry.label}</span>
                <span className="shrink-0 text-[9.5px] text-text-faint">{entry.category}</span>
              </button>
            ))
          ) : (
            <p className="px-2 py-2 text-[11px] text-text-faint">검색 결과가 없습니다.</p>
          )}
        </div>
      ) : null}
    </div>
  )
}

interface LegendOverlayProps {
  categories: readonly CategorySummary[]
  hiddenCategories: ReadonlySet<string>
  onToggle: (category: string) => void
}

function LegendOverlay({ categories, hiddenCategories, onToggle }: LegendOverlayProps) {
  return (
    <div className="absolute top-16 right-3 z-20 max-h-44 w-[174px] overflow-y-auto rounded-lg border border-border/75 bg-panel/88 p-1.5 shadow-sm backdrop-blur-sm sm:top-3 sm:max-h-64">
      <p className="px-1.5 pb-1 text-[9.5px] font-semibold tracking-wide text-text-faint uppercase">
        노드 타입
      </p>
      <div className="flex flex-col gap-1">
        {categories.map(({ category, color, total }) => {
          const hidden = hiddenCategories.has(category)
          return (
            <button
              key={category}
              type="button"
              onClick={() => onToggle(category)}
              className="flex h-6 items-center gap-1.5 rounded-full border border-white/70 px-2 text-left shadow-[0_1px_2px_rgba(15,23,42,0.05)] transition-opacity hover:opacity-85 focus-visible:ring-2 focus-visible:ring-ring/30"
              style={{ backgroundColor: hidden ? '#e5e7eb' : `${color}33` }}
              aria-pressed={!hidden}
              aria-label={`${category} ${hidden ? '표시' : '숨기기'}`}
            >
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: hidden ? '#9ca3af' : color }}
                aria-hidden="true"
              />
              <span className="shrink-0 text-[9.5px] font-semibold text-text">
                {hidden ? 0 : total} / {total}
              </span>
              <span className="min-w-0 flex-1 truncate text-[9.5px] text-text-muted">
                {category}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function SelectionSummary({ total, selected }: { total: number; selected: number }) {
  return (
    <div className="absolute bottom-3 left-3 z-20 flex items-center overflow-hidden rounded-lg border border-border/80 bg-panel/90 shadow-sm backdrop-blur-sm">
      <span className="border-r border-border/70 px-3 py-2 text-[10.5px] font-medium text-text">
        전체 {total.toLocaleString()}
      </span>
      <span className="px-3 py-2 text-[10.5px] text-text-muted">선택 {selected}</span>
    </div>
  )
}

function buildSearchIndex(graph: SigmaGraphologyGraph): SearchEntry[] {
  return graph.mapNodes((node, attributes) => {
    const propertyTerms = Object.values(attributes.properties)
      .filter((value): value is string | number => ['string', 'number'].includes(typeof value))
      .map(String)
    return {
      node,
      label: attributes.label,
      category: attributes.category,
      color: attributes.color,
      searchableText: [
        node,
        attributes.originalId ?? '',
        attributes.label,
        attributes.category,
        ...propertyTerms,
      ]
        .join(' ')
        .toLocaleLowerCase(),
    }
  })
}

function buildCategorySummaries(graph: SigmaGraphologyGraph): CategorySummary[] {
  const categories = new Map<string, CategorySummary>()
  graph.forEachNode((_node, attributes) => {
    const current = categories.get(attributes.category)
    if (current) {
      current.total += 1
      return
    }
    categories.set(attributes.category, {
      category: attributes.category,
      color: attributes.color,
      total: 1,
    })
  })
  return [...categories.values()].sort((left, right) => right.total - left.total)
}

function SigmaGraphView({ graph, issueCount }: SigmaGraphViewProps) {
  const [selectedNodeIds, setSelectedNodeIds] = useState<ReadonlySet<string>>(() => new Set())
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [hiddenCategories, setHiddenCategories] = useState<ReadonlySet<string>>(() => new Set())
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResultNodeId, setSearchResultNodeId] = useState<string | null>(null)
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const searchIndex = useMemo(() => buildSearchIndex(graph), [graph])
  const categories = useMemo(() => buildCategorySummaries(graph), [graph])
  const searchResults = useMemo(() => {
    const normalizedQuery = deferredSearchQuery.trim().toLocaleLowerCase()
    if (!normalizedQuery) return []
    return searchIndex.filter((entry) => entry.searchableText.includes(normalizedQuery)).slice(0, 8)
  }, [deferredSearchQuery, searchIndex])

  const handleSelectedNodeChange = useCallback(
    (node: string | null) => setSelectedNodeIds(node ? new Set([node]) : new Set()),
    [],
  )
  const handleResetInteraction = useCallback(() => {
    setSelectedNodeIds(new Set())
    setHoveredNode(null)
    setHiddenCategories(new Set())
    setSearchQuery('')
    setSearchResultNodeId(null)
  }, [])
  const handleToggleCategory = useCallback(
    (category: string) => {
      const shouldHide = !hiddenCategories.has(category)
      const nextHiddenCategories = new Set(hiddenCategories)
      if (shouldHide) nextHiddenCategories.add(category)
      else nextHiddenCategories.delete(category)
      setHiddenCategories(nextHiddenCategories)

      const selectedNode = selectedNodeIds.values().next().value
      if (
        shouldHide &&
        selectedNode &&
        graph.getNodeAttribute(selectedNode, 'category') === category
      ) {
        setSelectedNodeIds(new Set())
      }
      if (
        shouldHide &&
        searchResultNodeId &&
        graph.getNodeAttribute(searchResultNodeId, 'category') === category
      ) {
        setSearchResultNodeId(null)
      }
    },
    [graph, hiddenCategories, searchResultNodeId, selectedNodeIds],
  )
  const handleSearchSelect = useCallback(
    (entry: SearchEntry) => {
      const nextHiddenCategories = new Set(hiddenCategories)
      nextHiddenCategories.delete(entry.category)
      setHiddenCategories(nextHiddenCategories)
      setSelectedNodeIds(new Set([entry.node]))
      setSearchResultNodeId(entry.node)
      setSearchQuery(entry.label)
    },
    [hiddenCategories],
  )
  const handleSearchClear = useCallback(() => {
    setSearchQuery('')
    setSearchResultNodeId(null)
  }, [])
  const handleSearchQueryChange = useCallback((query: string) => {
    setSearchQuery(query)
    setSearchResultNodeId(null)
  }, [])

  const selectedNode = selectedNodeIds.values().next().value ?? null
  const selectedAttributes =
    selectedNode && graph.hasNode(selectedNode) ? graph.getNodeAttributes(selectedNode) : null

  return (
    <section
      className="overflow-hidden rounded-xl border border-border bg-panel shadow-[0_1px_2px_rgba(15,23,42,0.03)]"
      aria-label="Sigma 지식그래프"
    >
      <div className="flex items-center justify-between border-b border-border/80 px-4 py-2.5">
        <div>
          <p className="text-[12.5px] font-semibold text-text">지식그래프</p>
          <p className="text-[10.5px] text-text-faint">
            노드 {graph.order.toLocaleString()}개 · 관계 {graph.size.toLocaleString()}개
          </p>
        </div>
        {selectedAttributes ? (
          <p className="max-w-[45%] truncate text-[11px] text-text-muted">
            선택: {selectedAttributes.label} · {selectedAttributes.category}
          </p>
        ) : (
          <p className="hidden text-[11px] text-text-faint sm:block">
            노드를 선택하거나 화면을 드래그해 탐색하세요
          </p>
        )}
      </div>
      <div className="relative h-[clamp(460px,62vh,720px)] min-h-[460px] bg-[#f5f5f6] dark:bg-panel-2">
        <SigmaContainer<GraphNodeAttributes, GraphEdgeAttributes, GraphAttributes>
          graph={graph}
          settings={{
            allowInvalidContainer: true,
            defaultEdgeColor: '#c8cdd3',
            defaultEdgeType: 'arrow',
            edgeProgramClasses: { arrow: GraphEdgeArrowProgram },
            enableCameraPanning: true,
            enableCameraZooming: true,
            hideEdgesOnMove: false,
            hideLabelsOnMove: true,
            labelColor: { color: '#374151' },
            labelDensity: 0.06,
            labelFont: 'Pretendard Variable, Pretendard, -apple-system, sans-serif',
            labelRenderedSizeThreshold: 4,
            labelSize: 11,
            renderEdgeLabels: false,
            renderLabels: true,
            stagePadding: 46,
            zIndex: true,
          }}
          style={{ height: '100%', width: '100%' }}
        >
          <GraphRuntime
            graph={graph}
            selectedNodeIds={selectedNodeIds}
            hoveredNode={hoveredNode}
            hiddenCategories={hiddenCategories}
            searchResultNodeId={searchResultNodeId}
            onSelectedNodeChange={handleSelectedNodeChange}
            onHoveredNodeChange={setHoveredNode}
          />
          <SearchOverlay
            query={searchQuery}
            isSearching={deferredSearchQuery !== searchQuery}
            isResultListVisible={searchResultNodeId === null}
            results={searchResults}
            onQueryChange={handleSearchQueryChange}
            onClear={handleSearchClear}
            onSelect={handleSearchSelect}
          />
          <LegendOverlay
            categories={categories}
            hiddenCategories={hiddenCategories}
            onToggle={handleToggleCategory}
          />
          <SelectionSummary total={graph.order} selected={selectedNodeIds.size} />
          <CameraControls onResetInteraction={handleResetInteraction} />
        </SigmaContainer>
      </div>
      {issueCount > 0 ? (
        <p className="border-t border-border px-3 py-1.5 text-[10.5px] text-warn">
          연결점이 없는 관계 {issueCount.toLocaleString()}개는 표시하지 않았습니다.
        </p>
      ) : null}
    </section>
  )
}

export function SigmaGraph({ rows }: SigmaGraphProps) {
  const graphResult = useMemo(() => createGraphologyGraph(rows), [rows])

  if (graphResult.graph.order === 0) {
    return (
      <div className="flex h-[460px] items-center justify-center rounded-xl border border-border bg-[#f5f5f6] text-[12.5px] text-text-faint dark:bg-panel-2">
        그래프로 변환 가능한 노드 또는 관계가 없습니다.
      </div>
    )
  }

  return <SigmaGraphView graph={graphResult.graph} issueCount={graphResult.issues.length} />
}
