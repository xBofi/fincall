/**
 * Rutas de salida de un nodo, numeradas igual en el lienzo y en el panel lateral.
 *
 * Al seleccionar un nodo el lienzo no escribe las condiciones encima de las flechas (con el
 * reparto, una docena de textos largos caían debajo de otros nodos y unos encima de otros): marca
 * cada ruta con un número pequeño junto a la entrada de su destino y el panel «Nodo» lista esas
 * mismas rutas con el número y la condición completa.
 */
import type { EdgeKind, GraphStructure, StructureEdge } from './types.ts'
import type { Placed } from './layoutCache.ts'

/** Rutas que se dibujan desde `nodeId`, en el orden del manifiesto. Los reintentos (vuelven al
 * mismo nodo) no tienen flecha: se marcan con ↻ en la caja. */
export function outgoingRoutes(s: GraphStructure, nodeId: string): StructureEdge[] {
  return s.edges.filter((e) => e.source === nodeId && e.kind !== 'retry' && e.target !== nodeId)
}

/** Número (desde 1) de cada ruta de salida de `nodeId`, por id de arista. */
export function routeNumbers(s: GraphStructure, nodeId: string | null): Map<string, number> {
  if (!nodeId) return new Map()
  return new Map(outgoingRoutes(s, nodeId).map((e, i) => [e.id, i + 1]))
}

export type Side = 'left' | 'right' | 'top' | 'bottom'

/** Qué asas usa una arista según su tipo y dónde quedaron sus extremos. */
export function handlesFor(kind: EdgeKind, from?: Placed, to?: Placed, rtl = false): { sourceHandle: string; targetHandle: string } {
  if (kind === 'return') return { sourceHandle: 'back', targetHandle: 'loopin' }
  if (from && to) {
    const backwards = rtl ? to.x > from.x + from.width : to.x + to.width < from.x
    // El destino quedó en una fila de abajo y por detrás: el grafo se plegó aquí
    if (backwards && to.y > from.y + from.height) return { sourceHandle: 'down', targetHandle: 'up' }
  }
  return { sourceHandle: 'out', targetHandle: 'in' }
}

/** Punto y lado por el que una flecha entra en su destino (las asas de `RoutingNode`). */
export function targetPoint(handle: string, to: Placed, rtl = false): { x: number; y: number; side: Side } {
  const inSide: Side = rtl ? 'right' : 'left'
  const outSide: Side = rtl ? 'left' : 'right'
  if (handle === 'up') return { x: to.x + to.width / 2, y: to.y, side: 'top' }
  const side = handle === 'loopin' ? outSide : inSide
  return { x: side === 'left' ? to.x : to.x + to.width, y: to.y + to.height / 2, side }
}

/** Distancia del centro de la marca al borde del destino: cabe en el hueco entre capas (52 px)
 * y entre filas (16 px + media marca) sin tocar otra caja. */
export const BADGE_GAP = 13

/**
 * Dónde va la marca de una ruta: justo fuera del lado por el que la flecha entra en su destino.
 * Como cada ruta de un mismo nodo llega a un destino distinto (el id es `origen->destino`), las
 * marcas nunca se pisan entre sí, y al quedar en el hueco junto al asa no tapan ninguna caja.
 */
export function badgeAnchor(x: number, y: number, side: Side): { x: number; y: number } {
  switch (side) {
    case 'left': return { x: x - BADGE_GAP, y }
    case 'right': return { x: x + BADGE_GAP, y }
    case 'top': return { x, y: y - BADGE_GAP }
    case 'bottom': return { x, y: y + BADGE_GAP }
  }
}
