import { MultiDirectedGraph } from 'graphology'
import { colorForCategory } from './graphStyle'
import { createGraphNodePresentation } from './graphNodePresentation'
import type {
  GraphAttributes,
  GraphBuildIssue,
  GraphEdgeAttributes,
  GraphNodeAttributes,
  NormalizedGraphEdge,
  NormalizedGraphNode,
} from '@/types/graph'

type GraphologyGraph = MultiDirectedGraph<GraphNodeAttributes, GraphEdgeAttributes, GraphAttributes>

// graphology-layout의 circular()는 그래프의 노드 삽입 순서로만 원 위에
// 배치한다 - 어느 노드끼리 연결됐는지는 전혀 안 본다. 그래서 공급업체 하나에
// 부품 여러 개가 연결된 것 같은 허브형 그래프에서 관계선이 원을 가로질러
// 서로 겹치기 쉬웠다. 대신 BFS로 순회한 순서(연결된 노드끼리 방문 순서가
// 가까움)로 원 위에 배치하면, 같은 허브에 연결된 노드들이 원 위에서도
// 서로 가까이 모여서 선이 짧고 서로 덜 겹친다. 연결 요소가 여러 개면
// (그래프가 서로 안 이어진 조각으로 나뉘면) 요소별로 이어서 순회해
// 같은 조각끼리 원 위에서도 뭉쳐 있게 한다.
function lowCrossingCircularPositions(
  graph: GraphologyGraph,
  nodeKeys: readonly string[],
  scale: number,
): Record<string, { x: number; y: number }> {
  const targetSet = new Set(nodeKeys)
  const visited = new Set<string>()
  const order: string[] = []

  for (const startKey of nodeKeys) {
    if (visited.has(startKey)) continue
    const queue = [startKey]
    visited.add(startKey)
    while (queue.length > 0) {
      const current = queue.shift()!
      order.push(current)
      const neighbors = graph.neighbors(current).sort()
      for (const neighbor of neighbors) {
        if (!targetSet.has(neighbor) || visited.has(neighbor)) continue
        visited.add(neighbor)
        queue.push(neighbor)
      }
    }
  }

  const total = order.length
  const positions: Record<string, { x: number; y: number }> = {}
  order.forEach((nodeKey, index) => {
    const angle = (2 * Math.PI * index) / total
    positions[nodeKey] = { x: scale * Math.cos(angle), y: scale * Math.sin(angle) }
  })
  return positions
}

interface GraphBuildResult {
  graph: GraphologyGraph
  issues: GraphBuildIssue[]
}

interface EntityDefinition {
  category: string
  idFields: readonly string[]
  nameFields: readonly string[]
}

const ENTITY_DEFINITIONS: readonly EntityDefinition[] = [
  {
    category: 'Product',
    idFields: [
      'productId',
      'componentId',
      'componentProductId',
      'finishedProductId',
      'rootProductId',
    ],
    nameFields: ['productName', 'componentName', 'finishedProductName', 'name'],
  },
  { category: 'Supplier', idFields: ['supplierId'], nameFields: ['supplierName', 'name'] },
  { category: 'WorkOrder', idFields: ['workOrderId'], nameFields: ['workOrderName'] },
  {
    category: 'RoutingOperation',
    idFields: ['routingOperationKey'],
    nameFields: ['operationName'],
  },
  { category: 'Location', idFields: ['locationId'], nameFields: ['locationName', 'name'] },
  {
    category: 'ScrapReason',
    idFields: ['scrapReasonId'],
    nameFields: ['scrapReasonName', 'name'],
  },
]

const UNIQUE_PROPERTY_BY_CATEGORY: Record<string, readonly string[]> = Object.fromEntries(
  ENTITY_DEFINITIONS.map(({ category, idFields }) => [category, idFields]),
)

const RELATIONSHIP_INFERENCE: readonly {
  source: string
  target: string
  type: string
}[] = [
  { source: 'supplierId', target: 'componentId', type: 'SUPPLIES' },
  { source: 'supplierId', target: 'productId', type: 'SUPPLIES' },
  { source: 'finishedProductId', target: 'componentId', type: 'REQUIRES_COMPONENT' },
  { source: 'rootProductId', target: 'componentId', type: 'REQUIRES_COMPONENT' },
  { source: 'workOrderId', target: 'productId', type: 'PRODUCES' },
  { source: 'workOrderId', target: 'routingOperationKey', type: 'HAS_OPERATION' },
  { source: 'routingOperationKey', target: 'locationId', type: 'PERFORMED_AT' },
  { source: 'workOrderId', target: 'scrapReasonId', type: 'SCRAPPED_DUE_TO' },
]

const PRODUCT_PATH_NAME_FIELDS: Record<string, readonly string[]> = {
  pathProductIds: ['pathProductNames', 'productNamePath'],
  productIds: ['productNames', 'productNamePath'],
  productIdPath: ['productNamePath', 'pathProductNames'],
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isIdentifier(value: unknown): value is string | number {
  return typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value))
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function firstIdentifier(
  source: Record<string, unknown>,
  fields: readonly string[],
): string | number | undefined {
  for (const field of fields) {
    const value = source[field]
    if (isIdentifier(value)) return value
  }
  return undefined
}

function firstText(source: Record<string, unknown>, fields: readonly string[]): string | undefined {
  for (const field of fields) {
    const value = source[field]
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}

function normalizeLabels(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((label): label is string => typeof label === 'string' && label.length > 0)
}

function categoryFromProperties(properties: Record<string, unknown>): string | undefined {
  for (const definition of ENTITY_DEFINITIONS) {
    if (firstIdentifier(properties, definition.idFields) !== undefined) return definition.category
  }
  return undefined
}

function stableNodeKey(category: string, id: string | number): string {
  const normalizedId = String(id)
  return normalizedId.startsWith(`${category}:`) ? normalizedId : `${category}:${normalizedId}`
}

export function normalizeGraphNode(
  input: unknown,
  fallbackCategory?: string,
  fallbackId?: string | number,
): NormalizedGraphNode | null {
  if (!isRecord(input)) return null

  const structuralFields = new Set([
    'category',
    'color',
    'displayLabel',
    'elementId',
    'hidden',
    'id',
    'identity',
    'label',
    'labels',
    'nodeType',
    'originalId',
    'properties',
    'size',
    'x',
    'y',
  ])
  const directProperties = Object.fromEntries(
    Object.entries(input).filter(([key]) => !structuralFields.has(key)),
  )
  const nestedProperties = isRecord(input.properties) ? input.properties : directProperties
  const properties = { ...nestedProperties }
  const labels = normalizeLabels(input.labels)
  const category =
    labels[0] ??
    (typeof input.category === 'string' ? input.category : undefined) ??
    (typeof input.nodeType === 'string' ? input.nodeType : undefined) ??
    fallbackCategory ??
    categoryFromProperties(properties)
  if (!category) return null

  const uniqueFields = UNIQUE_PROPERTY_BY_CATEGORY[category] ?? []
  const rawId =
    firstIdentifier(input, ['elementId', 'id', 'identity', 'originalId']) ??
    firstIdentifier(properties, uniqueFields) ??
    fallbackId
  if (rawId === undefined) return null

  const x = finiteNumber(input.x) ?? finiteNumber(properties.x)
  const y = finiteNumber(input.y) ?? finiteNumber(properties.y)
  const label =
    firstText(input, ['label', 'displayLabel', 'name']) ??
    firstText(properties, ['name', ...uniqueFields]) ??
    String(rawId)
  const key = stableNodeKey(category, rawId)
  const presentation = createGraphNodePresentation(category, label, properties)

  return {
    key,
    hasPosition: x !== undefined && y !== undefined,
    attributes: {
      label,
      category,
      ...presentation,
      color: typeof input.color === 'string' ? input.color : colorForCategory(category),
      size: finiteNumber(input.size) ?? 4.8,
      x: x ?? 0,
      y: y ?? 0,
      originalId: String(rawId),
      hidden: input.hidden === true,
      properties,
    },
  }
}

function endpointKey(value: unknown, categoryHint?: string): string | null {
  if (isIdentifier(value)) {
    const normalized = String(value)
    if (normalized.includes(':')) return normalized
    return categoryHint ? stableNodeKey(categoryHint, value) : normalized
  }
  return normalizeGraphNode(value, categoryHint)?.key ?? null
}

export function normalizeGraphEdge(input: unknown): NormalizedGraphEdge | null {
  if (!isRecord(input)) return null
  const relationshipType =
    (typeof input.relationshipType === 'string' ? input.relationshipType : undefined) ??
    (typeof input.type === 'string' ? input.type : undefined) ??
    (typeof input.label === 'string' ? input.label : undefined) ??
    'RELATED_TO'
  const source = endpointKey(
    input.source ?? input.startNodeElementId ?? input.start ?? input.from,
    typeof input.sourceCategory === 'string' ? input.sourceCategory : undefined,
  )
  const target = endpointKey(
    input.target ?? input.endNodeElementId ?? input.end ?? input.to,
    typeof input.targetCategory === 'string' ? input.targetCategory : undefined,
  )
  if (!source || !target) return null

  const properties = isRecord(input.properties) ? { ...input.properties } : {}
  const rawId = firstIdentifier(input, ['elementId', 'id', 'identity', 'originalId'])
  return {
    key: rawId === undefined ? undefined : `edge:${String(rawId)}`,
    source,
    target,
    relationshipType,
    attributes: {
      label: relationshipType,
      relationshipType,
      color: typeof input.color === 'string' ? input.color : '#c8cdd3',
      size: finiteNumber(input.size) ?? 0.45,
      weight: finiteNumber(input.weight) ?? 1,
      hidden: input.hidden === true,
      properties,
    },
  }
}

export function createStableEdgeKey(
  source: string,
  relationshipType: string,
  target: string,
  occurrenceIndex: number,
): string {
  return `edge:${source}:${relationshipType}:${target}:${occurrenceIndex}`
}

function addNormalizedNode(graph: GraphologyGraph, node: NormalizedGraphNode): void {
  if (graph.hasNode(node.key)) {
    graph.mergeNodeAttributes(node.key, node.attributes)
    return
  }
  graph.addNode(node.key, node.attributes)
}

function nodeFromScalarRow(
  row: Record<string, unknown>,
  definition: EntityDefinition,
  idField: string,
): NormalizedGraphNode | null {
  const id = row[idField]
  if (!isIdentifier(id)) return null
  return normalizeGraphNode(
    {
      properties: { ...row, [idField]: id },
      label: firstText(row, definition.nameFields) ?? String(id),
    },
    definition.category,
    id,
  )
}

function categoryForField(field: string): string | undefined {
  return ENTITY_DEFINITIONS.find((definition) => definition.idFields.includes(field))?.category
}

function extractKnownRowEntities(
  row: Record<string, unknown>,
  nodes: NormalizedGraphNode[],
  edges: NormalizedGraphEdge[],
): void {
  for (const definition of ENTITY_DEFINITIONS) {
    for (const idField of definition.idFields) {
      const node = nodeFromScalarRow(row, definition, idField)
      if (node) nodes.push(node)
    }
  }

  for (const pathField of ['pathProductIds', 'productIds', 'productIdPath']) {
    const path = row[pathField]
    if (!Array.isArray(path)) continue
    const namePath = PRODUCT_PATH_NAME_FIELDS[pathField]
      .map((field) => row[field])
      .find(Array.isArray)
    const pathEntries = path.flatMap((id, index) => {
      if (!isIdentifier(id)) return []
      const candidateName = namePath?.[index]
      return [{ id, name: typeof candidateName === 'string' ? candidateName : undefined }]
    })
    pathEntries.forEach(({ id, name }) => {
      const node = normalizeGraphNode(
        { properties: { productId: id, ...(name ? { name } : {}) } },
        'Product',
        id,
      )
      if (node) nodes.push(node)
    })
    for (let index = 0; index < pathEntries.length - 1; index += 1) {
      edges.push({
        source: stableNodeKey('Product', pathEntries[index].id),
        target: stableNodeKey('Product', pathEntries[index + 1].id),
        relationshipType: 'REQUIRES_COMPONENT',
        attributes: {
          label: 'REQUIRES_COMPONENT',
          relationshipType: 'REQUIRES_COMPONENT',
          color: '#c8cdd3',
          size: 0.45,
          weight: 1,
          properties: {},
        },
      })
    }
  }

  for (const relation of RELATIONSHIP_INFERENCE) {
    const sourceId = row[relation.source]
    const targetId = row[relation.target]
    const sourceCategory = categoryForField(relation.source)
    const targetCategory = categoryForField(relation.target)
    if (!isIdentifier(sourceId) || !isIdentifier(targetId) || !sourceCategory || !targetCategory) {
      continue
    }
    edges.push({
      source: stableNodeKey(sourceCategory, sourceId),
      target: stableNodeKey(targetCategory, targetId),
      relationshipType: relation.type,
      attributes: {
        label: relation.type,
        relationshipType: relation.type,
        color: '#c8cdd3',
        size: 0.45,
        weight: 1,
        properties: {},
      },
    })
  }
}

function extractContainer(
  value: unknown,
  nodes: NormalizedGraphNode[],
  edges: NormalizedGraphEdge[],
): void {
  if (!isRecord(value)) return

  const containerNodes = Array.isArray(value.nodes) ? value.nodes : []
  const containerEdges = Array.isArray(value.edges)
    ? value.edges
    : Array.isArray(value.relationships)
      ? value.relationships
      : []
  containerNodes.forEach((node) => {
    const normalized = normalizeGraphNode(node)
    if (normalized) nodes.push(normalized)
  })
  containerEdges.forEach((edge) => {
    const normalized = normalizeGraphEdge(edge)
    if (normalized) edges.push(normalized)
  })

  for (const nested of Object.values(value)) {
    if (!isRecord(nested)) continue
    if (Array.isArray(nested.nodes) || Array.isArray(nested.relationships)) {
      extractContainer(nested, nodes, edges)
      continue
    }
    const normalizedNode = normalizeGraphNode(nested)
    if (normalizedNode) nodes.push(normalizedNode)
    const normalizedEdge = normalizeGraphEdge(nested)
    if (normalizedEdge) edges.push(normalizedEdge)
  }

  extractKnownRowEntities(value, nodes, edges)
}

export function createGraphologyGraph(input: unknown): GraphBuildResult {
  const graph = new MultiDirectedGraph<GraphNodeAttributes, GraphEdgeAttributes, GraphAttributes>()
  graph.setAttribute('source', 'neo4j-api')
  const issues: GraphBuildIssue[] = []
  const nodes: NormalizedGraphNode[] = []
  const edges: NormalizedGraphEdge[] = []
  const values = Array.isArray(input) ? input : [input]

  values.forEach((value) => extractContainer(value, nodes, edges))
  nodes.forEach((node) => addNormalizedNode(graph, node))

  const nodeKeyByOriginalId = new Map<string, string>()
  const ambiguousOriginalIds = new Set<string>()
  graph.forEachNode((nodeKey, attributes) => {
    if (!attributes.originalId) return
    const existing = nodeKeyByOriginalId.get(attributes.originalId)
    if (existing && existing !== nodeKey) {
      ambiguousOriginalIds.add(attributes.originalId)
      nodeKeyByOriginalId.delete(attributes.originalId)
      return
    }
    if (!ambiguousOriginalIds.has(attributes.originalId)) {
      nodeKeyByOriginalId.set(attributes.originalId, nodeKey)
    }
  })

  const positionedNodes = new Set(nodes.filter((node) => node.hasPosition).map((node) => node.key))
  const missingPositions = graph.nodes().filter((node) => !positionedNodes.has(node))

  const occurrenceBySignature = new Map<string, number>()
  for (const edge of edges) {
    const source = graph.hasNode(edge.source)
      ? edge.source
      : (nodeKeyByOriginalId.get(edge.source) ?? edge.source)
    const target = graph.hasNode(edge.target)
      ? edge.target
      : (nodeKeyByOriginalId.get(edge.target) ?? edge.target)
    if (!graph.hasNode(source) || !graph.hasNode(target)) {
      issues.push({
        kind: 'missing-endpoint',
        message: `${edge.relationshipType}: ${source} -> ${target}`,
      })
      continue
    }
    const signature = `${source}\u0000${edge.relationshipType}\u0000${target}`
    const occurrence = occurrenceBySignature.get(signature) ?? 0
    occurrenceBySignature.set(signature, occurrence + 1)
    const key = edge.key ?? createStableEdgeKey(source, edge.relationshipType, target, occurrence)
    if (graph.hasEdge(key)) continue
    graph.addDirectedEdgeWithKey(key, source, target, edge.attributes)
  }

  // 엣지를 다 넣은 뒤에 위치를 잡는다 - 인접 정보(누가 누구와 연결됐는지)가
  // 있어야 BFS 순서로 원 위에 배치해 선 교차를 줄일 수 있다.
  if (missingPositions.length > 0) {
    const positions = lowCrossingCircularPositions(
      graph,
      missingPositions,
      Math.max(1, graph.order / 4),
    )
    missingPositions.forEach((nodeKey) => {
      const position = positions[nodeKey]
      if (position) graph.mergeNodeAttributes(nodeKey, position)
    })
  }

  return { graph, issues }
}
