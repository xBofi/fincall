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
// ELK corre en un web worker propio (minificado y fuera del hilo principal): ni el paquete de la
// consola carga su código ni el cálculo bloquea la interfaz
import ELK from 'elkjs/lib/elk-api.js'
import elkWorkerUrl from 'elkjs/lib/elk-worker.min.js?url'
import type { GraphStructure } from './types.ts'
import { NODE_HEIGHT, NODE_WIDTH, cachedLayout, elkGraph, isTerminal, layoutEdges, layoutKey, placedFrom, rememberLayout, type Placed } from './layoutCache.ts'

export { NODE_HEIGHT, NODE_WIDTH, cachedLayout, isTerminal, layoutEdges, type Placed }

let elk: InstanceType<typeof ELK> | null = null

/** Arranca el worker de ELK sin esperar a que se pida una disposición (precarga). */
export function warmLayoutEngine(): void {
  elk ??= new ELK({ workerUrl: elkWorkerUrl })
}

export async function layOut(s: GraphStructure, rtl = false, aspectRatio = 1.6): Promise<Map<string, Placed>> {
  const key = layoutKey(s, rtl, aspectRatio)
  const hit = cachedLayout(s, rtl, aspectRatio)
  if (hit) return hit
  warmLayoutEngine()
  const placed = placedFrom(await elk!.layout(elkGraph(s, aspectRatio)), rtl)
  rememberLayout(key, placed)
  return placed
}
