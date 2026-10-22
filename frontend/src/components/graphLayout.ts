import type { GraphEdge, Graph, GraphNode } from '../api'

/**
 * Disposición del grafo del asistente.
 *
 * El flujo es una estrella: `siguiente_paso` reparte cada pregunta y casi todas vuelven a él.
 * Dibujarlo como capas con curvas da una maraña de ~30 aristas cruzando bloques. Aquí:
 *
 * - el nodo de entrada es un **eje** vertical a la izquierda; cada pregunta se une a él con una
 *   línea horizontal corta (flecha doble si además vuelve),
 * - las preguntas y efectos van en una **columna principal**, en el orden de etapas,
 * - las salidas de error se ponen **al lado** del paso que las provoca,
 * - las **salidas globales** (a las que se llega desde muchos pasos) se agrupan aparte y sus
 *   aristas solo se dibujan cuando hay un bloque seleccionado que las use,
 * - repetir la misma pregunta no es una arista: es una marca ↻ en el bloque.
 */

const NODE_WIDTH = 196
const NODE_HEIGHT = 50
const MARGIN = 24
const AXIS_WIDTH = 156
const AXIS_GAP = 64
const ROW_GAP = 26
const SIDE_GAP = 104
const LANE_1 = 22
const LANE_2 = 46
const RADIUS = 9

/** Salidas de error: se dibujan junto al paso que las provoca. Presentación, no lógica. */
const LATERAL: Record<string, string> = {
  estate_not_identified: 'identify_estate',
  home_not_identified: 'identify_home',
  unclassifiable: 'validate_data',
}

/** A partir de cuántos orígenes distintos una salida se considera global. */
const MIN_EXIT_SOURCES = 3

export type Column = 'axis' | 'principal' | 'side' | 'output'

export interface Box {
  node: GraphNode
  x: number
  y: number
  width: number
  high: number
  column: Column
  row: number
  /** Cuántos pasos distintos llevan a una salida global. */
  origins: number
}

export interface Stroke {
  key: string
  d: string
  /** Punta al final (hacia el destino) y/o al principio (vuelta al eje). */
  final: boolean
  start: boolean
  times: number
  origin: string
  target: string
  title: string
  /** Solo se dibuja con un extremo seleccionado. */
  activeOnly: boolean
}

export interface MarkerEnd {
  x: number
  y: number
  origin: string
}

export interface Layout {
  boxes: Map<string, Box>
  strokes: Stroke[]
  /** Pastilla "Fin de la llamada" bajo el último paso principal. */
  end: { x: number; y: number; origin: string } | null
  /** Pequeñas marcas de fin junto a los bloques que cierran la llamada. */
  finals: MarkerEnd[]
  /** Bloques que pueden repetirse a sí mismos. */
  repeated: Set<string>
  exitsLabel: { x: number; y: number } | null
  width: number
  high: number
}

const midpoint = (c: Box) => c.y + c.high / 2
const derecha = (c: Box) => c.x + c.width

/** Polilínea ortogonal con esquinas redondeadas. */
function elbow(p: [number, number][], r = RADIUS): string {
  let d = `M ${p[0][0]} ${p[0][1]}`
  for (let i = 1; i < p.length - 1; i++) {
    const [x0, y0] = p[i - 1]
    const [x1, y1] = p[i]
    const [x2, y2] = p[i + 1]
    const l1 = Math.hypot(x1 - x0, y1 - y0) || 1
    const l2 = Math.hypot(x2 - x1, y2 - y1) || 1
    const k1 = Math.min(r, l1 / 2) / l1
    const k2 = Math.min(r, l2 / 2) / l2
    d += ` L ${x1 - (x1 - x0) * k1} ${y1 - (y1 - y0) * k1} Q ${x1} ${y1} ${x1 + (x2 - x1) * k2} ${y1 + (y2 - y1) * k2}`
  }
  const u = p[p.length - 1]
  return `${d} L ${u[0]} ${u[1]}`
}

export function layOut(g: Graph): Layout {
  const utterance = g.utterance
  const existingIds = new Set(g.nodes.map((n) => n.id))

  // Orígenes distintos (sin contar el eje ni lazos) de cada nodo
  const origins = new Map<string, Set<string>>()
  for (const a of g.edges) {
    if (a.origin === a.target || a.origin === utterance) continue
    if (!origins.has(a.target)) origins.set(a.target, new Set())
    origins.get(a.target)!.add(a.origin)
  }
  const isLateral = (n: GraphNode) => n.id in LATERAL && existingIds.has(LATERAL[n.id])
  const isExit = (n: GraphNode) =>
    n.id !== utterance && !isLateral(n) && (origins.get(n.id)?.size ?? 0) >= MIN_EXIT_SOURCES

  // Orden estable: etapa y, dentro de la etapa, el orden del manifiesto
  const order = [...g.nodes].sort((a, b) => a.stage - b.stage)
  const mainNodes = order.filter((n) => n.id !== utterance && !isLateral(n) && !isExit(n))
  const exits = order.filter(isExit)

  const xAxis = MARGIN
  const xMain = MARGIN + AXIS_WIDTH + AXIS_GAP
  const xLat = xMain + NODE_WIDTH + SIDE_GAP
  const yRow = (i: number) => MARGIN + i * (NODE_HEIGHT + ROW_GAP)

  const boxes = new Map<string, Box>()
  const box = (node: GraphNode, x: number, row: number, column: Column): Box => ({
    node,
    x,
    y: yRow(row),
    width: NODE_WIDTH,
    high: NODE_HEIGHT,
    column,
    row,
    origins: origins.get(node.id)?.size ?? 0,
  })

  mainNodes.forEach((n, i) => boxes.set(n.id, box(n, xMain, i, 'principal')))
  let lastLateral = -1
  for (const n of order.filter(isLateral)) {
    const anchor = boxes.get(LATERAL[n.id])!
    boxes.set(n.id, box(n, xLat, anchor.row, 'side'))
    lastLateral = Math.max(lastLateral, anchor.row)
  }
  // Una fila libre para el rótulo y, debajo, las salidas globales
  const firstExit = lastLateral + 2
  exits.forEach((n, i) => boxes.set(n.id, box(n, xLat, firstExit + i, 'output')))

  // El eje abarca las filas de los pasos principales a los que reparte
  const axisNode = g.nodes.find((n) => n.id === utterance)
  const axisRows = g.edges
    .filter((a) => a.origin === utterance)
    .map((a) => boxes.get(a.target))
    .filter((c): c is Box => c?.column === 'principal')
    .map((c) => c.row)
  const axisEnd = axisRows.length ? Math.max(...axisRows) : 0
  if (axisNode) {
    boxes.set(utterance, {
      ...box(axisNode, xAxis, 0, 'axis'),
      width: AXIS_WIDTH,
      high: yRow(axisEnd) + NODE_HEIGHT - yRow(0),
    })
  }

  const last = mainNodes.at(-1)
  const end =
    last && g.edges.some((a) => a.origin === last.id && !existingIds.has(a.target))
      ? { x: xMain + NODE_WIDTH / 2, y: yRow(mainNodes.length) - ROW_GAP / 2 + 14, origin: last.id }
      : null

  const strokes: Stroke[] = []
  const finals: MarkerEnd[] = []
  const repeated = new Set<string>()
  const axis = boxes.get(utterance)
  const title = (a: GraphEdge) =>
    `${a.origin} → ${a.target}\n${a.condition}\nRecorrida ${a.times} ${a.times === 1 ? 'vez' : 'veces'}`

  // Eje ↔ principal: una sola línea por paso, con punta en cada sentido que exista
  if (axis) {
    for (const c of boxes.values()) {
      if (c.column !== 'principal') continue
      const outbound = g.edges.find((a) => a.origin === utterance && a.target === c.node.id)
      const vuelta = g.edges.find((a) => a.origin === c.node.id && a.target === utterance)
      if (!outbound && !vuelta) continue
      const y = midpoint(c)
      strokes.push({
        key: `eje-${c.node.id}`,
        d: `M ${derecha(axis)} ${y} L ${c.x} ${y}`,
        final: !!outbound,
        start: !!vuelta,
        times: Math.max(outbound?.times ?? 0, vuelta?.times ?? 0),
        origin: utterance,
        target: c.node.id,
        title: [outbound, vuelta].filter((a): a is GraphEdge => !!a).map(title).join('\n\n'),
        activeOnly: false,
      })
    }
  }

  for (const a of g.edges) {
    const o = boxes.get(a.origin)
    if (!o) continue
    if (a.origin === a.target) {
      repeated.add(a.origin)
      continue
    }
    const d = boxes.get(a.target)
    if (!d) {
      // Hacia el fin de la llamada
      if (end && end.origin === a.origin) {
        strokes.push({ key: `${a.origin}->fin`, d: `M ${o.x + o.width / 2} ${o.y + o.high} L ${end.x} ${end.y - 11}`, final: true, start: false, times: a.times, origin: a.origin, target: 'end', title: title(a), activeOnly: false })
      } else {
        finals.push({ x: derecha(o) + 16, y: midpoint(o), origin: a.origin })
      }
      continue
    }
    if (o.column === 'axis' && d.column === 'principal') continue
    if (d.column === 'axis') continue // vueltas: ya dibujadas como punta inicial

    const base = { key: `${a.origin}->${a.target}`, final: true, start: false, times: a.times, origin: a.origin, target: a.target, title: title(a) }
    let points: [number, number][]
    let activeOnly = false

    if (o.column === 'axis' && axis) {
      // Del eje a una salida lateral: por el pasillo bajo la fila del ancla y entrando por abajo
      const yc = d.y + d.high + ROW_GAP / 2
      const xd = d.x + d.width / 2
      const axisBottom = axis.y + axis.high
      points =
        yc <= axisBottom
          ? [[derecha(axis), yc], [xd, yc], [xd, d.y + d.high]]
          : [[axis.x + axis.width / 2, axisBottom], [axis.x + axis.width / 2, yc], [xd, yc], [xd, d.y + d.high]]
    } else if (d.column === 'output') {
      // Salida global: por el carril exterior; solo con un extremo seleccionado
      const xc = derecha(o) + LANE_2
      points = [[derecha(o), midpoint(o) + 8], [xc, midpoint(o) + 8], [xc, midpoint(d)], [d.x, midpoint(d)]]
      activeOnly = true
    } else if (o.column === 'principal' && d.column === 'principal' && d.row === o.row + 1) {
      points = [[o.x + o.width / 2, o.y + o.high], [d.x + d.width / 2, d.y]]
    } else if (o.column === 'principal' && d.column === 'principal') {
      // Salto hacia atrás o por encima de otros pasos: por el carril interior, entrando por la derecha
      const xc = derecha(o) + LANE_1
      points = [[derecha(o), midpoint(o) - 8], [xc, midpoint(o) - 8], [xc, midpoint(d) + 8], [derecha(d), midpoint(d) + 8]]
    } else {
      // Mismo nivel (paso → su salida lateral)
      points = [[derecha(o), midpoint(o)], [d.x, midpoint(d)]]
    }
    strokes.push({ ...base, d: elbow(points), activeOnly })
  }

  const rows = Math.max(mainNodes.length + (end ? 1 : 0), firstExit + exits.length)
  return {
    boxes,
    strokes,
    end,
    finals,
    repeated,
    exitsLabel: exits.length ? { x: xLat, y: yRow(firstExit) - 10 } : null,
    width: xLat + NODE_WIDTH + 40,
    high: yRow(rows) - ROW_GAP + MARGIN,
  }
}
