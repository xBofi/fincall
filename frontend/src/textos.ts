/**
 * Etiquetas legibles para lo que el backend devuelve como códigos.
 *
 * La consola se enseña a clientes: ningún código interno (`incidencia_creada`, `fontaneria`) debe
 * verse tal cual. Un código sin traducción se capitaliza, nunca se oculta.
 */

export function capitalizar(codigo: string): string {
  const t = codigo.replace(/_/g, ' ').trim()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

const CATEGORIAS: Record<string, string> = {
  fontaneria: 'Fontanería',
  electricidad: 'Electricidad',
  ascensor: 'Ascensor',
  limpieza: 'Limpieza',
  cerrajeria: 'Cerrajería',
  albanileria: 'Albañilería',
  pintura: 'Pintura',
  jardineria: 'Jardinería',
  mantenimiento_general: 'Mantenimiento general',
  administrativo: 'Gestión administrativa',
  no_clasificable: 'Sin clasificar',
}

export const categoria = (c: string | null | undefined): string => (c ? CATEGORIAS[c] ?? capitalizar(c) : '—')

const URGENCIA: Record<string, { texto: string; color: string }> = {
  baja: { texto: 'Baja', color: 'green' },
  media: { texto: 'Media', color: 'gold' },
  alta: { texto: 'Alta', color: 'volcano' },
  emergencia: { texto: 'Emergencia', color: 'red' },
}

export const urgencia = (u: string | null | undefined) =>
  u ? URGENCIA[u] ?? { texto: capitalizar(u), color: 'default' } : null

export type Tono = 'bien' | 'revisar' | 'neutro'

const RESULTADOS: Record<string, { corto: string; largo: string; tono: Tono }> = {
  incidencia_creada: {
    corto: 'Incidencia creada',
    largo: 'Incidencia creada y profesional avisado.',
    tono: 'bien',
  },
  finca_no_identificada: {
    corto: 'Finca no encontrada',
    largo: 'La dirección no corresponde a ninguna finca dada de alta. Se guardó el mensaje para revisarlo.',
    tono: 'revisar',
  },
  vivienda_no_identificada: {
    corto: 'Vivienda no encontrada',
    largo: 'No se pudo identificar la vivienda. Se recogió el problema y se avisó al administrador por correo.',
    tono: 'revisar',
  },
  no_clasificable: {
    corto: 'Pendiente de revisión',
    largo: 'Faltaban datos para crear la incidencia. Se envió un resumen al administrador para revisarla.',
    tono: 'revisar',
  },
  derivada_administrador: {
    corto: 'Derivada a una persona',
    largo: 'El vecino pidió hablar con una persona. Se avisó al administrador.',
    tono: 'revisar',
  },
  abandonada: {
    corto: 'Colgó antes de terminar',
    largo: 'El vecino se despidió antes de terminar. La llamada queda registrada, sin incidencia.',
    tono: 'neutro',
  },
}

export const resultado = (r: string | null | undefined) =>
  r ? RESULTADOS[r] ?? { corto: capitalizar(r), largo: capitalizar(r), tono: 'neutro' as Tono } : null

const CANALES: Record<string, string> = {
  correo: 'correo',
  llamada: 'llamada',
  ticket: 'ticket',
}

export const canal = (c: string | null | undefined): string => (c ? CANALES[c] ?? c : '—')

/** Destino de una regla de aviso, ya con su preposición: un profesional concreto, el asignado o un rol. */
export function destinoRegla(destino: string, nombrePorId: Record<string, string>): string {
  if (destino === 'especialista') return 'al profesional asignado'
  if (destino === 'coordinador') return 'al correo de la finca'
  if (destino === 'administrador') return 'al administrador'
  return `a ${nombrePorId[destino] ?? destino}`
}

export function etiquetaVivienda(v: { portal: string | null; piso: string; puerta: string }): string {
  return `${v.portal ? `Portal ${v.portal} · ` : ''}${v.piso}º ${v.puerta}`
}

/** Error técnico → frase que se entiende en una demo. El detalle queda en la consola del navegador. */
export function mensajeError(e: unknown): string {
  console.error(e)
  if (e instanceof DOMException) {
    if (e.name === 'NotAllowedError' || e.name === 'SecurityError')
      return 'El navegador no deja usar el micrófono: revisa los permisos de la página.'
    if (e.name === 'NotFoundError') return 'No se ha encontrado ningún micrófono.'
  }
  if (e instanceof TypeError) return 'No hay conexión con el servidor. Comprueba que está en marcha.'
  const { status, detalle } = (typeof e === 'object' && e !== null ? e : {}) as { status?: number; detalle?: string }
  if (status === 401 || status === 403) return 'La sesión ha caducado o no tienes permiso. Vuelve a entrar.'
  if (status === 404) return 'No se ha encontrado: puede que la llamada ya haya terminado.'
  if (status !== undefined && status >= 500) return 'El servidor ha tenido un problema. Inténtalo de nuevo.'
  if (status !== undefined && detalle) return detalle
  return 'Algo ha fallado. Inténtalo de nuevo.'
}

/** Tiempo de respuesta (fin de voz del vecino → empieza a hablar el bot). Objetivo ≤ 1200 ms. */
const UMBRALES_LATENCIA: [number, string, string][] = [
  [700, 'excelente', 'green'],
  [1200, 'dentro del objetivo', 'cyan'],
  [1500, 'tolerable', 'gold'],
  [2000, 'lento', 'volcano'],
]

export function calificar(ms: number | null | undefined): { nombre: string; color: string } {
  if (ms == null) return { nombre: '—', color: 'default' }
  for (const [limite, nombre, color] of UMBRALES_LATENCIA) if (ms <= limite) return { nombre, color }
  return { nombre: 'muy lento', color: 'red' }
}

const ESTADOS_INCIDENCIA: Record<string, { texto: string; color: string }> = {
  pendiente: { texto: 'Pendiente', color: 'orange' },
  asignada: { texto: 'Asignada', color: 'blue' },
  en_curso: { texto: 'En curso', color: 'processing' },
  esperando_informacion: { texto: 'Esperando información', color: 'gold' },
  resuelta: { texto: 'Resuelta', color: 'green' },
  cerrada: { texto: 'Cerrada', color: 'default' },
}

export const estadoIncidencia = (e: string) => ESTADOS_INCIDENCIA[e] ?? { texto: capitalizar(e), color: 'default' }

const ACTUACIONES: Record<string, string> = {
  reparacion: 'Reparación',
  reposicion: 'Reposición',
  siniestro: 'Posible siniestro',
  convivencia: 'Convivencia',
  privativo: 'Zona privativa',
}

export const actuacion = (a: string | null | undefined): string => (a ? ACTUACIONES[a] ?? capitalizar(a) : '—')

/** Por qué una incidencia espera al administrador: `actuacion:siniestro`, `objecion_vecino:…`… */
export function motivoRevision(m: string | null | undefined): string | null {
  if (!m) return null
  const [codigo, ...resto] = m.split(':')
  const valor = resto.join(':').trim()
  switch (codigo) {
    case 'actuacion':
      return `No es una reparación: ${actuacion(valor).toLowerCase()}.`
    case 'objecion_vecino':
      return `El vecino discrepa del resumen: «${valor}».`
    case 'especialista_inexistente':
      return 'El profesional configurado para esta avería ya no existe.'
    case 'sin_especialista_para_categoria':
      return 'Ningún profesional cubre este tipo de avería.'
    default:
      return capitalizar(m)
  }
}

export const ROLES: Record<string, string> = {
  owner: 'Propietario',
  administrador: 'Administrador',
  calidad: 'Calidad',
}
