/**
 * Disposición automática del grafo exportado con ELK (layered, de izquierda a derecha, con
 * plegado en filas para que quepa en el lienzo sin encoger el texto).
 *
 * El grafo real es una estrella: casi todos los nodos vuelven a `next_step` y muchos pueden
 * salir a «derivar» o «despedida». Si esas aristas entraran en el cálculo, ELK las trataría como
 * ciclos y enredaría las capas. Se colocan solo con las rutas de avance (y las correcciones que
 * vuelven a una etapa anterior tampoco cuentan); las vueltas al reparto y las salidas globales
 * se dibujan igual, por debajo y atenuadas. Ninguna coordenada está escrita a mano.
 */
import ELK from 'elkjs/lib/elk.bundled.js'
import type { GraphStructure } from './types.ts'

export const NODE_WIDTH = 188
export const NODE_HEIGHT = 66
const SMALL = { width: 112, height: 38 }

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

let elk: InstanceType<typeof ELK> | null = null

export async function layOut(s: GraphStructure, rtl = false, aspectRatio = 1.6): Promise<Map<string, Placed>> {
  elk ??= new ELK()
  const result = await elk.layout({
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
  })
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
