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

export interface Finca {
  id: string
  direccion: string
  correo: string
  telefono_oficial: string
  viviendas: string[]
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

export interface Resumen {
  n: number
  p50: number | null
  p95: number | null
  max: number | null
}

export interface Agregados {
  turnos: number
  respuesta_ms: Resumen
  calificaciones: Record<string, number>
  servidor_ms: Resumen
  servidor_ms_con_llm: Resumen
  servidor_ms_sin_llm: Resumen
  stt_ms: Resumen
  interpretacion_ms: Resumen
  clasificacion_ms: Resumen
  vad_silencio_ms: Resumen
  tts_inicio_ms: Resumen
  umbrales_ms: Record<string, number>
}

async function pedir<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return (await r.json()) as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ---------------------------------------------------------------- consola operativa (APIs protegidas)

export interface EstadoAcceso {
  modo: 'google' | 'desarrollo' | 'deshabilitado'
  autenticado: boolean
  usuario: { id: string; email: string; rol: 'owner' | 'administrador' | 'calidad' } | null
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
  acceso: () => pedir<EstadoAcceso>('/api/auth/estado'),
  logout: () => pedir<unknown>('/api/auth/logout', { method: 'POST' }),
  resumen: () => pedir<ResumenOperaciones>('/api/operaciones/resumen'),
  llamadas: (estado?: string) => pedir<Expediente[]>(`/api/operaciones/llamadas${estado ? `?estado=${estado}` : ''}`),
  detalle: (id: string) => pedir<DetalleExpediente>(`/api/operaciones/llamadas/${id}`),
}

export const api = {
  salud: () => pedir<Salud>('/api/salud'),
  fincas: () => pedir<Finca[]>('/api/fincas'),
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
  metricasLlamada: (id: string) =>
    pedir<{ agregados: Agregados; turnos: Record<string, unknown>[] }>(`/api/llamadas/${id}/metricas`),
  metricasGlobales: () => pedir<{ agregados: Agregados }>('/api/metricas'),
}
