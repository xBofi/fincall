/**
 * Títulos de nodo y condiciones de ruta en el idioma de la consola.
 *
 * El backend los exporta en el idioma del código (español, `graph/manifest.py`). Aquí se
 * traducen por id con las claves `routing.graph.*` de los catálogos; un nodo o una ruta nuevos
 * sin clave se ven con el texto del backend (y `trace.test.ts` avisa de que falta la clave).
 */
import { t } from '../../i18n'
import { es, type MessageKey } from '../../i18n/messages/es'
import type { GraphStructure, Visit } from './types'
import { edgeIdOf } from './trace'
import { edgeConditionKey, nodeTitleKey } from './labelKeys'

const has = (key: string): key is MessageKey => key in es

export const roleKey = (role: string) => `routing.role.${role}` as MessageKey
export const modeKey = (mode: string) => `routing.mode.${mode}` as MessageKey
export const originKey = (origin: string): MessageKey | null => {
  const key = `routing.decision.origin.${origin}`
  return has(key) ? key : null
}

/** Copia de la estructura con títulos y condiciones traducidos al idioma `lang` (el activo de
 * la consola: se pasa para que quien memorice el resultado lo recalcule al cambiar). */
export function localizeStructure(s: GraphStructure, lang: string): GraphStructure {
  void lang
  return {
    ...s,
    nodes: s.nodes.map((n) => {
      const key = nodeTitleKey(n.id)
      return has(key) ? { ...n, title: t(key) } : n
    }),
    edges: s.edges.map((e) => {
      const key = edgeConditionKey(e)
      return has(key) ? { ...e, condition: t(key) } : e
    }),
  }
}

/** La condición de la ruta que tomó una visita, traducida si la estructura la conoce. */
export function visitCondition(s: GraphStructure, v: Visit): string | null {
  if (!v.condition) return null
  const id = edgeIdOf(v)
  return s.edges.find((e) => e.id === id)?.condition ?? v.condition
}
