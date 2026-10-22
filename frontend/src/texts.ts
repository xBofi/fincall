/**
 * Etiquetas legibles para lo que el backend devuelve como códigos.
 *
 * La consola se enseña a clientes: ningún código interno (`incidencia_creada`, `fontaneria`) debe
 * verse tal cual. Un código sin traducción se capitaliza, nunca se oculta.
 */

export function capitalize(code: string): string {
  const t = code.replace(/_/g, ' ').trim()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

const CATEGORIES: Record<string, string> = {
  plumbing: 'Fontanería',
  electrical: 'Electricidad',
  ascensor: 'Ascensor',
  cleaning: 'Limpieza',
  locksmith: 'Cerrajería',
  masonry: 'Albañilería',
  painting: 'Pintura',
  gardening: 'Jardinería',
  general_maintenance: 'Mantenimiento general',
  administrative: 'Gestión administrativa',
  unclassifiable: 'Sin clasificar',
}

export const category = (c: string | null | undefined): string => (c ? CATEGORIES[c] ?? capitalize(c) : '—')

const URGENCY: Record<string, { text: string; color: string }> = {
  baja: { text: 'Baja', color: 'green' },
  medium: { text: 'Media', color: 'gold' },
  high: { text: 'Alta', color: 'volcano' },
  emergency: { text: 'Emergencia', color: 'red' },
}

export const urgency = (u: string | null | undefined) =>
  u ? URGENCY[u] ?? { text: capitalize(u), color: 'default' } : null

export type Tone = 'good' | 'review' | 'neutral'

const RESULTS: Record<string, { short: string; long: string; tone: Tone }> = {
  incident_created: {
    short: 'Incidencia creada',
    long: 'Incidencia creada y profesional avisado.',
    tone: 'good',
  },
  estate_not_identified: {
    short: 'Finca no encontrada',
    long: 'La dirección no corresponde a ninguna finca dada de alta. Se guardó el mensaje para revisarlo.',
    tone: 'review',
  },
  home_not_identified: {
    short: 'Vivienda no encontrada',
    long: 'No se pudo identificar la vivienda. Se recogió el problema y se avisó al administrador por correo.',
    tone: 'review',
  },
  unclassifiable: {
    short: 'Pendiente de revisión',
    long: 'Faltaban datos para crear la incidencia. Se envió un resumen al administrador para revisarla.',
    tone: 'review',
  },
  handed_to_administrator: {
    short: 'Derivada a una persona',
    long: 'El vecino pidió hablar con una persona. Se avisó al administrador.',
    tone: 'review',
  },
  abandoned: {
    short: 'Colgó antes de terminar',
    long: 'El vecino se despidió antes de terminar. La llamada queda registrada, sin incidencia.',
    tone: 'neutral',
  },
}

export const result = (r: string | null | undefined) =>
  r ? RESULTS[r] ?? { short: capitalize(r), long: capitalize(r), tone: 'neutral' as Tone } : null

const CHANNELS: Record<string, string> = {
  email: 'email',
  call: 'call',
  ticket: 'ticket',
}

export const channel = (c: string | null | undefined): string => (c ? CHANNELS[c] ?? c : '—')

/** Destino de una regla de aviso, ya con su preposición: un profesional concreto, el asignado o un rol. */
export function ruleTarget(target: string, nameById: Record<string, string>): string {
  if (target === 'specialist') return 'al profesional asignado'
  if (target === 'coordinator') return 'al correo de la finca'
  if (target === 'administrator') return 'al administrador'
  return `a ${nameById[target] ?? target}`
}

export function homeLabel(v: { entrance: string | null; floor: string; door: string }): string {
  return `${v.entrance ? `Portal ${v.entrance} · ` : ''}${v.floor}º ${v.door}`
}

/** Error técnico → frase que se entiende en una demo. El detalle queda en la consola del navegador. */
export function errorMessage(e: unknown): string {
  console.error(e)
  if (e instanceof DOMException) {
    if (e.name === 'NotAllowedError' || e.name === 'SecurityError')
      return 'El navegador no deja usar el micrófono: revisa los permisos de la página.'
    if (e.name === 'NotFoundError') return 'No se ha encontrado ningún micrófono.'
  }
  if (e instanceof TypeError) return 'No hay conexión con el servidor. Comprueba que está en marcha.'
  const { status, detail } = (typeof e === 'object' && e !== null ? e : {}) as { status?: number; detail?: string }
  if (status === 401 || status === 403) return 'La sesión ha caducado o no tienes permiso. Vuelve a entrar.'
  if (status === 404) return 'No se ha encontrado: puede que la llamada ya haya terminado.'
  if (status !== undefined && status >= 500) return 'El servidor ha tenido un problema. Inténtalo de nuevo.'
  if (status !== undefined && detail) return detail
  return 'Algo ha fallado. Inténtalo de nuevo.'
}

/** Tiempo de respuesta (fin de voz del vecino → empieza a hablar el bot). Objetivo ≤ 1200 ms. */
const LATENCY_THRESHOLDS: [number, string, string][] = [
  [700, 'excellent', 'green'],
  [1200, 'dentro del objetivo', 'cyan'],
  [1500, 'tolerable', 'gold'],
  [2000, 'slow', 'volcano'],
]

export function rate(ms: number | null | undefined): { name: string; color: string } {
  if (ms == null) return { name: '—', color: 'default' }
  for (const [limit, name, color] of LATENCY_THRESHOLDS) if (ms <= limit) return { name, color }
  return { name: 'muy lento', color: 'red' }
}

const INCIDENT_STATUSES: Record<string, { text: string; color: string }> = {
  pending: { text: 'Pendiente', color: 'orange' },
  assigned: { text: 'Asignada', color: 'blue' },
  in_progress: { text: 'En curso', color: 'processing' },
  awaiting_information: { text: 'Esperando información', color: 'gold' },
  resolved: { text: 'Resuelta', color: 'green' },
  closed: { text: 'Cerrada', color: 'default' },
}

export const incidentStatus = (e: string) => INCIDENT_STATUSES[e] ?? { text: capitalize(e), color: 'default' }

const WORK_TYPES: Record<string, string> = {
  repair: 'Reparación',
  replacement: 'Reposición',
  insurance_claim: 'Posible siniestro',
  neighbour_dispute: 'Convivencia',
  private_unit: 'Zona privativa',
}

export const work_type = (a: string | null | undefined): string => (a ? WORK_TYPES[a] ?? capitalize(a) : '—')

/** Por qué una incidencia espera al administrador: `actuacion:siniestro`, `objecion_vecino:…`… */
export function reviewReason(m: string | null | undefined): string | null {
  if (!m) return null
  const [code, ...remainder] = m.split(':')
  const value = remainder.join(':').trim()
  switch (code) {
    case 'work_type':
      return `No es una reparación: ${work_type(value).toLowerCase()}.`
    case 'caller_objection':
      return `El vecino discrepa del resumen: «${value}».`
    case 'nonexistent_specialist':
      return 'El profesional configurado para esta avería ya no existe.'
    case 'no_specialist_for_category':
      return 'Ningún profesional cubre este tipo de avería.'
    default:
      return capitalize(m)
  }
}

export const ROLES: Record<string, string> = {
  owner: 'Propietario',
  administrator: 'Administrador',
  quality: 'Calidad',
}
