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
  /** Emergencia con tratamiento activado: indicación de seguridad dada y respuesta recogida */
  emergencia_atendida?: boolean | null
  especialista_id: string | null
  especialista_nombre: string | null
  /** reparacion | reposicion | siniestro | convivencia | privativo */
  actuacion: string | null
  equipo_id: string | null
  /** Para el administrador o el profesional: equipo, contrato y por qué le llega */
  sugerencia: string | null
  objecion: string | null
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
  /** Ledger de entrega: qué frases del último mensaje oyó el vecino. */
  entrega_ultima?: Record<string, unknown> | null
  datos: DatosLlamada
  transcripcion?: string
  sin_voz?: boolean
}

export interface TiemposTurnoServidor {
  turno: number
  stt_ms: number | null
  interpretacion_ms: number | null
  llm_llamadas: number
  servidor_ms: number
  respuesta_ms: number | null
  stt_cliente: string | null
}

export interface Salud {
  estado: string
  repositorio: string
  modelo: string
  modelo_disponible: boolean
  stt_servidor: string
  stt_disponible: boolean
  stt_streaming: string | null
  /** Hay servidor de LiveKit configurado: la consola puede hablar por WebRTC con el agente. */
  livekit: boolean
  /** Voz del servidor (la del teléfono); null si no hay y la consola usa la del navegador. */
  tts_servidor: string | null
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
      // Errores de validación de FastAPI: una lista de {loc, msg}; se enseñan los mensajes
      else if (Array.isArray(j.detail))
        detalle = j.detail
          .map((d: { msg?: string }) => (d.msg ?? '').replace(/^Value error, /, ''))
          .filter(Boolean)
          .join(' · ')
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

export type EstadoAccesoUsuario = 'sin_solicitar' | 'solicitado' | 'concedido' | 'denegado'

export interface UsuarioConsola {
  id: string
  nombre_usuario: string
  nombre: string | null
  rol: Rol
  activo: boolean
  email: string | null
  /** 'google' | 'microsoft'; null en las cuentas antiguas con contraseña */
  proveedor: string | null
  estado_acceso: EstadoAccesoUsuario
  solicitado_en: string | null
  creado_en: string | null
}

export interface EstadoAcceso {
  modo: 'oidc'
  autenticado: boolean
  usuario: UsuarioConsola | null
}

export interface ProveedorAcceso {
  id: 'google' | 'microsoft'
  etiqueta: string
}

export const auth = {
  estado: () => pedir<EstadoAcceso>('/api/auth/estado'),
  proveedores: () => pedir<ProveedorAcceso[]>('/api/auth/proveedores'),
  /** Navegación completa: el proveedor vuelve a /api/auth/oidc/{id}/retorno y de ahí a la consola */
  entrarCon: (id: string) => window.location.assign(`/api/auth/oidc/${id}/inicio`),
  solicitar: () => pedir<UsuarioConsola>('/api/auth/solicitar', { method: 'POST' }),
  logout: () => pedir<unknown>('/api/auth/logout', { method: 'POST' }),
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
  conceder: (id: string, rol: Rol) => pedir<UsuarioConsola>(`/api/usuarios/${id}/conceder`, json({ rol })),
  denegar: (id: string) => pedir<UsuarioConsola>(`/api/usuarios/${id}/denegar`, { method: 'POST' }),
  modificar: (id: string, cambios: { rol?: Rol; activo?: boolean; nombre?: string }) =>
    pedir<UsuarioConsola>(`/api/usuarios/${id}`, json(cambios, 'PATCH')),
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

/** Lo que el asistente debería haber entendido: un valor, "*" (cualquiera), null (sin dato) o alternativas. */
export type ValorEsperado = string | boolean | null | (string | boolean | null)[]

export type BancoPruebas = 'interpretacion' | 'clasificacion'

/** Turno real del vecino marcado por calidad como caso del banco de pruebas. */
export interface CasoBanco {
  id: string
  llamada_id: string
  turno: number
  banco: BancoPruebas
  nodo: string
  pregunta: string | null
  oido: string | null
  entrada: string
  catalogo: boolean
  equipos: [string, string][] | null
  esperado: Record<string, ValorEsperado>
  nota: string | null
  autor_id: string | null
  creado_en: string
  actualizado_en: string
}

export interface NuevoCasoBanco {
  turno: number
  banco: BancoPruebas
  esperado: Record<string, ValorEsperado>
  nota?: string | null
  oido?: string | null
  pregunta?: string | null
}

export interface OpcionesCaso {
  claves: string[]
  intenciones: string[]
  urgencias: string[]
  actuaciones: string[]
  categorias: string[]
}

export interface DetalleExpediente {
  llamada: Expediente & { transcripcion: Turno[] }
  turnos: {
    turno: number
    vecino: string | null
    asistente: string[]
    nodo_inicial: string
    nodo_final: string
    tiempos: TiemposServidor
    interpretacion?: Record<string, unknown> | null
  }[]
  decisiones: { turno: number; nodo: string; accion: string; detalle: string }[]
  casos?: CasoBanco[]
  metricas: Record<string, unknown>[]
  pistas: { id: string; canal: string; duracion_ms: number | null; expira_en: string; disponible: boolean; url: string | null }[]
}

export const operaciones = {
  resumen: () => pedir<ResumenOperaciones>('/api/operaciones/resumen'),
  llamadas: (estado?: string) => pedir<Expediente[]>(`/api/operaciones/llamadas${estado ? `?estado=${estado}` : ''}`),
  detalle: (id: string) => pedir<DetalleExpediente>(`/api/operaciones/llamadas/${id}`),
  opcionesCaso: () => pedir<OpcionesCaso>('/api/operaciones/casos/opciones'),
  marcarCaso: (llamadaId: string, caso: NuevoCasoBanco) =>
    pedir<CasoBanco>(`/api/operaciones/llamadas/${llamadaId}/casos`, json(caso)),
  desmarcarCaso: (casoId: string) => pedir<void>(`/api/operaciones/casos/${casoId}`, { method: 'DELETE' }),
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
  obtener: (id: string) => pedir<VistaLlamada>(`/api/llamadas/${id}`),
  /** Tiempos por turno que guarda el servidor (los del agente de LiveKit incluidos) */
  metricasLlamada: (id: string) =>
    pedir<{ turnos: TiemposTurnoServidor[] }>(`/api/llamadas/${id}/metricas`),
  tokenLiveKit: (llamada_id: string) =>
    pedir<{ url: string; token: string; sala: string }>('/api/livekit/token', json({ llamada_id })),
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

export interface EquipoCatalogo {
  id: string
  tipo: string
  descripcion: string
  ubicacion: string | null
  marca: string | null
  modelo: string | null
  instalador: string | null
  mantenedor_id: string | null
  contrato: string | null
  garantia_hasta: string | null
}

export interface FincaDetalle extends Omit<FincaResumen, 'viviendas'> {
  viviendas: ViviendaCatalogo[]
  equipos: EquipoCatalogo[]
  configuracion_efectiva: Record<string, unknown>
  configuracion_id: string | null
  /** La finca fija sus propios profesionales: lo que se asigne al administrador no le llega. */
  asignacion_propia?: boolean
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

export interface AdministradorCatalogo {
  id: string
  nombre: string
  fincas: { id: string; direccion: string }[]
  catalogo_categorias: string[]
  especialista_por_categoria: Record<string, string>
  configuracion_id: string | null
}

export interface DatosFinca {
  direccion: string
  correo: string
  telefono_oficial: string
}

export interface DatosVivienda {
  portal: string | null
  piso: string
  puerta: string
}

export type DatosEquipo = Omit<EquipoCatalogo, 'id' | 'descripcion'>

export interface DatosEspecialista {
  nombre: string
  categoria_principal: string
  canal_contacto: 'correo' | 'llamada'
  destino: string
}

export interface BorradoEspecialista {
  borrado: string
  categorias_desasignadas: string[]
  equipos_sin_mantenedor: string[]
}

const ruta = (...partes: string[]) => `/api/catalogos/${partes.map(encodeURIComponent).join('/')}`

export const catalogos = {
  fincas: () => pedir<FincaResumen[]>('/api/catalogos/fincas'),
  finca: (id: string) => pedir<FincaDetalle>(`/api/catalogos/fincas/${encodeURIComponent(id)}`),
  especialistas: () => pedir<EspecialistaCatalogo[]>('/api/catalogos/especialistas'),
  administradores: () => pedir<AdministradorCatalogo[]>('/api/catalogos/administradores'),

  crearFinca: (datos: DatosFinca & { administrador_id: string }) => pedir<FincaDetalle>(ruta('fincas'), json(datos)),
  editarFinca: (id: string, datos: DatosFinca) => pedir<FincaDetalle>(ruta('fincas', id), json(datos, 'PUT')),
  borrarFinca: (id: string) => pedir<unknown>(ruta('fincas', id), { method: 'DELETE' }),

  crearVivienda: (finca: string, datos: DatosVivienda) => pedir<ViviendaCatalogo>(ruta('fincas', finca, 'viviendas'), json(datos)),
  editarVivienda: (finca: string, id: string, datos: DatosVivienda) =>
    pedir<ViviendaCatalogo>(ruta('fincas', finca, 'viviendas', id), json(datos, 'PUT')),
  borrarVivienda: (finca: string, id: string) => pedir<unknown>(ruta('fincas', finca, 'viviendas', id), { method: 'DELETE' }),

  crearEquipo: (finca: string, datos: DatosEquipo) => pedir<EquipoCatalogo>(ruta('fincas', finca, 'equipos'), json(datos)),
  editarEquipo: (finca: string, id: string, datos: DatosEquipo) =>
    pedir<EquipoCatalogo>(ruta('fincas', finca, 'equipos', id), json(datos, 'PUT')),
  borrarEquipo: (finca: string, id: string) => pedir<unknown>(ruta('fincas', finca, 'equipos', id), { method: 'DELETE' }),

  crearEspecialista: (datos: DatosEspecialista & { administrador_id: string }) =>
    pedir<EspecialistaCatalogo>(ruta('especialistas'), json(datos)),
  editarEspecialista: (id: string, datos: DatosEspecialista) =>
    pedir<EspecialistaCatalogo>(ruta('especialistas', id), json(datos, 'PUT')),
  borrarEspecialista: (id: string, forzar = false) =>
    pedir<BorradoEspecialista>(`${ruta('especialistas', id)}${forzar ? '?forzar=true' : ''}`, { method: 'DELETE' }),

  asignarCategoria: (admin: string, categoria: string, especialista_id: string | null) =>
    pedir<AdministradorCatalogo>(ruta('administradores', admin, 'categorias', categoria), json({ especialista_id }, 'PUT')),
}

// ---------------------------------------------------------------- bandeja del administrador

export type EstadoIncidencia = 'pendiente' | 'asignada' | 'en_curso' | 'esperando_informacion' | 'resuelta' | 'cerrada'
export type EstadoManual = 'en_curso' | 'esperando_informacion' | 'resuelta' | 'cerrada'

export interface Incidencia {
  id: string
  llamada_id: string
  finca_id: string
  vivienda_id: string
  descripcion: string
  categoria: string
  especialista_id: string | null
  urgencia: 'baja' | 'media' | 'alta' | 'emergencia'
  estado: EstadoIncidencia
  creada_en: string
  equipo_id: string | null
  actuacion: string
  sugerencia: string | null
}

export interface FilaBandeja extends Incidencia {
  pendiente_administrador: boolean
  finca_direccion: string | null
  vivienda: string | null
  especialista_nombre: string | null
  motivo_revision: string | null
}

export interface EspecialistaBandeja {
  id: string
  administrador_id: string
  nombre: string
  categoria_principal: string
  canal_contacto: string
  destino: string
}

export interface EntradaHistorial {
  id: string
  accion: 'asignar' | 'cambiar_estado' | 'nota'
  estado_anterior: EstadoIncidencia | null
  estado_nuevo: EstadoIncidencia
  usuario_id: string | null
  usuario_nombre?: string | null
  nota: string | null
  detalle: Record<string, unknown>
  cambiado_en: string
}

export interface DetalleIncidencia {
  incidencia: Incidencia
  pendiente_administrador: boolean
  finca: { id: string; direccion: string } | null
  vivienda: string | null
  llamada: {
    id: string
    iniciada_en: string
    telefono_llamante: string | null
    nombre_declarado: string | null
    resumen: string | null
    transcripcion: Turno[]
    resultado: string | null
    motivo_revision: string | null
  } | null
  equipo: (EquipoCatalogo & { mantenedor: EspecialistaBandeja | null }) | null
  especialista: EspecialistaBandeja | null
  especialistas: EspecialistaBandeja[]
  historial: EntradaHistorial[]
}

export interface FiltrosBandeja {
  estado?: EstadoIncidencia
  pendientes_administrador?: boolean
  finca_id?: string
  urgencia?: string
}

export interface ResultadoAccion {
  incidencia: Incidencia
  historial: EntradaHistorial
  aviso?: string
}

const rutaIncidencia = (id: string, accion = '') => `/api/bandeja/incidencias/${encodeURIComponent(id)}${accion}`

export const bandeja = {
  listar: (f: FiltrosBandeja = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== false && v !== '') q.set(k, String(v))
    const s = q.toString()
    return pedir<FilaBandeja[]>(`/api/bandeja/incidencias${s ? `?${s}` : ''}`)
  },
  detalle: (id: string) => pedir<DetalleIncidencia>(rutaIncidencia(id)),
  asignar: (id: string, especialista_id: string, nota?: string) =>
    pedir<ResultadoAccion>(rutaIncidencia(id, '/asignar'), json({ especialista_id, nota: nota || null })),
  estado: (id: string, estado: EstadoManual, nota?: string) =>
    pedir<ResultadoAccion>(rutaIncidencia(id, '/estado'), json({ estado, nota: nota || null })),
  nota: (id: string, texto: string) => pedir<ResultadoAccion>(rutaIncidencia(id, '/nota'), json({ texto })),
}
