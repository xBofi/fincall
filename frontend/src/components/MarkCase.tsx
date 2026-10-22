import { useEffect, useState } from 'react'
import { Alert, Form, Input, Modal, Segmented, Select, message } from 'antd'
import { ApiError, operations } from '../api'
import type { TestBank, BankCase, CallRecordDetail, CaseOptions, ExpectedValue } from '../api'

type CallRecordTurn = CallRecordDetail['turns'][number]

/** Datos que se escriben a mano. Vacío = no se comprueba · * = cualquiera · - = sin dato · a | b = alternativas */
const TEXT_FIELDS: { key: string; label: string }[] = [
  { key: 'problem', label: 'Problema' },
  { key: 'address', label: 'Dirección' },
  { key: 'entrance', label: 'Portal' },
  { key: 'floor', label: 'Piso' },
  { key: 'door', label: 'Puerta' },
  { key: 'name', label: 'Nombre' },
  { key: 'equipment', label: 'Equipo (id)' },
  { key: 'objection', label: 'Objeción' },
]

const NO_DATA = '__sin_dato__'

let optionsCache: Promise<CaseOptions> | null = null
const loadOptions = () => {
  optionsCache ??= operations.caseOptions().catch((e: unknown) => {
    optionsCache = null
    throw e
  })
  return optionsCache
}

function toText(v: ExpectedValue | undefined): string {
  if (v === undefined) return ''
  if (v === null) return '-'
  if (Array.isArray(v)) return v.map((x) => toText(x)).join(' | ')
  return String(v)
}

function fromText(text: string): ExpectedValue | undefined {
  const parts = text
    .split('|')
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => (p === '-' ? null : p))
  if (parts.length === 0) return undefined
  return parts.length === 1 ? parts[0] : parts
}

/** Selección múltiple: varias = alternativas válidas; "sin dato" = el asistente no debía rellenarla. */
function toList(v: ExpectedValue | undefined): string[] {
  if (v === undefined) return []
  const items = Array.isArray(v) ? v : [v]
  return items.map((x) => (x === null ? NO_DATA : String(x)))
}

function fromList(items: string[]): ExpectedValue | undefined {
  const values = items.map((x) => (x === NO_DATA ? null : x))
  if (values.length === 0) return undefined
  return values.length === 1 ? values[0] : values
}

const optionsFrom = (values: string[], withNoData = true) => [
  ...values.map((v) => ({ value: v, label: v.replace(/_/g, ' ') })),
  ...(withNoData ? [{ value: NO_DATA, label: 'sin dato' }] : []),
]

interface Props {
  callId: string
  turn: CallRecordTurn
  question: string | null
  previous?: BankCase
  onClose: () => void
  onSaved: () => void
}

export function MarkCase({ callId, turn, question, previous, onClose, onSaved }: Props) {
  const [options, setOptions] = useState<CaseOptions | null>(null)
  const [bank, setTestBank] = useState<TestBank>(previous?.bank ?? 'interpretation')
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(TEXT_FIELDS.map(({ key }) => [key, toText(previous?.expected[key])])),
  )
  const [lists, setLists] = useState<Record<string, string[]>>(() => ({
    intent: toList(previous?.expected.intent),
    confirms: toList(previous?.expected.confirms).map((x) => (x === NO_DATA ? x : x === 'true' ? 'si' : 'no')),
    category: toList(previous?.expected.category),
    urgency: toList(previous?.expected.urgency),
    work_type: toList(previous?.expected.work_type),
  }))
  const [heard_text, setHeard] = useState(previous?.heard_text ?? '')
  const [note, setNote] = useState(previous?.note ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadOptions()
      .then(setOptions)
      .catch(() => setError('No se han podido cargar las opciones. Recarga la página.'))
  }, [])

  const items = (key: string) => (values: string[]) => setLists((l) => ({ ...l, [key]: values }))

  function expected(): Record<string, ExpectedValue> {
    const output: Record<string, ExpectedValue> = {}
    const setValue = (key: string, v: ExpectedValue | undefined) => {
      if (v !== undefined) output[key] = v
    }
    setValue('category', fromList(lists.category))
    setValue('urgency', fromList(lists.urgency))
    if (bank === 'classification') return output
    setValue('intent', fromList(lists.intent))
    setValue('work_type', fromList(lists.work_type))
    const confirms = lists.confirms.map((x) => (x === NO_DATA ? null : x === 'si'))
    if (confirms.length) output.confirms = confirms.length === 1 ? confirms[0] : confirms
    for (const { key } of TEXT_FIELDS) setValue(key, fromText(texts[key] ?? ''))
    return output
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await operations.markCase(callId, {
        turn: turn.turn,
        bank,
        expected: expected(),
        note: note.trim() || null,
        heard_text: bank === 'interpretation' && heard_text.trim() ? heard_text.trim() : null,
      })
      message.success('Turno guardado como caso para el banco de pruebas')
      onSaved()
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'No se ha podido guardar el caso.')
    } finally {
      setSaving(false)
    }
  }

  const understood = turn.interpretation
    ? Object.entries(turn.interpretation)
        .filter(([k, v]) => v != null && k !== 'llm_ms')
        .map(([k, v]) => `${k}: ${String(v)}`)
        .join(' · ')
    : null

  return (
    <Modal
      open
      title={previous ? 'Corregir caso del banco de pruebas' : 'Marcar como caso para el banco de pruebas'}
      okText="Guardar caso"
      cancelText="Cancelar"
      confirmLoading={saving}
      onOk={() => void save()}
      onCancel={onClose}
      width={640}
    >
      <div className="caso-contexto">
        {question && (
          <p>
            <small>El asistente preguntó</small>
            {question}
          </p>
        )}
        <p>
          <small>El vecino dijo</small>
          {turn.caller}
        </p>
        {understood && (
          <p>
            <small>El asistente entendió</small>
            <code>{understood}</code>
          </p>
        )}
      </div>
      <p className="hist-nota">
        Se guarda solo el texto del turno y la pregunta del bot: nunca el teléfono, y el nombre del vecino se cambia por uno ficticio.
      </p>

      <Segmented<TestBank>
        block
        // Al corregir no se cambia de banco: crearía otro caso en vez de corregir este
        disabled={Boolean(previous)}
        value={bank}
        onChange={setTestBank}
        options={[
          { label: 'Qué debía entender del turno', value: 'interpretation' },
          { label: 'Categoría y urgencia', value: 'classification' },
        ]}
      />

      <Form layout="vertical" className="caso-form" component="div">
        <div className="caso-rejilla">
          {bank === 'interpretation' && (
            <>
              <Form.Item label="Intención">
                <Select mode="multiple" allowClear value={lists.intent} onChange={items('intent')} options={optionsFrom(options?.intents ?? [], false)} placeholder="No se comprueba" />
              </Form.Item>
              <Form.Item label="Confirma (sí/no)">
                <Select
                  mode="multiple"
                  allowClear
                  value={lists.confirms}
                  onChange={items('confirms')}
                  options={[
                    { value: 'si', label: 'sí' },
                    { value: 'no', label: 'no' },
                    { value: NO_DATA, label: 'sin dato' },
                  ]}
                  placeholder="No se comprueba"
                />
              </Form.Item>
            </>
          )}
          <Form.Item label="Categoría" required={bank === 'classification'}>
            <Select mode="multiple" maxCount={bank === 'classification' ? 1 : undefined} allowClear value={lists.category} onChange={items('category')} options={optionsFrom(options?.categories ?? [], bank === 'interpretation')} placeholder="No se comprueba" />
          </Form.Item>
          <Form.Item label="Urgency" required={bank === 'classification'}>
            <Select mode="multiple" allowClear value={lists.urgency} onChange={items('urgency')} options={optionsFrom(options?.urgencies ?? [], bank === 'interpretation')} placeholder="No se comprueba" />
          </Form.Item>
          {bank === 'interpretation' && (
            <>
              <Form.Item label="Tipo de actuación">
                <Select mode="multiple" allowClear value={lists.work_type} onChange={items('work_type')} options={optionsFrom(options?.work_types ?? [])} placeholder="No se comprueba" />
              </Form.Item>
              {TEXT_FIELDS.map(({ key, label }) => (
                <Form.Item key={key} label={label}>
                  <Input value={texts[key]} onChange={(e) => setTexts((t) => ({ ...t, [key]: e.target.value }))} placeholder="No se comprueba" />
                </Form.Item>
              ))}
            </>
          )}
        </div>
        {bank === 'interpretation' && (
          <p className="hist-nota">
            En los campos de texto: <code>*</code> = cualquier valor, <code>-</code> = sin dato, <code>a | b</code> = vale cualquiera. Varias opciones en un desplegable también son alternativas.
          </p>
        )}
        {bank === 'interpretation' && (
          <Form.Item label="Si el vecino cortó al asistente, lo que llegó a oír">
            <Input value={heard_text} onChange={(e) => setHeard(e.target.value)} placeholder="Vacío si oyó el mensaje entero" />
          </Form.Item>
        )}
        <Form.Item label="Nota para el equipo">
          <Input.TextArea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000} placeholder="Qué falló y por qué importa" />
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} />}
    </Modal>
  )
}
