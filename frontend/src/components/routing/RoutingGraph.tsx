import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  getBezierPath,
  MarkerType,
  useNodesInitialized,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  ReactFlowProvider,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { EdgeKind, GraphStructure, StructureNode } from './types'
import type { ViewAt } from './trace'
import { cachedLayout, isTerminal, layOut, type Placed } from './layout'
import { t } from '../../i18n'
import { roleKey } from './labels'
import { ROLE_ICON } from './roleIcons'
import { badgeAnchor, handlesFor, routeNumbers, type Side } from './routes'
import { RoutingErrorBoundary } from './RoutingErrorBoundary'

type EdgeState = 'idle' | 'taken' | 'turn' | 'current'

interface NodeData extends Record<string, unknown> {
  node: StructureNode
  terminal: boolean
  visits: number
  lastMs: number | null
  current: boolean
  running: boolean
  waiting: boolean
  retried: boolean
  canRetry: boolean
  dim: boolean
  /** Hay un nodo seleccionado y este no es ni él ni un vecino directo. */
  faded: boolean
  rtl: boolean
}

interface EdgeData extends Record<string, unknown> {
  state: EdgeState
  condition: string
  width: number
  label: boolean
  loop: boolean
  /** Número de la ruta cuando sale del nodo seleccionado (el mismo que en el panel). */
  number: number | null
  /** Relación con el nodo seleccionado. */
  focus: 'out' | 'in' | 'faded' | null
  hovered: boolean
}

const fmtMs = (ms: number) => (ms >= 100 ? Math.round(ms).toLocaleString() : ms.toFixed(ms < 1 ? 1 : 0))

const RoutingNode = memo(function RoutingNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const { node, terminal, rtl } = data
  const inSide = rtl ? Position.Right : Position.Left
  const outSide = rtl ? Position.Left : Position.Right
  const cls = [
    'rv-node',
    `rv-role-${node.role}`,
    terminal ? 'is-terminal' : '',
    data.current ? 'is-current' : '',
    data.running ? 'is-running' : '',
    data.waiting ? 'is-waiting' : '',
    data.visits > 0 ? 'is-visited' : '',
    data.dim ? 'is-dim' : '',
    data.faded ? 'is-faded' : '',
    selected ? 'is-selected' : '',
  ].join(' ')
  return (
    <div className={cls} dir={rtl ? 'rtl' : undefined}>
      <Handle type="target" position={inSide} id="in" className="rv-handle" isConnectable={false} />
      <Handle type="source" position={outSide} id="out" className="rv-handle" isConnectable={false} />
      {/* Vueltas al reparto: salen por la entrada del nodo y llegan por la salida del reparto */}
      <Handle type="source" position={inSide} id="back" className="rv-handle" isConnectable={false} />
      <Handle type="target" position={outSide} id="loopin" className="rv-handle" isConnectable={false} />
      {/* Saltos a la fila siguiente cuando ELK pliega el grafo: de abajo a arriba */}
      <Handle type="source" position={Position.Bottom} id="down" className="rv-handle" isConnectable={false} />
      <Handle type="target" position={Position.Top} id="up" className="rv-handle" isConnectable={false} />
      {terminal ? (
        <div className="rv-node-pill">
          <span className="rv-node-icon" aria-hidden>{ROLE_ICON[node.role]}</span>
          <span>{node.role === 'entry' ? t('routing.role.entry') : t('routing.node.end')}</span>
        </div>
      ) : (
        <>
          <div className="rv-node-top">
            <span className="rv-node-icon" aria-hidden>{ROLE_ICON[node.role]}</span>
            <span className="rv-node-role">{t(roleKey(node.role))}</span>
            {data.canRetry && (
              <span className={`rv-node-retry ${data.retried ? 'is-on' : ''}`} title={t('routing.legend.retry')} aria-hidden>↻</span>
            )}
            {data.visits > 0 && <span className="rv-node-visits">{t('routing.node.visits', { n: data.visits })}</span>}
          </div>
          <div className="rv-node-title">{node.title}</div>
          <div className="rv-node-meta">
            <span className="rv-mono">{node.id}</span>
            {data.running ? (
              <span className="rv-node-status">{t('routing.node.running')}</span>
            ) : data.waiting ? (
              <span className="rv-node-status">{t('routing.node.waiting')}</span>
            ) : data.lastMs !== null ? (
              <span className="rv-mono">{t('routing.node.ms', { ms: fmtMs(data.lastMs) })}</span>
            ) : null}
          </div>
        </>
      )}
    </div>
  )
})

const RoutingEdge = memo(function RoutingEdge(props: EdgeProps<Edge<EdgeData>>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, id } = props
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  const state = data?.state ?? 'idle'
  const focus = data?.focus ? `is-${data.focus}` : ''
  const condition = data?.condition ?? ''
  const badge = data?.number ? badgeAnchor(targetX, targetY, targetPosition as Side) : null
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        className={`rv-edge is-${state} ${data?.loop ? 'is-loop' : ''} ${focus} ${data?.hovered ? 'is-hovered' : ''}`}
        style={{ strokeWidth: (data?.width ?? 1.4) + (data?.hovered ? 1.2 : 0) }}
      />
      {/* Las etiquetas van por encima de las cajas (zIndex) y nunca en mitad de la flecha: con el
          nodo seleccionado, un número junto a la entrada de cada destino; la condición completa
          está en el panel y aparece aquí al pasar el ratón (por el número o por su fila). */}
      {badge && (
        <EdgeLabelRenderer>
          <div
            className={`rv-edge-badge side-${targetPosition} ${data?.hovered ? 'is-hovered' : ''} nodrag nopan`}
            style={{ transform: `translate(-50%, -50%) translate(${badge.x}px, ${badge.y}px)`, zIndex: data?.hovered ? 2200 : 2100 }}
            data-edge={id}
            aria-label={condition}
          >
            <span className="rv-edge-badge-n">{data?.number}</span>
            <span className="rv-edge-badge-text" dir="auto">{condition}</span>
          </div>
        </EdgeLabelRenderer>
      )}
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            className={`rv-edge-label is-${state} nodrag nopan`}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, zIndex: 2000 }}
            title={condition}
            dir="auto"
          >
            {condition.length > 56 ? `${condition.slice(0, 55)}…` : condition}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
})

/** El zoom del lienzo como variable CSS (`--rv-zoom`): las marcas y la condición al pasar el
 * ratón se compensan para seguir legibles con el grafo entero encajado (zoom < 1). Escribe en el
 * DOM sin volver a pintar el lienzo. */
function ZoomVar() {
  const zoom = useStore((st) => st.transform[2])
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    ref.current?.closest<HTMLElement>('.rv-canvas')?.style.setProperty('--rv-zoom', zoom.toFixed(3))
  }, [zoom])
  return <span ref={ref} hidden />
}

const nodeTypes = { routing: RoutingNode }
const edgeTypes = { routing: RoutingEdge }

export interface RoutingGraphProps {
  structure: GraphStructure
  view: ViewAt | null
  running?: string | null
  selected: string | null
  onSelect: (id: string | null) => void
  compact?: boolean
  rtl?: boolean
  dark?: boolean
  /** Ruta resaltada desde el panel lateral (id de arista). */
  hoveredEdge?: string | null
}

function Canvas({ structure, view, running, selected, onSelect, compact, rtl = false, dark = false, hoveredEdge = null, aspect, size }: RoutingGraphProps & { aspect: number | null; size: string }) {
  const [layout, setLayout] = useState<{ key: GraphStructure; rtl: boolean; aspect: number; placed: Map<string, Placed> } | null>(null)
  // Una forma ya calculada (en esta visita o en una anterior) se dibuja en el primer render, sin ELK
  const cached = useMemo(() => (aspect === null ? null : cachedLayout(structure, rtl, aspect)), [structure, rtl, aspect])
  useEffect(() => {
    if (aspect === null || cached) return
    let stale = false
    void layOut(structure, rtl, aspect).then((placed) => { if (!stale) setLayout({ key: structure, rtl, aspect, placed }) })
    return () => { stale = true }
  }, [structure, rtl, aspect, cached])
  // Mientras se recalcula (otra forma de lienzo) se sigue viendo la disposición anterior
  const placed = cached ?? (layout && layout.key === structure && layout.rtl === rtl ? layout.placed : null)
  const layoutAspect = cached ? aspect : layout?.aspect
  const retryable = useMemo(
    () => new Set(structure.edges.filter((e) => e.kind === 'retry').map((e) => e.source)),
    [structure],
  )
  const tracing = view !== null
  const turnEdges = useMemo(() => new Set(view?.turnEdges ?? []), [view])
  // Rutas del nodo seleccionado (numeradas como en el panel) y sus vecinos directos
  const numbers = useMemo(() => (compact ? new Map<string, number>() : routeNumbers(structure, selected)), [structure, selected, compact])
  const related = useMemo(() => {
    if (!selected) return null
    const ids = new Set([selected])
    for (const e of structure.edges) {
      if (e.source === selected) ids.add(e.target)
      if (e.target === selected) ids.add(e.source)
    }
    if (selected === structure.hub) ids.add(structure.entry)
    if (selected === structure.entry) ids.add(structure.hub)
    return ids
  }, [structure, selected])

  const nodes = useMemo<Node<NodeData>[]>(() => !placed ? [] : structure.nodes.map((n) => {
    const p = placed.get(n.id) ?? { x: 0, y: 0, width: 120, height: 40 }
    const visits = view?.visits.get(n.id) ?? 0
    const isCurrent = view?.node === n.id
    return {
      id: n.id,
      type: 'routing',
      position: { x: p.x, y: p.y },
      width: p.width,
      height: p.height,
      selected: selected === n.id,
      draggable: false,
      connectable: false,
      ariaLabel: t('routing.node.aria', { title: n.title, role: t(roleKey(n.role)) }),
      data: {
        node: n,
        terminal: isTerminal(n.id, structure),
        visits,
        lastMs: view?.lastMs.get(n.id) ?? null,
        current: isCurrent,
        running: Boolean(running && running === n.id),
        waiting: Boolean(isCurrent && view?.waiting),
        retried: Boolean(view?.retried.has(n.id)),
        canRetry: retryable.has(n.id),
        dim: tracing && visits === 0 && !isCurrent && n.id !== structure.entry,
        faded: !tracing && related !== null && !related.has(n.id),
        rtl,
      },
    }
  }), [structure, placed, view, running, selected, retryable, tracing, rtl, related])

  const edges = useMemo<Edge<EdgeData>[]>(() => {
    const maxTimes = Math.max(1, ...structure.edges.map((e) => e.times ?? 0))
    const list: Edge<EdgeData>[] = []
    const entryId = `${structure.entry}->${structure.hub}`
    const routes: { id: string; source: string; target: string; condition: string; kind: EdgeKind; times?: number }[] = [
      { id: entryId, source: structure.entry, target: structure.hub, condition: t('routing.entry.edge', { router: structure.routers.entry ?? '' }), kind: 'forward', times: 0 },
      ...structure.edges.filter((e) => e.kind !== 'retry'),
    ]
    // El turno puede retomar en cualquier nodo: esa entrada se dibuja solo cuando ocurre
    if (view?.entryEdge && view.entryEdge !== entryId) {
      const target = view.entryEdge.split('->')[1]
      routes.push({ id: view.entryEdge, source: structure.entry, target, condition: t('routing.entry.edge', { router: structure.routers.entry ?? '' }), kind: 'global', times: 0 })
    }
    for (const e of routes) {
      const taken = view?.taken.get(e.id) ?? 0
      const state: EdgeState = view?.edge === e.id
        ? 'current'
        : turnEdges.has(e.id) ? 'turn' : taken > 0 ? 'taken' : 'idle'
      const touchesSelection = selected !== null && (e.source === selected || e.target === selected)
      const secondary = e.kind === 'return' || e.kind === 'global'
      const hidden = secondary && state === 'idle' && !touchesSelection
      const loop = secondary
      const width = tracing
        ? state === 'idle' ? 1.2 : 1.6 + Math.min(taken, 6) * 0.45
        : 1.2 + ((e.times ?? 0) / maxTimes) * 2.6
      list.push({
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'routing',
        hidden,
        ...handlesFor(e.kind, placed?.get(e.source), placed?.get(e.target), rtl),
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        zIndex: state === 'current' ? 10 : state === 'turn' ? 5 : 0,
        data: {
          state,
          condition: e.condition,
          width,
          loop,
          label: !compact && !loop && state === 'current',
          number: numbers.get(e.id) ?? null,
          focus: selected === null ? null : e.source === selected ? 'out' : e.target === selected ? 'in' : tracing ? null : 'faded',
          hovered: hoveredEdge === e.id,
        },
      })
    }
    return list
  }, [structure, view, selected, turnEdges, tracing, compact, placed, rtl, numbers, hoveredEdge])

  // La selección la decide solo esta vista (clic, o Intro/Espacio sobre un nodo con el foco).
  // Antes también la movía `onSelectionChange` de React Flow: su almacén aún tenía el nodo
  // anterior cuando llegaba el nuevo, cada lado devolvía al otro su nodo y React cortaba con
  // «Maximum update depth exceeded»: la pantalla en blanco al pulsar varios nodos seguidos.
  const onKeyDown = useCallback((ev: React.KeyboardEvent) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return
    const id = (ev.target as HTMLElement).closest('.react-flow__node')?.getAttribute('data-id')
    if (!id) return
    ev.preventDefault()
    onSelect(id)
  }, [onSelect])

  // Ajustar al tamaño solo cuando cambia la estructura o el contenedor: nunca en cada paso
  // Encajar cuando los nodos están medidos y cada vez que cambia la disposición o el tamaño del
  // lienzo; nunca en cada paso de la traza (la cámara no salta mientras se reproduce)
  const { fitView } = useReactFlow()
  const initialized = useNodesInitialized()
  const fitted = useRef<string | null>(null)
  useEffect(() => {
    if (!placed || !initialized) return
    const key = `${layoutAspect}:${rtl}:${size}:${structure.nodes.length}`
    if (fitted.current === key) return
    const first = fitted.current === null
    fitted.current = key
    void fitView({ padding: 0.04, duration: first ? 0 : 200 })
  }, [placed, initialized, layoutAspect, structure, rtl, size, fitView])

  return (
    <>
    {!placed && (
      // Esqueleto ligero mientras ELK coloca los nodos (solo la primera vez: luego hay caché)
      <div className="rv-canvas-skeleton" aria-busy="true" role="status">
        <div className="rv-skel-row">{[0, 1, 2, 3].map((i) => <span key={i} className="rv-skel-box" />)}</div>
        <div className="rv-skel-row">{[0, 1, 2].map((i) => <span key={i} className="rv-skel-box" />)}</div>
        <span className="rv-muted rv-small">{t('routing.layout')}</span>
      </div>
    )}
    <ReactFlow
      nodes={nodes}
      edges={placed ? edges : []}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodeClick={(_, n) => onSelect(n.id)}
      onPaneClick={() => onSelect(null)}
      onKeyDown={onKeyDown}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      edgesFocusable={false}
      fitView
      fitViewOptions={{ padding: 0.04 }}
      minZoom={0.2}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      colorMode={dark ? 'dark' : 'light'}
      aria-label={t('routing.canvas')}
    >
      <ZoomVar />
      <Background gap={22} size={1} />
      <Controls showInteractive={false} position={rtl ? 'bottom-right' : 'bottom-left'} />
      {!compact && (
        <MiniMap
          style={{ width: 150, height: 96 }}
          pannable
          zoomable
          ariaLabel={t('routing.minimap')}
          position={rtl ? 'bottom-left' : 'bottom-right'}
          nodeClassName={(n) => `rv-mini rv-role-${(n.data as NodeData).node.role}`}
        />
      )}
    </ReactFlow>
    </>
  )
}

export function RoutingGraph(props: RoutingGraphProps) {
  // La forma del lienzo decide cómo pliega ELK: ancho en la pantalla Flujo, alto en la columna
  const ref = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<{ w: number; h: number } | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) setBox({ w: Math.round(width / 40), h: Math.round(height / 40) })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])
  const aspect = box ? Math.min(3, Math.max(0.5, Math.round((box.w / box.h) * 4) / 4)) : null
  return (
    <div ref={ref} className={`rv-canvas ${props.compact ? 'is-compact' : ''}`} dir="ltr">
      <RoutingErrorBoundary compact onReset={() => props.onSelect(null)}>
        <ReactFlowProvider>
          <Canvas {...props} aspect={aspect} size={box ? `${box.w}x${box.h}` : ''} />
        </ReactFlowProvider>
      </RoutingErrorBoundary>
    </div>
  )
}
