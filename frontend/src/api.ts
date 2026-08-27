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

export interface VistaLlamada {
  llamada_id: string
  nodo_actual: string
  esperando_usuario: boolean
  finalizada: boolean
  mensajes_bot: string[]
  historial: Turno[]
  traza: Traza[]
  validaciones: string[]
  senales_confianza: Record<string, unknown> | null
  configuracion: Record<string, unknown> | null
  datos: DatosLlamada
}

export interface Salud {
  estado: string
  repositorio: string
  modelo: string
  modelo_disponible: boolean
  grafos: string[]
}

export interface Finca {
  id: string
  direccion: string
  correo: string
  telefono_oficial: string
  viviendas: string[]
}

async function pedir<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return (await r.json()) as T
}

export const api = {
  salud: () => pedir<Salud>('/api/salud'),
  fincas: () => pedir<Finca[]>('/api/fincas'),
  iniciar: (telefono?: string) =>
    pedir<VistaLlamada>('/api/llamadas', {
      method: 'POST',
      body: JSON.stringify({ telefono_llamante: telefono ?? null }),
    }),
  mensaje: (id: string, texto: string) =>
    pedir<VistaLlamada>(`/api/llamadas/${id}/mensajes`, {
      method: 'POST',
      body: JSON.stringify({ texto }),
    }),
}
