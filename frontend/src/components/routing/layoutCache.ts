/**
 * Lo que decide dónde cae cada caja (tamaños, aristas que cuentan para las capas) y la caché de
 * disposiciones ya calculadas. Sin ELK: se importa desde los tests de Node.
 */
import type { GraphStructure } from './types.ts'

export const NODE_WIDTH = 188
export const NODE_HEIGHT = 66
export const SMALL = { width: 112, height: 38 }

export interface Placed {
  x: number
  y: number
  width: number
  height: number
}

export function isTerminal(id: string, s: GraphStructure): boolean {
  return id === s.entry || id === s.end
}

/** Aristas que deciden las capas. Exportado para los tests. */
export function layoutEdges(s: GraphStructure): [string, string][] {
  const stage = new Map(s.nodes.map((n) => [n.id, n.stage]))
  const pairs: [string, string][] = [[s.entry, s.hub]]
  const globalTargets = new Set<string>()
  for (const e of s.edges) {
    if (e.kind === 'return' || e.kind === 'retry') continue
    if ((stage.get(e.target) ?? 0) < (stage.get(e.source) ?? 0)) continue
    if (e.kind === 'global') {
      // Se llega desde casi cualquier pregunta: se coloca como un desenlace más del reparto
      globalTargets.add(e.target)
      continue
    }
    pairs.push([e.source, e.target])
  }
  for (const target of globalTargets) pairs.push([s.hub, target])
  return pairs
}

/** Grafo que recibe ELK. `layout.ts` lo calcula en el worker; los tests, con ELK en Node. */
export function elkGraph(s: GraphStructure, aspectRatio: number) {
  return {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.layered.wrapping.strategy': 'MULTI_EDGE',
      'elk.aspectRatio': String(aspectRatio),
      'elk.spacing.nodeNode': '16',
      'elk.layered.spacing.nodeNodeBetweenLayers': '52',
      'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
      'elk.padding': '[top=12,left=12,bottom=12,right=12]',
    },
    children: s.nodes.map((n) => ({
      id: n.id,
      ...(isTerminal(n.id, s) ? SMALL : { width: NODE_WIDTH, height: NODE_HEIGHT }),
    })),
    edges: layoutEdges(s).map(([source, target], i) => ({ id: `l${i}`, sources: [source], targets: [target] })),
  }
}

interface ElkResult {
  children?: { id: string; x?: number; y?: number; width?: number; height?: number }[]
}

/** Cajas colocadas a partir de la respuesta de ELK; en RTL, en espejo. */
export function placedFrom(result: ElkResult, rtl: boolean): Map<string, Placed> {
  const placed = new Map<string, Placed>()
  let maxX = 0
  for (const c of result.children ?? []) {
    const p = { x: c.x ?? 0, y: c.y ?? 0, width: c.width ?? NODE_WIDTH, height: c.height ?? NODE_HEIGHT }
    maxX = Math.max(maxX, p.x + p.width)
    placed.set(c.id, p)
  }
  if (rtl) {
    // En árabe y urdu el flujo se lee de derecha a izquierda
    for (const [id, p] of placed) placed.set(id, { ...p, x: maxX - p.x - p.width })
  }
  return placed
}

// --------------------------------------------------------------------------------- caché
// La disposición solo depende de los ids, del tamaño de cada caja, de las aristas que deciden
// las capas, de la dirección y de la forma del lienzo: no del idioma ni de la traza. Se guarda
// en memoria (volver a la pestaña es instantáneo) y en localStorage (la siguiente visita también:
// sin descargar ni arrancar ELK).
const CACHE_VERSION = 1
const STORAGE_PREFIX = 'bc.routing.layout.'
const memory = new Map<string, Map<string, Placed>>()

function hash(text: string): string {
  // FNV-1a de 32 bits: basta para distinguir formas de grafo, no es criptográfico
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(36)
}

/** Clave de la caché: cambia si cambia cualquier cosa que mueva una caja. Exportada para tests. */
export function layoutKey(s: GraphStructure, rtl: boolean, aspectRatio: number): string {
  const shape = JSON.stringify([
    CACHE_VERSION,
    s.nodes.map((n) => [n.id, isTerminal(n.id, s)]),
    layoutEdges(s),
    rtl,
    aspectRatio,
    NODE_WIDTH,
    NODE_HEIGHT,
  ])
  return `${hash(shape)}${shape.length.toString(36)}`
}

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

/** Disposición ya calculada para esta forma, sin esperar: null si no hay. */
export function cachedLayout(s: GraphStructure, rtl: boolean, aspectRatio: number): Map<string, Placed> | null {
  const key = layoutKey(s, rtl, aspectRatio)
  const hit = memory.get(key)
  if (hit) return hit
  try {
    const raw = storage()?.getItem(STORAGE_PREFIX + key)
    if (!raw) return null
    const entries = JSON.parse(raw) as [string, Placed][]
    // Si el grafo trae un nodo que la copia no tiene, mejor recalcular que dibujarlo en (0, 0)
    if (!s.nodes.every((n) => entries.some(([id]) => id === n.id))) return null
    const placed = new Map(entries)
    memory.set(key, placed)
    return placed
  } catch {
    return null
  }
}

export function rememberLayout(key: string, placed: Map<string, Placed>): void {
  memory.set(key, placed)
  try {
    const store = storage()
    if (!store) return
    // Solo unas pocas formas (pantalla Flujo, columna del simulador, RTL): se poda lo viejo
    const old = Object.keys(store).filter((k) => k.startsWith(STORAGE_PREFIX) && k !== STORAGE_PREFIX + key)
    for (const k of old.slice(0, Math.max(0, old.length - 7))) store.removeItem(k)
    store.setItem(STORAGE_PREFIX + key, JSON.stringify([...placed]))
  } catch {
    // Sin almacenamiento (modo privado, cuota llena): queda la copia en memoria
  }
}

