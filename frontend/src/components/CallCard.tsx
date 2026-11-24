import type { ReactNode } from 'react'
import { Collapse, Tag } from 'antd'
import { CheckCircleFilled, ExclamationCircleFilled, InfoCircleFilled, LoadingOutlined } from '@ant-design/icons'
import type { CallView } from '../api'
import { channel, category, result, urgency } from '../texts'
import { StatePanel } from './StatePanel'
import { t } from '../i18n'

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
  const target = d.notice_target && d.notice_target !== d.specialist_name ? t('card.noticeTo', { target: d.notice_target }) : ''
  const notice = d.notice_channel
    ? t('card.noticeVia', { who: d.specialist_name ?? t('card.administrator'), channel: channel(d.notice_channel), target })
    : null
  return [
    { title: t('card.estate'), value: d.estate_address, nodes: ['identify_estate', 'estate_not_identified'], note: d.estate_attempts ? t('card.attempts', { n: d.estate_attempts }) : null },
    { title: t('card.home'), value: d.home_label, nodes: ['identify_home', 'home_not_identified', 'confirm_home'], note: d.home_attempts ? t('card.attempts', { n: d.home_attempts }) : null },
    { title: t('card.request'), value: d.request, nodes: ['collect_request', 'ask_detail'] },
    { title: t('card.category'), value: d.category ? category(d.category) : null, nodes: ['classify_problem'] },
    {
      title: t('card.urgency'),
      value: u ? (
        <>
          <Tag color={u.color}>{u.text}</Tag>
          {d.declared_urgency && d.declared_urgency !== d.urgency && (
            <small className="card-note">{t('card.callerSaid', { urgency: urgency(d.declared_urgency)?.text ?? '' })}</small>
          )}
        </>
      ) : null,
      nodes: ['determine_urgency'],
    },
    // Emergencia con el tratamiento activado: indicación de seguridad en lugar del resumen largo
    ...(d.urgency === 'emergency'
      ? [{ title: t('card.safety'), value: d.emergency_handled ? t('card.safetyGiven') : null, nodes: ['handle_emergency'] }]
      : []),
    { title: t('card.confirmed'), value: d.confirmed ? t('card.confirmedYes') : null, nodes: ['confirm_summary'] },
    { title: t('card.incident'), value: d.incident_id ? t('card.incidentCreated', { id: d.incident_id }) : null, nodes: ['validate_data', 'create_ticket'] },
    { title: t('card.notice'), value: notice, nodes: ['assign_specialist', 'notify_channel'], note: d.suggestion },
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
  pending: <span className="card-dot" />,
  skipped: <span className="card-dot" />,
}

export function CallCard({ view }: { view: CallView | null }) {
  if (!view) {
    return (
      <div className="card-empty">
        <p>{t('card.empty.intro')}</p>
        <ol>
          <li>{t('card.empty.1')}</li>
          <li>{t('card.empty.2')}</li>
          <li>{t('card.empty.3')}</li>
          <li>{t('card.empty.4')}</li>
        </ol>
        <p>{t('card.empty.check')}</p>
      </div>
    )
  }

  const items = steps(view)
  const r = result(view.data.result)

  return (
    <div className="card">
      {view.language && (
        <small className="card-language">
          {t('card.language')}: <b>{view.language.toUpperCase()}</b>
          {view.persona ? ` · ${view.persona}` : ''}
        </small>
      )}
      {r && view.finished && (
        <div className={`card-result is-${r.tone}`}>
          {r.tone === 'good' ? <CheckCircleFilled /> : r.tone === 'review' ? <ExclamationCircleFilled /> : <InfoCircleFilled />}
          <div>
            <b>{r.short}</b>
            <p>{r.long}</p>
          </div>
        </div>
      )}

      <ol className="card-steps">
        {items.map((p) => {
          const e = stateOf(p, view)
          return (
            <li key={p.title} className={`is-${e}`}>
              <span className="card-icon">{ICON[e]}</span>
              <div>
                <span className="card-title">
                  {p.title}
                  {e === 'now' && <em>{t('card.askingNow')}</em>}
                </span>
                <span className="card-value">{p.value ?? (e === 'skipped' ? t('card.notReached') : t('card.pending'))}</span>
                {p.note && <small className="card-note">{p.note}</small>}
              </div>
            </li>
          )
        })}
      </ol>

      {view.data.summary && (
        <div className="card-summary">
          <span>{t('card.summary')}</span>
          <p dir="auto">{view.data.summary}</p>
        </div>
      )}

      <Collapse
        size="small"
        ghost
        className="card-technical"
        items={[
          {
            key: 'technical',
            label: t('card.technical'),
            children: <StatePanel view={view} />,
          },
        ]}
      />
    </div>
  )
}
