/**
 * Etiquetas legibles para lo que el backend devuelve como códigos.
 *
 * La consola se enseña a clientes: ningún código interno (`incidencia_creada`, `fontaneria`) debe
 * verse tal cual. Un código sin traducción se capitaliza, nunca se oculta.
 */

import { t } from './i18n'
import type { MessageKey } from './i18n/messages/es'

export function capitalize(code: string): string {
  const text = code.replace(/_/g, ' ').trim()
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** A translated label for `prefix.code`, or the code capitalized: never a raw internal code. */
function label(prefix: string, code: string): string {
  const key = `${prefix}.${code}` as MessageKey
  const text = t(key)
  return text === key ? capitalize(code) : text
}

export const category = (c: string | null | undefined): string => (c ? label('category', c) : '—')

const URGENCY_COLOR: Record<string, string> = {
  low: 'green',
  medium: 'gold',
  high: 'volcano',
  emergency: 'red',
}

export const urgency = (u: string | null | undefined) =>
  u ? { text: label('urgency', u), color: URGENCY_COLOR[u] ?? 'default' } : null

export type Tone = 'good' | 'review' | 'neutral'

const RESULT_TONE: Record<string, Tone> = {
  incident_created: 'good',
  estate_not_identified: 'review',
  home_not_identified: 'review',
  unclassifiable: 'review',
  handed_to_administrator: 'review',
  abandoned: 'neutral',
}

export const result = (r: string | null | undefined) => {
  if (!r) return null
  const tone = RESULT_TONE[r] ?? ('neutral' as Tone)
  return { short: label('result', r), long: r in RESULT_TONE ? t(`result.${r}.long` as MessageKey) : capitalize(r), tone }
}

export const channel = (c: string | null | undefined): string => (c ? label('channel', c).toLowerCase() : '—')

/** Destino de una regla de aviso, ya con su preposición: un profesional concreto, el asignado o un rol. */
export function ruleTarget(target: string, nameById: Record<string, string>): string {
  if (target === 'specialist') return t('rule.specialist')
  if (target === 'coordinator') return t('rule.coordinator')
  if (target === 'administrator') return t('rule.administrator')
  return t('rule.other', { name: nameById[target] ?? target })
}

export function homeLabel(v: { entrance: string | null; floor: string; door: string }): string {
  const home = t('home.label', { floor: v.floor, door: v.door })
  return v.entrance ? `${t('home.entrance', { entrance: v.entrance })} · ${home}` : home
}

/** Error técnico → frase que se entiende en una demo. El detalle queda en la consola del navegador. */
export function errorMessage(e: unknown): string {
  console.error(e)
  if (e instanceof DOMException) {
    if (e.name === 'NotAllowedError' || e.name === 'SecurityError')
      return t('error.mic')
    if (e.name === 'NotFoundError') return t('error.noMic')
  }
  if (e instanceof TypeError) return t('error.network')
  const { status, detail } = (typeof e === 'object' && e !== null ? e : {}) as { status?: number; detail?: string }
  if (status === 401 || status === 403) return t('error.session')
  if (status === 404) return t('error.notFound')
  if (status !== undefined && status >= 500) return t('error.server')
  if (status !== undefined && detail) return detail
  return t('error.generic')
}

/** Tiempo de respuesta (fin de voz del vecino → empieza a hablar el bot). Objetivo ≤ 1200 ms. */
const LATENCY_THRESHOLDS: [number, MessageKey, string][] = [
  [700, 'latency.excellent', 'green'],
  [1200, 'latency.target', 'cyan'],
  [1500, 'latency.tolerable', 'gold'],
  [2000, 'latency.slow', 'volcano'],
]

export function rate(ms: number | null | undefined): { name: string; color: string } {
  if (ms == null) return { name: '—', color: 'default' }
  for (const [limit, name, color] of LATENCY_THRESHOLDS) if (ms <= limit) return { name: t(name), color }
  return { name: t('latency.verySlow'), color: 'red' }
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  assigned: 'blue',
  in_progress: 'processing',
  awaiting_information: 'gold',
  resolved: 'green',
  closed: 'default',
}

export const incidentStatus = (e: string) => ({ text: label('status', e), color: STATUS_COLOR[e] ?? 'default' })

export const work_type = (a: string | null | undefined): string => (a ? label('work', a) : '—')

/** Por qué una incidencia espera al administrador: `actuacion:siniestro`, `objecion_vecino:…`… */
export function reviewReason(m: string | null | undefined): string | null {
  if (!m) return null
  const [code, ...remainder] = m.split(':')
  const value = remainder.join(':').trim()
  switch (code) {
    case 'work_type':
      return t('review.work_type', { work: work_type(value).toLowerCase() })
    case 'caller_objection':
      return t('review.caller_objection', { value })
    case 'nonexistent_specialist':
      return t('review.nonexistent_specialist')
    case 'no_specialist_for_category':
      return t('review.no_specialist_for_category')
    default:
      return capitalize(m)
  }
}

/** Role names in the console language (read when rendering, so they follow a change). */
export const ROLES: Record<string, string> = new Proxy({} as Record<string, string>, {
  get: (_target, role: string) => label('role', role),
})
