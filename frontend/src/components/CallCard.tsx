import type { ReactNode } from 'react'
import { Collapse, Tag } from 'antd'
import { CheckCircleFilled, ExclamationCircleFilled, InfoCircleFilled, LoadingOutlined } from '@ant-design/icons'
import type { CallView } from '../api'
import { channel, category, result, urgency } from '../texts'
import { StatePanel } from './StatePanel'

type State = 'done' | 'now' | 'pending' | 'skipped'

interface Step {
  title: string
  value: ReactNode | null
  /** Nodos del grafo que preguntan por este dato: si es el actual, el bot lo está preguntando. */
  nodes: string[]
  note?: string | null
}

function steps(v: CallView): Step[] {
  const d = v.data
  const u = urgency(d.urgency)
  // Con ticket, el destino es el propio profesional: no se repite su nombre
  const target = d.notice_target && d.notice_target !== d.specialist_name ? ` a ${d.notice_target}` : ''
  const notice = d.notice_channel ? `${d.specialist_name ?? 'Administrator'} · por ${channel(d.notice_channel)}${target}` : null
  return [
    { title: 'Estate', value: d.estate_address, nodes: ['identify_estate', 'estate_not_identified'], note: d.estate_attempts ? `${d.estate_attempts} intento(s)` : null },
    { title: 'Home', value: d.home_label, nodes: ['identify_home', 'home_not_identified'], note: d.home_attempts ? `${d.home_attempts} intento(s)` : null },
    // El nombre no se pregunta (basta la vivienda): solo aparece si el vecino lo dijo
    ...(d.declared_name ? [{ title: 'Nombre del vecino', value: d.declared_name, nodes: [] }] : []),
    { title: 'Qué le ocurre', value: d.request, nodes: ['collect_request', 'ask_detail'] },
    { title: 'Tipo de avería', value: d.category ? category(d.category) : null, nodes: ['classify_problem'] },
    {
      title: 'Urgency',
      value: u ? (
        <>
          <Tag color={u.color}>{u.text}</Tag>
          {d.declared_urgency && d.declared_urgency !== d.urgency && (
            <small className="ficha-nota">el vecino dijo «{urgency(d.declared_urgency)?.text}»</small>
          )}
        </>
      ) : null,
      nodes: ['determine_urgency'],
    },
    // Emergencia con el tratamiento activado: indicación de seguridad en lugar del resumen largo
    ...(d.urgency === 'emergency'
      ? [{ title: 'Indicación de seguridad', value: d.emergency_handled ? 'Dada · emergencia registrada' : null, nodes: ['handle_emergency'] }]
      : []),
    { title: 'Resumen confirmado', value: d.confirmed ? 'Sí, el vecino lo confirmó' : null, nodes: ['confirm_summary'] },
    { title: 'Incident', value: d.incident_id ? `Creada · ref. ${d.incident_id}` : null, nodes: ['validate_data', 'create_ticket'] },
    { title: 'Aviso (simulado)', value: notice, nodes: ['assign_specialist', 'notify_channel'], note: d.suggestion },
  ]
}

function stateOf(p: Step, v: CallView): State {
  if (p.value) return 'done'
  if (v.finished) return 'skipped'
  return p.nodes.includes(v.current_node) ? 'now' : 'pending'
}

const ICON: Record<State, ReactNode> = {
  done: <CheckCircleFilled />,
  now: <LoadingOutlined />,
  pending: <span className="ficha-punto" />,
  skipped: <span className="ficha-punto" />,
}

export function CallCard({ view }: { view: CallView | null }) {
  if (!view) {
    return (
      <div className="ficha-vacia">
        <p>Aquí verás, en tiempo real, lo que el asistente va entendiendo:</p>
        <ol>
          <li>la finca y la vivienda desde la que llaman,</li>
          <li>quién llama y qué le ocurre,</li>
          <li>el tipo de avería y su urgencia,</li>
          <li>y a qué profesional se avisa.</li>
        </ol>
        <p>Cada dato se puede comprobar en «Fincas y profesionales».</p>
      </div>
    )
  }

  const items = steps(view)
  const r = result(view.data.result)

  return (
    <div className="ficha">
      {r && view.finished && (
        <div className={`ficha-resultado is-${r.tone}`}>
          {r.tone === 'bien' ? <CheckCircleFilled /> : r.tone === 'review' ? <ExclamationCircleFilled /> : <InfoCircleFilled />}
          <div>
            <b>{r.short}</b>
            <p>{r.long}</p>
          </div>
        </div>
      )}

      <ol className="ficha-pasos">
        {items.map((p) => {
          const e = stateOf(p, view)
          return (
            <li key={p.title} className={`is-${e}`}>
              <span className="ficha-icono">{ICON[e]}</span>
              <div>
                <span className="ficha-titulo">
                  {p.title}
                  {e === 'now' && <em>preguntando ahora</em>}
                </span>
                <span className="ficha-valor">{p.value ?? (e === 'skipped' ? 'No se llegó a este paso' : 'Pendiente')}</span>
                {p.note && <small className="ficha-nota">{p.note}</small>}
              </div>
            </li>
          )
        })}
      </ol>

      {view.data.summary && (
        <div className="ficha-resumen">
          <span>Resumen que se envía</span>
          <p>{view.data.summary}</p>
        </div>
      )}

      <Collapse
        size="small"
        ghost
        className="ficha-tecnico"
        items={[
          {
            key: 'technical',
            label: 'Detalle técnico (para el equipo)',
            children: <StatePanel view={view} />,
          },
        ]}
      />
    </div>
  )
}
