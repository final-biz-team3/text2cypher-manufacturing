import { describe, expect, it } from 'vitest'
import {
  createGraphologyGraph,
  createStableEdgeKey,
  normalizeGraphEdge,
  normalizeGraphNode,
} from './createGraphologyGraph'

describe('Neo4j API to Graphology adapter', () => {
  it('normalizes numeric and string node identifiers without mutating input', () => {
    const input = {
      elementId: 42,
      labels: ['Product'],
      properties: { productId: '680', name: 'HL Road Frame' },
    }
    const snapshot = structuredClone(input)

    const node = normalizeGraphNode(input)

    expect(node?.key).toBe('Product:42')
    expect(node?.attributes.label).toBe('HL Road Frame')
    expect(input).toEqual(snapshot)
  })

  it('normalizes a directed edge and preserves a provided relationship id', () => {
    const edge = normalizeGraphEdge({
      elementId: '4:abc:9',
      type: 'REQUIRES_COMPONENT',
      source: 'Product:680',
      target: 'Product:486',
      properties: { quantityPerAssembly: 2 },
    })

    expect(edge).toMatchObject({
      key: 'edge:4:abc:9',
      source: 'Product:680',
      target: 'Product:486',
      relationshipType: 'REQUIRES_COMPONENT',
    })
  })

  it('reads Neo4j properties returned directly in a record value', () => {
    const node = normalizeGraphNode({ productId: 956, name: 'Touring-1000' })

    expect(node?.key).toBe('Product:956')
    expect(node?.attributes.properties.productId).toBe(956)
  })

  it('resolves raw edge endpoint ids to namespaced node keys', () => {
    const result = createGraphologyGraph({
      nodes: [
        { elementId: 'node-1', labels: ['Product'], properties: { productId: 1 } },
        { elementId: 'node-2', labels: ['Product'], properties: { productId: 2 } },
      ],
      edges: [{ source: 'node-1', target: 'node-2', type: 'REQUIRES_COMPONENT' }],
    })

    expect(result.graph.hasDirectedEdge('Product:node-1', 'Product:node-2')).toBe(true)
  })

  it('keeps parallel relationships and self-loops in a MultiDirectedGraph', () => {
    const result = createGraphologyGraph({
      nodes: [
        { id: 680, labels: ['Product'], properties: { name: 'Frame' } },
        { id: 486, labels: ['Product'], properties: { name: 'Paint' } },
      ],
      relationships: [
        { source: 'Product:680', target: 'Product:486', type: 'REQUIRES_COMPONENT' },
        { source: 'Product:680', target: 'Product:486', type: 'REQUIRES_COMPONENT' },
        { source: 'Product:680', target: 'Product:680', type: 'RELATED_TO_SELF' },
      ],
    })

    expect(result.graph.type).toBe('directed')
    expect(result.graph.multi).toBe(true)
    expect(result.graph.size).toBe(3)
    expect(result.graph.selfLoopCount).toBe(1)
  })

  it('creates deterministic edge keys from occurrence indexes', () => {
    expect(createStableEdgeKey('Product:1', 'REQUIRES_COMPONENT', 'Product:2', 0)).toBe(
      'edge:Product:1:REQUIRES_COMPONENT:Product:2:0',
    )
    expect(createStableEdgeKey('Product:1', 'REQUIRES_COMPONENT', 'Product:2', 1)).toBe(
      'edge:Product:1:REQUIRES_COMPONENT:Product:2:1',
    )
  })

  it('skips edges with missing endpoints and reports the issue', () => {
    const result = createGraphologyGraph({
      nodes: [{ id: 1, labels: ['Product'], properties: {} }],
      edges: [{ source: 'Product:1', target: 'Product:2', type: 'REQUIRES_COMPONENT' }],
    })

    expect(result.graph.size).toBe(0)
    expect(result.issues).toHaveLength(1)
    expect(result.issues[0].kind).toBe('missing-endpoint')
  })

  it('rejects an edge whose source or target field is missing', () => {
    expect(normalizeGraphEdge({ target: 'Product:2', type: 'RELATED_TO' })).toBeNull()
    expect(normalizeGraphEdge({ source: 'Product:1', type: 'RELATED_TO' })).toBeNull()
  })

  it('creates valid positions for empty, single-node, and path-shaped results', () => {
    expect(createGraphologyGraph([]).graph.order).toBe(0)

    const single = createGraphologyGraph([{ productId: 680, productName: 'Frame' }]).graph
    expect(single.order).toBe(1)
    expect(Number.isFinite(single.getNodeAttribute('Product:680', 'x'))).toBe(true)
    expect(Number.isFinite(single.getNodeAttribute('Product:680', 'y'))).toBe(true)

    const path = createGraphologyGraph([{ pathProductIds: [680, '486', 707] }]).graph
    expect(path.order).toBe(3)
    expect(path.size).toBe(2)
    path.forEachNode((_node, attributes) => {
      expect(Number.isFinite(attributes.x)).toBe(true)
      expect(Number.isFinite(attributes.y)).toBe(true)
    })
  })
})
