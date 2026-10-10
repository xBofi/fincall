export interface Traza {
  nodo: string
  accion: string
  detalle: string
}

export interface Turno {
  rol: 'bot' | 'vecino'
  texto: string
}

export interface DatosLlamada {
  finca_id: string | null
  finca_direccion: string | null
  vivienda_id: string | null
  vivienda_etiqueta: string | null
  nombre_declarado: string | null
  solicitud: string | null
  categoria: string | null
  urgencia: string | null
  urgencia_declarada: string | null
  resumen: string | null
  confirmado: boolean | null
  especialista_id: string | null
  especialista_nombre: string | null
  canal_aviso: string | null
  destino_aviso: string | null
  incidencia_id: string | null
  resultado: string | null
  motivo_revision: string | null
  configuracion_id: string | null
  grafo: string | null
  intentos_finca: number | null
  intentos_vivienda: number | null
}

export interface TiemposServidor {
  turno?: number
  servidor_ms?: number
  grafo_ms?: number
  stt_ms?: number
  interpretacion_ms?: number
  clasificacion_ms?: number
  llm_llamadas?: number
  origen_interpretacion?: string
  stt_eou_a_respuesta_ms?: number
}

export interface VistaLlamada {
  llamada_id: string
  turno: number
  nodo_actual: string
  esperando_usuario: boolean
  finalizada: boolean
  mensajes_bot: string[]
  historial: Turno[]
  traza: Traza[]
  validaciones: string[]
  senales_confianza: Record<string, unknown> | null
  interpretacion: Record<string, unknown> | null
  pendientes: Record<string, string>
  tiempos: TiemposServidor
  configuracion: Record<string, unknown> | null
  datos: DatosLlamada
  transcripcion?: string
  sin_voz?: boolean
}

export interface Salud {
  estado: string
  repositorio: string
  modelo: string
  modelo_disponible: boolean
  stt_servidor: string
  stt_disponible: boolean
  stt_streaming: string | null
  grafos: string[]
  umbrales_ms: Record<string, number>
}

export interface MetricasCliente {
  turno: number
  vad_silencio_ms?: number
  red_ms?: number
  tts_inicio_ms?: number
  respuesta_ms?: number
  stt_cliente?: string
  extra?: Record<string, unknown>
}

export class ApiError extends Error {
  status: number
  detalle: string
  constructor(status: number, detalle: string) {
    super(`${status} ${detalle}`)
    this.status = status
    this.detalle = detalle
  }
}

async function pedir<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { credentials: 'same-origin', ...init })
  if (!r.ok) {
    const texto = await r.text()
    let detalle = texto
    try {
      const j = JSON.parse(texto) as { detail?: unknown }
      if (typeof j.detail === 'string') detalle = j.detail
    } catch {
      // cuerpo no JSON: se usa el texto tal cual
    }
    throw new ApiError(r.status, detalle || r.statusText)
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

export type Rol = 'owner' | 'administrador' | 'calidad'

export interface UsuarioConsola {
  id: string
  nombre_usuario: string
  nombre: string | null
  rol: Rol
  activo: boolean
  debe_cambiar_contrasena: boolean
  creado_en: string | null
}

export interface EstadoAcceso {
  modo: 'local'
  autenticado: boolean
  usuario: UsuarioConsola | null
}

export const auth = {
  estado: () => pedir<EstadoAcceso>('/api/auth/estado'),
  login: (usuario: string, contrasena: string) => pedir<UsuarioConsola>('/api/auth/login', json({ usuario, contrasena })),
  logout: () => pedir<unknown>('/api/auth/logout', { method: 'POST' }),
  cambiarContrasena: (actual: string, nueva: string) => pedir<UsuarioConsola>('/api/auth/contrasena', json({ actual, nueva })),
}

export interface EstadoTelefonia {
  proveedor: string
  lista: boolean
  motivo: string | null
  numero: string | null
  url_publica: string | null
  tts: string
  tts_disponible: boolean
  stt_streaming: boolean
}

export const telefonia = {
  estado: () => pedir<EstadoTelefonia>('/api/telefonia/estado'),
}

export const usuarios = {
  listar: () => pedir<UsuarioConsola[]>('/api/usuarios'),
  crear: (datos: { nombre_usuario: string; rol: Rol; nombre?: string }) =>
    pedir<UsuarioConsola & { contrasena_temporal: string | null }>('/api/usuarios', json(datos)),
  modificar: (id: string, cambios: { rol?: Rol; activo?: boolean; nombre?: string }) =>
    pedir<UsuarioConsola>(`/api/usuarios/${id}`, json(cambios, 'PATCH')),
  restablecer: (id: string) => pedir<UsuarioConsola & { contrasena_temporal: string }>(`/api/usuarios/${id}/restablecer`, { method: 'POST' }),
  eliminar: (id: string) => pedir<void>(`/api/usuarios/${id}`, { method: 'DELETE' }),
}

export interface Resumen {
  n: number
  p50: number | null
  p95: number | null
  max: number | null
}

export interface ResumenOperaciones {
  llamadas: number
  resueltas: number
  no_resueltas: number
  activas: number
  requieren_revision: number
  respuesta_ms: Resumen
  latencia_global: Resumen
}

export interface Expediente {
  id: string
  iniciada_en: string
  telefono_llamante: string | null
  finca_id: string | null
  vivienda_id: string | null
  nombre_declarado: string | null
  resumen: string | null
  resultado: string | null
  motivo_revision: string | null
  estado: 'activa' | 'finalizada'
  finalizada_en: string | null
  requiere_revision: boolean
  respuesta_ms_p50: number | null
}

export interface DetalleExpediente {
  llamada: Expediente & { transcripcion: Turno[] }
  turnos: { turno: number; vecino: string | null; asistente: string[]; nodo_inicial: string; nodo_final: string; tiempos: TiemposServidor }[]
  decisiones: { turno: number; nodo: string; accion: string; detalle: string }[]
  metricas: Record<string, unknown>[]
  pistas: { id: string; canal: string; duracion_ms: number | null; expira_en: string; disponible: boolean; url: string | null }[]
}

export const operaciones = {
  resumen: () => pedir<ResumenOperaciones>('/api/operaciones/resumen'),
  llamadas: (estado?: string) => pedir<Expediente[]>(`/api/operaciones/llamadas${estado ? `?estado=${estado}` : ''}`),
  detalle: (id: string) => pedir<DetalleExpediente>(`/api/operaciones/llamadas/${id}`),
}

export const api = {
  salud: () => pedir<Salud>('/api/salud'),
  iniciar: (telefono?: string) =>
    pedir<VistaLlamada>('/api/llamadas', json({ telefono_llamante: telefono ?? null })),
  mensaje: (id: string, texto: string) =>
    pedir<VistaLlamada>(`/api/llamadas/${id}/mensajes`, json({ texto })),
  audio: (id: string, blob: Blob, mime: string) => {
    const form = new FormData()
    form.append('fichero', blob, 'turno.webm')
    form.append('mime', mime)
    return pedir<VistaLlamada>(`/api/llamadas/${id}/audio`, { method: 'POST', body: form })
  },
  metricas: (id: string, cuerpo: MetricasCliente) =>
    pedir<unknown>(`/api/llamadas/${id}/metricas`, json(cuerpo)),
  entrega: (id: string, turno: number, estado: string, ratio: number, texto_entregado: string) =>
    pedir<unknown>(`/api/llamadas/${id}/entrega`, json({ turno, estado, ratio, texto_entregado })),
}

// ---------------------------------------------------------------- flujo del asistente

export type TipoNodo = 'decision' | 'pregunta' | 'efecto' | 'terminal'

export interface NodoGrafo {
  id: string
  titulo: string
  resumen: string
  tipo: TipoNodo
  etapa: number
  usa_llm: boolean
  caminos_deterministas: string[]
  efectos: string[]
  plantillas: string[]
}

export interface AristaGrafo {
  origen: string
  destino: string
  condicion: string
  veces: number
}

export interface Grafo {
  nodos: NodoGrafo[]
  aristas: AristaGrafo[]
  entrada: string
  plantillas_globales: string[]
  violaciones: string[]
}

export interface Plantilla {
  id: string
  nodo: string
  descripcion: string
  texto: string
  texto_original: string
  variables: string[]
  pregunta: boolean
  pregunta_en: string | null
  sobrescrita: boolean
  version: number
}

export interface PromptSistema {
  id: string
  titulo: string
  version: string
  fichero: string
  cuando: string
  texto: string
  contrato: string[]
}

export interface Prompts {
  editables: boolean
  motivo: string
  prompts: PromptSistema[]
}

export const flujo = {
  grafo: () => pedir<Grafo>('/api/grafo'),
  prompts: () => pedir<Prompts>('/api/prompts'),
  plantillas: () => pedir<Plantilla[]>('/api/plantillas'),
  publicar: (id: string, texto: string) => pedir<Plantilla>(`/api/plantillas/${encodeURIComponent(id)}`, json({ texto }, 'PUT')),
  restaurar: (id: string) => pedir<Plantilla>(`/api/plantillas/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

// ---------------------------------------------------------------- catálogos

export interface FincaResumen {
  id: string
  direccion: string
  administrador_id: string
  administrador: string
  correo: string
  telefono_oficial: string
  viviendas: number
}

export interface ViviendaCatalogo {
  id: string
  portal: string | null
  piso: string
  puerta: string
}

export interface FincaDetalle extends Omit<FincaResumen, 'viviendas'> {
  viviendas: ViviendaCatalogo[]
  configuracion_efectiva: Record<string, unknown>
  configuracion_id: string | null
}

export interface EspecialistaCatalogo {
  id: string
  nombre: string
  administrador_id: string
  administrador: string
  categoria_principal: string
  canal_contacto: string
  destino: string
}

export const catalogos = {
  fincas: () => pedir<FincaResumen[]>('/api/catalogos/fincas'),
  finca: (id: string) => pedir<FincaDetalle>(`/api/catalogos/fincas/${encodeURIComponent(id)}`),
  especialistas: () => pedir<EspecialistaCatalogo[]>('/api/catalogos/especialistas'),
}
