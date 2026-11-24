/**
 * Texts of the conversation KPIs panel, behind keys so another language is one more table.
 * The console speaks Spanish today; the locale comes from <html lang> (falls back to Spanish).
 */

export type KpiKey =
  | 'kpis.eyebrow'
  | 'kpis.title'
  | 'kpis.window'
  | 'kpis.containment'
  | 'kpis.containment.help'
  | 'kpis.handoff'
  | 'kpis.handoff.help'
  | 'kpis.needsHuman'
  | 'kpis.reprompt'
  | 'kpis.reprompt.help'
  | 'kpis.emergency'
  | 'kpis.turns'
  | 'kpis.llmCalls'
  | 'kpis.aht'
  | 'kpis.latency'
  | 'kpis.serverLatency'
  | 'kpis.noData'
  | 'kpis.error'
  | 'kpis.refresh'

const MESSAGES: Record<'es' | 'en', Record<KpiKey, string>> = {
  es: {
    'kpis.eyebrow': 'Conversación · llamadas guardadas',
    'kpis.title': 'Indicadores de las llamadas',
    'kpis.window': 'Últimas {n} llamadas',
    'kpis.containment': 'Resueltas sin persona',
    'kpis.containment.help': 'Llamadas acabadas en las que el asistente creó la incidencia él solo.',
    'kpis.handoff': 'Pasadas a una persona',
    'kpis.handoff.help': 'El vecino pidió hablar con una persona.',
    'kpis.needsHuman': 'Necesitan al administrador',
    'kpis.reprompt': 'Turnos repetidos',
    'kpis.reprompt.help': 'Turnos en los que la conversación no avanzó: se repitió, no se oyó o se volvió a preguntar.',
    'kpis.emergency': 'Emergencias',
    'kpis.turns': 'Turnos por llamada',
    'kpis.llmCalls': 'Llamadas al modelo por llamada',
    'kpis.aht': 'Duración media',
    'kpis.latency': 'Respuesta p50 / p95',
    'kpis.serverLatency': 'Servidor p50 / p95',
    'kpis.noData': 'Aún no hay llamadas guardadas.',
    'kpis.error': 'No se pudieron cargar los indicadores.',
    'kpis.refresh': 'Actualizar',
  },
  en: {
    'kpis.eyebrow': 'Conversation · stored calls',
    'kpis.title': 'Call metrics',
    'kpis.window': 'Last {n} calls',
    'kpis.containment': 'Contained (no human)',
    'kpis.containment.help': 'Finished calls where the assistant created the incident on its own.',
    'kpis.handoff': 'Handed to a human',
    'kpis.handoff.help': 'The caller asked to speak to a person.',
    'kpis.needsHuman': 'Need the administrator',
    'kpis.reprompt': 'Reprompt turns',
    'kpis.reprompt.help': 'Turns where the conversation did not move on: repeated, not heard or asked again.',
    'kpis.emergency': 'Emergencies',
    'kpis.turns': 'Turns per call',
    'kpis.llmCalls': 'LLM requests per call',
    'kpis.aht': 'Average handle time',
    'kpis.latency': 'Response p50 / p95',
    'kpis.serverLatency': 'Server p50 / p95',
    'kpis.noData': 'No stored calls yet.',
    'kpis.error': 'Could not load the metrics.',
    'kpis.refresh': 'Refresh',
  },
}

function locale(): 'es' | 'en' {
  const lang = typeof document !== 'undefined' ? document.documentElement.lang.slice(0, 2) : 'es'
  return lang === 'en' ? 'en' : 'es'
}

export function t(key: KpiKey, params: Record<string, string | number> = {}): string {
  const text = MESSAGES[locale()][key] ?? MESSAGES.es[key]
  return text.replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? ''))
}
