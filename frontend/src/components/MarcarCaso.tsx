import { useEffect, useState } from 'react'
import { Alert, Form, Input, Modal, Segmented, Select, message } from 'antd'
import { ApiError, operaciones } from '../api'
import type { BancoPruebas, CasoBanco, DetalleExpediente, OpcionesCaso, ValorEsperado } from '../api'

type TurnoExpediente = DetalleExpediente['turnos'][number]

/** Datos que se escriben a mano. Vacío = no se comprueba · * = cualquiera · - = sin dato · a | b = alternativas */
const CAMPOS_TEXTO: { clave: string; etiqueta: string }[] = [
  { clave: 'problema', etiqueta: 'Problema' },
  { clave: 'direccion', etiqueta: 'Dirección' },
  { clave: 'portal', etiqueta: 'Portal' },
  { clave: 'piso', etiqueta: 'Piso' },
  { clave: 'puerta', etiqueta: 'Puerta' },
  { clave: 'nombre', etiqueta: 'Nombre' },
  { clave: 'equipo', etiqueta: 'Equipo (id)' },
  { clave: 'objecion', etiqueta: 'Objeción' },
]

const SIN_DATO = '__sin_dato__'

let opcionesCache: Promise<OpcionesCaso> | null = null
const cargarOpciones = () => {
  opcionesCache ??= operaciones.opcionesCaso().catch((e: unknown) => {
    opcionesCache = null
    throw e
  })
  return opcionesCache
}

function aTexto(v: ValorEsperado | undefined): string {
  if (v === undefined) return ''
  if (v === null) return '-'
  if (Array.isArray(v)) return v.map((x) => aTexto(x)).join(' | ')
  return String(v)
}

function deTexto(texto: string): ValorEsperado | undefined {
  const partes = texto
    .split('|')
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => (p === '-' ? null : p))
  if (partes.length === 0) return undefined
  return partes.length === 1 ? partes[0] : partes
}

/** Selección múltiple: varias = alternativas válidas; "sin dato" = el asistente no debía rellenarla. */
function aLista(v: ValorEsperado | undefined): string[] {
  if (v === undefined) return []
  const lista = Array.isArray(v) ? v : [v]
  return lista.map((x) => (x === null ? SIN_DATO : String(x)))
}

function deLista(lista: string[]): ValorEsperado | undefined {
  const valores = lista.map((x) => (x === SIN_DATO ? null : x))
  if (valores.length === 0) return undefined
  return valores.length === 1 ? valores[0] : valores
}

const opcionesDe = (valores: string[], conSinDato = true) => [
  ...valores.map((v) => ({ value: v, label: v.replace(/_/g, ' ') })),
  ...(conSinDato ? [{ value: SIN_DATO, label: 'sin dato' }] : []),
]

interface Props {
  llamadaId: string
  turno: TurnoExpediente
  pregunta: string | null
  previo?: CasoBanco
  onCerrar: () => void
  onGuardado: () => void
}

export function MarcarCaso({ llamadaId, turno, pregunta, previo, onCerrar, onGuardado }: Props) {
  const [opciones, setOpciones] = useState<OpcionesCaso | null>(null)
  const [banco, setBanco] = useState<BancoPruebas>(previo?.banco ?? 'interpretacion')
  const [textos, setTextos] = useState<Record<string, string>>(() =>
    Object.fromEntries(CAMPOS_TEXTO.map(({ clave }) => [clave, aTexto(previo?.esperado[clave])])),
  )
  const [listas, setListas] = useState<Record<string, string[]>>(() => ({
    intencion: aLista(previo?.esperado.intencion),
    confirma: aLista(previo?.esperado.confirma).map((x) => (x === SIN_DATO ? x : x === 'true' ? 'si' : 'no')),
    categoria: aLista(previo?.esperado.categoria),
    urgencia: aLista(previo?.esperado.urgencia),
    actuacion: aLista(previo?.esperado.actuacion),
  }))
  const [oido, setOido] = useState(previo?.oido ?? '')
  const [nota, setNota] = useState(previo?.nota ?? '')
  const [guardando, setGuardando] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    cargarOpciones()
      .then(setOpciones)
      .catch(() => setError('No se han podido cargar las opciones. Recarga la página.'))
  }, [])

  const lista = (clave: string) => (valores: string[]) => setListas((l) => ({ ...l, [clave]: valores }))

  function esperado(): Record<string, ValorEsperado> {
    const salida: Record<string, ValorEsperado> = {}
    const poner = (clave: string, v: ValorEsperado | undefined) => {
      if (v !== undefined) salida[clave] = v
    }
    poner('categoria', deLista(listas.categoria))
    poner('urgencia', deLista(listas.urgencia))
    if (banco === 'clasificacion') return salida
    poner('intencion', deLista(listas.intencion))
    poner('actuacion', deLista(listas.actuacion))
    const confirma = listas.confirma.map((x) => (x === SIN_DATO ? null : x === 'si'))
    if (confirma.length) salida.confirma = confirma.length === 1 ? confirma[0] : confirma
    for (const { clave } of CAMPOS_TEXTO) poner(clave, deTexto(textos[clave] ?? ''))
    return salida
  }

  async function guardar() {
    setGuardando(true)
    setError(null)
    try {
      await operaciones.marcarCaso(llamadaId, {
        turno: turno.turno,
        banco,
        esperado: esperado(),
        nota: nota.trim() || null,
        oido: banco === 'interpretacion' && oido.trim() ? oido.trim() : null,
      })
      message.success('Turno guardado como caso para el banco de pruebas')
      onGuardado()
    } catch (e) {
      setError(e instanceof ApiError ? e.detalle : 'No se ha podido guardar el caso.')
    } finally {
      setGuardando(false)
    }
  }

  const entendido = turno.interpretacion
    ? Object.entries(turno.interpretacion)
        .filter(([k, v]) => v != null && k !== 'llm_ms')
        .map(([k, v]) => `${k}: ${String(v)}`)
        .join(' · ')
    : null

  return (
    <Modal
      open
      title={previo ? 'Corregir caso del banco de pruebas' : 'Marcar como caso para el banco de pruebas'}
      okText="Guardar caso"
      cancelText="Cancelar"
      confirmLoading={guardando}
      onOk={() => void guardar()}
      onCancel={onCerrar}
      width={640}
    >
      <div className="caso-contexto">
        {pregunta && (
          <p>
            <small>El asistente preguntó</small>
            {pregunta}
          </p>
        )}
        <p>
          <small>El vecino dijo</small>
          {turno.vecino}
        </p>
        {entendido && (
          <p>
            <small>El asistente entendió</small>
            <code>{entendido}</code>
          </p>
        )}
      </div>
      <p className="hist-nota">
        Se guarda solo el texto del turno y la pregunta del bot: nunca el teléfono, y el nombre del vecino se cambia por uno ficticio.
      </p>

      <Segmented<BancoPruebas>
        block
        // Al corregir no se cambia de banco: crearía otro caso en vez de corregir este
        disabled={Boolean(previo)}
        value={banco}
        onChange={setBanco}
        options={[
          { label: 'Qué debía entender del turno', value: 'interpretacion' },
          { label: 'Categoría y urgencia', value: 'clasificacion' },
        ]}
      />

      <Form layout="vertical" className="caso-form" component="div">
        <div className="caso-rejilla">
          {banco === 'interpretacion' && (
            <>
              <Form.Item label="Intención">
                <Select mode="multiple" allowClear value={listas.intencion} onChange={lista('intencion')} options={opcionesDe(opciones?.intenciones ?? [], false)} placeholder="No se comprueba" />
              </Form.Item>
              <Form.Item label="Confirma (sí/no)">
                <Select
                  mode="multiple"
                  allowClear
                  value={listas.confirma}
                  onChange={lista('confirma')}
                  options={[
                    { value: 'si', label: 'sí' },
                    { value: 'no', label: 'no' },
                    { value: SIN_DATO, label: 'sin dato' },
                  ]}
                  placeholder="No se comprueba"
                />
              </Form.Item>
            </>
          )}
          <Form.Item label="Categoría" required={banco === 'clasificacion'}>
            <Select mode="multiple" maxCount={banco === 'clasificacion' ? 1 : undefined} allowClear value={listas.categoria} onChange={lista('categoria')} options={opcionesDe(opciones?.categorias ?? [], banco === 'interpretacion')} placeholder="No se comprueba" />
          </Form.Item>
          <Form.Item label="Urgencia" required={banco === 'clasificacion'}>
            <Select mode="multiple" allowClear value={listas.urgencia} onChange={lista('urgencia')} options={opcionesDe(opciones?.urgencias ?? [], banco === 'interpretacion')} placeholder="No se comprueba" />
          </Form.Item>
          {banco === 'interpretacion' && (
            <>
              <Form.Item label="Tipo de actuación">
                <Select mode="multiple" allowClear value={listas.actuacion} onChange={lista('actuacion')} options={opcionesDe(opciones?.actuaciones ?? [])} placeholder="No se comprueba" />
              </Form.Item>
              {CAMPOS_TEXTO.map(({ clave, etiqueta }) => (
                <Form.Item key={clave} label={etiqueta}>
                  <Input value={textos[clave]} onChange={(e) => setTextos((t) => ({ ...t, [clave]: e.target.value }))} placeholder="No se comprueba" />
                </Form.Item>
              ))}
            </>
          )}
        </div>
        {banco === 'interpretacion' && (
          <p className="hist-nota">
            En los campos de texto: <code>*</code> = cualquier valor, <code>-</code> = sin dato, <code>a | b</code> = vale cualquiera. Varias opciones en un desplegable también son alternativas.
          </p>
        )}
        {banco === 'interpretacion' && (
          <Form.Item label="Si el vecino cortó al asistente, lo que llegó a oír">
            <Input value={oido} onChange={(e) => setOido(e.target.value)} placeholder="Vacío si oyó el mensaje entero" />
          </Form.Item>
        )}
        <Form.Item label="Nota para el equipo">
          <Input.TextArea value={nota} onChange={(e) => setNota(e.target.value)} rows={2} maxLength={2000} placeholder="Qué falló y por qué importa" />
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} />}
    </Modal>
  )
}
