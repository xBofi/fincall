/** Claves de catálogo de los títulos de nodo y las condiciones de ruta. Sin dependencias, para
 * que los tests las usen con `node --test`. */
import type { StructureEdge } from './types.ts'

export function nodeTitleKey(id: string): string {
  return `routing.graph.node.${id}`
}

/** Las salidas globales (derivar, despedida) comparten un texto por destino. */
export function edgeConditionKey(edge: Pick<StructureEdge, 'id' | 'kind' | 'target'>): string {
  return edge.kind === 'global' ? `routing.graph.global.${edge.target}` : `routing.graph.edge.${edge.id}`
}
