export interface Trace {
  node: string
  action: string
  detail: string
}

export interface Turn {
  role: 'bot' | 'caller'
  text: string
}

export interface CallData {
  estate_id: string | null
  estate_address: string | null
  home_id: string | null
  home_label: string | null
  request: string | null
  category: string | null
  urgency: string | null
  declared_urgency: string | null
  summary: string | null
  confirmed: boolean | null
  /** Emergencia con tratamiento activado: indicación de seguridad dada y respuesta recogida */
  emergency_handled?: boolean | null
  specialist_id: string | null
  specialist_name: string | null
  /** reparacion | reposicion | siniestro | convivencia | privativo */
  work_type: string | null
  equipment_id: string | null
  /** Para el administrador o el profesional: equipo, contrato y por qué le llega */
  suggestion: string | null
  objection: string | null
  notice_channel: string | null
  notice_target: string | null
  incident_id: string | null
  result: string | null
  review_reason: string | null
  config_id: string | null
  graph: string | null
  estate_attempts: number | null
  home_attempts: number | null
}

export interface ServerTimings {
  turn?: number
  server_ms?: number
  graph_ms?: number
  stt_ms?: number
  interpretation_ms?: number
  classification_ms?: number
  llm_calls?: number
  interpretation_origin?: string
  stt_eou_to_response_ms?: number
}

export interface LanguageInfo {
  code: string
  locale: string
  name: string
  native_name: string
  rtl: boolean
  browser_lang: string
  stt: string
  server_tts: boolean
  enabled: boolean
}

export interface LanguagesInfo {
  default: string
  detection: boolean
  languages: LanguageInfo[]
}

export interface PersonaInfo {
  id: string
  client: string
  default: boolean
  languages: Record<string, { agent_name: string; has_voice: boolean }>
}

export interface CallView {
  call_id: string
  language?: string
  language_locked?: boolean
  persona?: string | null
  turn: number
  current_node: string
  awaiting_user: boolean
  finished: boolean
  bot_messages: string[]
  history: Turn[]
  trace: Trace[]
  validations: string[]
  confidence_signals: Record<string, unknown> | null
  interpretation: Record<string, unknown> | null
  pending: Record<string, string>
  timings: ServerTimings
  config: Record<string, unknown> | null
  /** Ledger de entrega: qué frases del último mensaje oyó el vecino. */
  last_delivery?: Record<string, unknown> | null
  data: CallData
  transcript?: string
  no_voice?: boolean
}

export interface ServerTurnTimings {
  turn: number
  stt_ms: number | null
  interpretation_ms: number | null
  llm_calls: number
  server_ms: number
  response_ms: number | null
  stt_client: string | null
}

export interface Health {
  state: string
  repository: string
  model: string
  model_available: boolean
  stt_server: string
  stt_available: boolean
  stt_streaming: string | null
  /** Hay servidor de LiveKit configurado: la consola puede hablar por WebRTC con el agente. */
  livekit: boolean
  /** La consola puede llamar al agente por Twilio (la red telefónica) desde el navegador. */
  twilio_browser?: boolean
  /** Voz del servidor (la del teléfono); null si no hay y la consola usa la del navegador. */
  server_tts: string | null
  graphs: string[]
  thresholds_ms: Record<string, number>
}

export interface ClientMetrics {
  turn: number
  vad_silence_ms?: number
  network_ms?: number
  tts_start_ms?: number
  response_ms?: number
  stt_client?: string
  extra?: Record<string, unknown>
}

export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(`${status} ${detail}`)
    this.status = status
    this.detail = detail
  }
}

async function ask<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { credentials: 'same-origin', ...init })
  if (!r.ok) {
    const text = await r.text()
    let detail = text
    try {
      const j = JSON.parse(text) as { detail?: unknown }
      if (typeof j.detail === 'string') detail = j.detail
      // Errores de validación de FastAPI: una lista de {loc, msg}; se enseñan los mensajes
      else if (Array.isArray(j.detail))
        detail = j.detail
          .map((d: { msg?: string }) => (d.msg ?? '').replace(/^Value error, /, ''))
          .filter(Boolean)
          .join(' · ')
    } catch {
      // cuerpo no JSON: se usa el texto tal cual
    }
    throw new ApiError(r.status, detail || r.statusText)
  }
  if (r.status === 204) return undefined as T
  return (await r.json()) as T
}

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})


// ---------------------------------------------------------------- consola operativa (APIs protegidas)

export type Role = 'owner' | 'administrator' | 'quality'

export type UserAccessState = 'not_requested' | 'requested' | 'granted' | 'denied'

export interface ConsoleUser {
  id: string
  username: string
  name: string | null
  role: Role
  active: boolean
  email: string | null
  /** 'google' | 'microsoft'; null en las cuentas antiguas con contraseña */
  provider: string | null
  access_state: UserAccessState
  requested_at: string | null
  created_at: string | null
}

export interface AccessState {
  mode: 'oidc'
  authenticated: boolean
  user: ConsoleUser | null
}

export interface AccessProvider {
  id: 'google' | 'microsoft'
  label: string
}

export const auth = {
  state: () => ask<AccessState>('/api/auth/state'),
  providers: () => ask<AccessProvider[]>('/api/auth/providers'),
  /** Navegación completa: el proveedor vuelve a /api/auth/oidc/{id}/retorno y de ahí a la consola */
  signInWith: (id: string) => window.location.assign(`/api/auth/oidc/${id}/start`),
  submit_request: () => ask<ConsoleUser>('/api/auth/submit_request', { method: 'POST' }),
  logout: () => ask<unknown>('/api/auth/logout', { method: 'POST' }),
}

export interface TelephonyStatus {
  provider: string
  items: boolean
  reason: string | null
  number: string | null
  public_url: string | null
  tts: string
  tts_available: boolean
  stt_streaming: boolean
}

export const telephony = {
  state: () => ask<TelephonyStatus>('/api/telephony/state'),
  /** Access Token para que este navegador llame al agente por Twilio */
  browserToken: () =>
    ask<{ token: string; identity: string }>('/api/telephony/twilio/browser-token', { method: 'POST' }),
}

export const users = {
  list_all: () => ask<ConsoleUser[]>('/api/users'),
  grant: (id: string, role: Role) => ask<ConsoleUser>(`/api/users/${id}/grant`, json({ role })),
  deny: (id: string) => ask<ConsoleUser>(`/api/users/${id}/deny`, { method: 'POST' }),
  modify: (id: string, changes: { role?: Role; active?: boolean; name?: string }) =>
    ask<ConsoleUser>(`/api/users/${id}`, json(changes, 'PATCH')),
  remove: (id: string) => ask<void>(`/api/users/${id}`, { method: 'DELETE' }),
}

export interface Summary {
  n: number
  p50: number | null
  p95: number | null
  max: number | null
}

export interface OperationsSummary {
  calls: number
  resolved: number
  unresolved: number
  active_calls: number
  need_review: number
  response_ms: Summary
  global_latency: Summary
}

export interface CallRecord {
  id: string
  started_at: string
  caller_phone: string | null
  estate_id: string | null
  home_id: string | null
  summary: string | null
  result: string | null
  review_reason: string | null
  state: 'active' | 'finished'
  finished_at: string | null
  needs_review: boolean
  response_ms_p50: number | null
}

/** Lo que el asistente debería haber entendido: un valor, "*" (cualquiera), null (sin dato) o alternativas. */
export type ExpectedValue = string | boolean | null | (string | boolean | null)[]

export type TestBank = 'interpretation' | 'classification'

/** Turno real del vecino marcado por calidad como caso del banco de pruebas. */
export interface BankCase {
  id: string
  call_id: string
  turn: number
  bank: TestBank
  node: string
  question: string | null
  heard_text: string | null
  utterance: string
  catalog: boolean
  equipment_list: [string, string][] | null
  expected: Record<string, ExpectedValue>
  note: string | null
  author_id: string | null
  created_at: string
  updated_at: string
}

export interface NewTestBankCase {
  turn: number
  bank: TestBank
  expected: Record<string, ExpectedValue>
  note?: string | null
  heard_text?: string | null
  question?: string | null
}

export interface CaseOptions {
  keys: string[]
  intents: string[]
  urgencies: string[]
  work_types: string[]
  categories: string[]
}

export interface CallRecordDetail {
  call: CallRecord & { transcript: Turn[] }
  turns: {
    turn: number
    caller: string | null
    assistant: string[]
    initial_node: string
    final_node: string
    timings: ServerTimings
    interpretation?: Record<string, unknown> | null
  }[]
  decisions: { turn: number; node: string; action: string; detail: string }[]
  cases?: BankCase[]
  metrics: Record<string, unknown>[]
  tracks: { id: string; channel: string; duration_ms: number | null; expires_at: string; available: boolean; url: string | null }[]
}

export const operations = {
  summary: () => ask<OperationsSummary>('/api/operations/summary'),
  calls: (state?: string) => ask<CallRecord[]>(`/api/operations/calls${state ? `?state=${state}` : ''}`),
  detail: (id: string) => ask<CallRecordDetail>(`/api/operations/calls/${id}`),
  caseOptions: () => ask<CaseOptions>('/api/operations/cases/options'),
  markCase: (callId: string, testCase: NewTestBankCase) =>
    ask<BankCase>(`/api/operations/calls/${callId}/cases`, json(testCase)),
  unmarkCase: (caseId: string) => ask<void>(`/api/operations/cases/${caseId}`, { method: 'DELETE' }),
  /** Only the owner: the call and everything associated with it; its incidents stay, anonymised. */
  erase: (callId: string) => ask<void>(`/api/operations/calls/${encodeURIComponent(callId)}/erase`, { method: 'POST' }),
}

/** What is on screen, for the audit log. Nobody queries the log from the console. */
export const audit = {
  openView: (resource: string, resource_id: string) =>
    ask<{ id: string; heartbeat_s: number }>('/api/audit/views', json({ resource, resource_id })),
  heartbeat: (id: string) => ask<void>(`/api/audit/views/${encodeURIComponent(id)}/heartbeat`, { method: 'POST' }),
  endPath: (id: string) => `/api/audit/views/${encodeURIComponent(id)}/end`,
}

export const api = {
  health: () => ask<Health>('/api/health'),
  /** `language` null = BC_LANGUAGE with detection; `persona` null = BC_PERSONA. */
  begin: (options: { phone?: string; language?: string | null; persona?: string | null } = {}) =>
    ask<CallView>(
      '/api/calls',
      json({ caller_phone: options.phone ?? null, language: options.language ?? null, persona: options.persona ?? null }),
    ),
  languages: () => ask<LanguagesInfo>('/api/languages'),
  personas: () => ask<PersonaInfo[]>('/api/personas'),
  message: (id: string, text: string) =>
    ask<CallView>(`/api/calls/${id}/messages`, json({ text })),
  audio: (id: string, blob: Blob, mime: string) => {
    const form = new FormData()
    form.append('file', blob, 'turno.webm')
    form.append('mime', mime)
    return ask<CallView>(`/api/calls/${id}/audio`, { method: 'POST', body: form })
  },
  metrics: (id: string, body: ClientMetrics) =>
    ask<unknown>(`/api/calls/${id}/metrics`, json(body)),
  delivery: (id: string, turn: number, state: string, ratio: number, delivered_text: string) =>
    ask<unknown>(`/api/calls/${id}/delivery`, json({ turn, state, ratio, delivered_text })),
  get: (id: string) => ask<CallView>(`/api/calls/${id}`),
  /** Tiempos por turno que guarda el servidor (los del agente de LiveKit incluidos) */
  callMetrics: (id: string) =>
    ask<{ turns: ServerTurnTimings[] }>(`/api/calls/${id}/metrics`),
  liveKitToken: (call_id: string) =>
    ask<{ url: string; token: string; room: string }>('/api/livekit/token', json({ call_id })),
}

// ---------------------------------------------------------------- flujo del asistente

export type NodeType = 'decision' | 'question' | 'effect' | 'terminal'

export interface GraphNode {
  id: string
  title: string
  summary: string
  kind: NodeType
  stage: number
  uses_llm: boolean
  deterministic_paths: string[]
  effects: string[]
  templates: string[]
}

export interface GraphEdge {
  origin: string
  target: string
  condition: string
  times: number
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  utterance: string
  global_templates: string[]
  violations: string[]
}

export interface Template {
  id: string
  node: string
  description: string
  text: string
  original_text: string
  variables: string[]
  question: boolean
  question_in: string | null
  overridden: boolean
  version: number
}

export interface SystemPrompt {
  id: string
  title: string
  version: string
  file: string
  when: string
  text: string
  contract: string[]
}

export interface Prompts {
  editables: boolean
  reason: string
  prompts: SystemPrompt[]
}

export const flujo = {
  graph: () => ask<Graph>('/api/graph'),
  prompts: () => ask<Prompts>('/api/prompts'),
  templates: () => ask<Template[]>('/api/templates'),
  publish: (id: string, text: string) => ask<Template>(`/api/templates/${encodeURIComponent(id)}`, json({ text }, 'PUT')),
  restore: (id: string) => ask<Template>(`/api/templates/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

// ---------------------------------------------------------------- catálogos

export interface EstateSummary {
  id: string
  address: string
  administrator_id: string
  administrator: string
  email: string
  official_phone: string
  homes: number
}

export interface CatalogHome {
  id: string
  entrance: string | null
  floor: string
  door: string
}

export interface CatalogEquipment {
  id: string
  kind: string
  description: string
  location: string | null
  brand: string | null
  model: string | null
  installer: string | null
  maintainer_id: string | null
  contract: string | null
  warranty_until: string | null
}

export interface EstateDetail extends Omit<EstateSummary, 'homes'> {
  homes: CatalogHome[]
  equipment_list: CatalogEquipment[]
  effective_config: Record<string, unknown>
  config_id: string | null
  /** La finca fija sus propios profesionales: lo que se asigne al administrador no le llega. */
  own_assignment?: boolean
}

export interface CatalogSpecialist {
  id: string
  name: string
  administrator_id: string
  administrator: string
  main_category: string
  contact_channel: string
  target: string
}

export interface CatalogAdministrator {
  id: string
  name: string
  estates: { id: string; address: string }[]
  category_catalog: string[]
  specialist_by_category: Record<string, string>
  config_id: string | null
}

export interface EstateData {
  address: string
  email: string
  official_phone: string
}

export interface HomeData {
  entrance: string | null
  floor: string
  door: string
}

export type EquipmentData = Omit<CatalogEquipment, 'id' | 'description'>

export interface SpecialistData {
  name: string
  main_category: string
  contact_channel: 'email' | 'call'
  target: string
}

export interface SpecialistDeletion {
  deleted: string
  unassigned_categories: string[]
  equipment_without_maintainer: string[]
}

const path = (...parts: string[]) => `/api/catalogs/${parts.map(encodeURIComponent).join('/')}`

export const catalogs = {
  estates: () => ask<EstateSummary[]>('/api/catalogs/estates'),
  estate: (id: string) => ask<EstateDetail>(`/api/catalogs/estates/${encodeURIComponent(id)}`),
  specialists: () => ask<CatalogSpecialist[]>('/api/catalogs/specialists'),
  administrators: () => ask<CatalogAdministrator[]>('/api/catalogs/administrators'),

  createEstate: (data: EstateData & { administrator_id: string }) => ask<EstateDetail>(path('estates'), json(data)),
  editEstate: (id: string, data: EstateData) => ask<EstateDetail>(path('estates', id), json(data, 'PUT')),
  deleteEstate: (id: string) => ask<unknown>(path('estates', id), { method: 'DELETE' }),

  createHome: (estate: string, data: HomeData) => ask<CatalogHome>(path('estates', estate, 'homes'), json(data)),
  editHome: (estate: string, id: string, data: HomeData) =>
    ask<CatalogHome>(path('estates', estate, 'homes', id), json(data, 'PUT')),
  deleteHome: (estate: string, id: string) => ask<unknown>(path('estates', estate, 'homes', id), { method: 'DELETE' }),

  createEquipment: (estate: string, data: EquipmentData) => ask<CatalogEquipment>(path('estates', estate, 'equipment_list'), json(data)),
  editEquipment: (estate: string, id: string, data: EquipmentData) =>
    ask<CatalogEquipment>(path('estates', estate, 'equipment_list', id), json(data, 'PUT')),
  deleteEquipment: (estate: string, id: string) => ask<unknown>(path('estates', estate, 'equipment_list', id), { method: 'DELETE' }),

  createSpecialist: (data: SpecialistData & { administrator_id: string }) =>
    ask<CatalogSpecialist>(path('specialists'), json(data)),
  editSpecialist: (id: string, data: SpecialistData) =>
    ask<CatalogSpecialist>(path('specialists', id), json(data, 'PUT')),
  deleteSpecialist: (id: string, force = false) =>
    ask<SpecialistDeletion>(`${path('specialists', id)}${force ? '?forzar=true' : ''}`, { method: 'DELETE' }),

  assignCategory: (admin: string, category: string, specialist_id: string | null) =>
    ask<CatalogAdministrator>(path('administrators', admin, 'categories', category), json({ specialist_id }, 'PUT')),
}

// ---------------------------------------------------------------- bandeja del administrador

/** What an erased call turns into wherever it was referenced (its incidents stay). */
export const DELETED_CALL = 'deleted-call'

export type IncidentStatus = 'pending' | 'assigned' | 'in_progress' | 'awaiting_information' | 'resolved' | 'closed'
export type ManualStatus = 'in_progress' | 'awaiting_information' | 'resolved' | 'closed'

export interface Incident {
  id: string
  call_id: string
  estate_id: string
  home_id: string
  description: string
  category: string
  specialist_id: string | null
  urgency: 'low' | 'medium' | 'high' | 'emergency'
  state: IncidentStatus
  created_at: string
  equipment_id: string | null
  work_type: string
  suggestion: string | null
}

export interface InboxRow extends Incident {
  pending_administrator: boolean
  estate_address: string | null
  home: string | null
  specialist_name: string | null
  review_reason: string | null
}

export interface InboxSpecialist {
  id: string
  administrator_id: string
  name: string
  main_category: string
  contact_channel: string
  target: string
}

export interface HistoryEntry {
  id: string
  action: 'assign' | 'change_state' | 'note'
  previous_state: IncidentStatus | null
  new_state: IncidentStatus
  user_id: string | null
  user_name?: string | null
  note: string | null
  detail: Record<string, unknown>
  changed_at: string
}

export interface IncidentDetail {
  incident: Incident
  pending_administrator: boolean
  estate: { id: string; address: string } | null
  home: string | null
  call: {
    id: string
    started_at: string
    caller_phone: string | null
      summary: string | null
    transcript: Turn[]
    result: string | null
    review_reason: string | null
  } | null
  equipment: (CatalogEquipment & { maintainer: InboxSpecialist | null }) | null
  specialist: InboxSpecialist | null
  specialists: InboxSpecialist[]
  history: HistoryEntry[]
}

export interface InboxFilters {
  state?: IncidentStatus
  pending_administrator_only?: boolean
  estate_id?: string
  urgency?: string
}

export interface ActionResult {
  incident: Incident
  history: HistoryEntry
  notice?: string
}

const incidentPath = (id: string, action = '') => `/api/inbox/incidents/${encodeURIComponent(id)}${action}`

export const inbox = {
  list_all: (f: InboxFilters = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== false && v !== '') q.set(k, String(v))
    const s = q.toString()
    return ask<InboxRow[]>(`/api/inbox/incidents${s ? `?${s}` : ''}`)
  },
  detail: (id: string) => ask<IncidentDetail>(incidentPath(id)),
  assign: (id: string, specialist_id: string, note?: string) =>
    ask<ActionResult>(incidentPath(id, '/assign'), json({ specialist_id, note: note || null })),
  state: (id: string, state: ManualStatus, note?: string) =>
    ask<ActionResult>(incidentPath(id, '/state'), json({ state, note: note || null })),
  note: (id: string, text: string) => ask<ActionResult>(incidentPath(id, '/note'), json({ text })),
}
