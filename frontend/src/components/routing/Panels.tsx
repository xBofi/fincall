import { useState } from 'react'
import { Empty, Switch, Tag, Tooltip } from 'antd'
import { ArrowRightOutlined, InfoCircleOutlined, WarningOutlined } from '@ant-design/icons'
import type { GraphStructure, StructureNode } from './types'
import type { Step, TraceModel, TurnInfo } from './trace'
import { edgeIdOf, END_ID, stateAt } from './trace'
import { ROLE_ICON } from './roleIcons'
import { t } from './i18n'

const show = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'object' && Object.keys(v as object).length === 0) return '—'
  if (typeof v === 'object' && !Array.isArray(v)) {
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${typeof x === 'string' ? x : JSON.stringify(x)}`).join(' · ')
  }
  return JSON.stringify(v)
}

const fmt = (ms: number) => `${ms >= 100 ? Math.round(ms).toLocaleString() : ms.toFixed(1)} ms`

function NodeChip({ id, structure }: { id: string; structure: GraphStructure }) {
  const n = structure.nodes.find((x) => x.id === (id === 'end' ? END_ID : id))
  if (!n) return <span className="rv-mono">{id}</span>
  return (
    <span className={`rv-chip rv-role-${n.role}`}>
      <span aria-hidden>{ROLE_ICON[n.role]}</span>
      {n.id === END_ID ? t('routing.node.end') : n.title}
    </span>
  )
}

function Metric({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rv-metric">
      <span className="rv-metric-label">
        {label}
        {hint && (
          <Tooltip title={hint}>
            <InfoCircleOutlined className="rv-hint" aria-label={hint} />
          </Tooltip>
        )}
      </span>
      <span className="rv-metric-value">{value}</span>
    </div>
  )
}

export function DecisionPanel({
  step,
  turn,
  structure,
  running,
}: {
  step: Step | null
  turn: TurnInfo | null
  structure: GraphStructure
  running: string | null
}) {
  if (running) {
    const n = structure.nodes.find((x) => x.id === running)
    return (
      <div className="rv-panel-body" aria-live="polite">
        <p className="rv-running"><span className="rv-pulse" aria-hidden />{t('routing.decision.running', { node: n?.title ?? running })}</p>
      </div>
    )
  }
  if (!step) return <Empty className="rv-empty-panel" description={t('routing.panel.pick')} />
  const v = step.visit
  const edge = edgeIdOf(v)
  const interp = v.interpretation ?? {}
  const origin = typeof interp.origin === 'string' ? interp.origin : null
  const intent = typeof interp.intent === 'string' ? interp.intent : null
  const extracted = Object.entries(interp).filter(([k]) => !['origin', 'llm_ms', 'intent'].includes(k))
  return (
    <div className="rv-panel-body" aria-live="polite">
      <p className="rv-eyebrow">{t('routing.decision.turn', { turn: step.turn, i: step.index + 1 })}</p>
      <div className="rv-said is-caller">
        <span className="rv-said-who">{t('routing.decision.caller')}</span>
        {turn?.caller ? <q>{turn.caller}</q> : <em>{t('routing.decision.greeting')}</em>}
      </div>

      <section className="rv-section">
        <h4>{t('routing.decision.route')}</h4>
        <div className="rv-route">
          <NodeChip id={v.node} structure={structure} />
          {(edge !== null || v.finished) && (
            <>
              <ArrowRightOutlined className="rv-flip" aria-hidden />
              <NodeChip id={v.target} structure={structure} />
            </>
          )}
        </div>
        {v.synthetic ? (
          <p className="rv-muted">{t('routing.decision.synthetic')}</p>
        ) : edge === null ? (
          <p className="rv-muted">{v.finished ? t('routing.decision.ends') : t('routing.decision.waits')}</p>
        ) : v.declared && v.condition ? (
          <blockquote className="rv-condition">
            <span className="rv-condition-label">{t('routing.decision.condition')}</span>
            {v.condition}
            <small className="rv-mono"> · {t('routing.decision.declared')} · {structure.routers.edges}()</small>
          </blockquote>
        ) : (
          <p className="rv-warn"><WarningOutlined /> {t('routing.decision.undeclared')}</p>
        )}
      </section>

      {!v.synthetic && (
        <section className="rv-metrics">
          <Metric label={t('routing.decision.latency')} value={fmt(v.ms)} />
          <Metric label={t('routing.decision.llmCalls')} value={v.llm_calls} />
          <Metric label={t('routing.decision.llmMs')} value={v.llm_ms ? fmt(v.llm_ms) : '—'} />
          <Metric
            label={t('routing.decision.tokens')}
            value={<span className="rv-muted">{t('routing.decision.tokens.none')}</span>}
            hint={t('routing.decision.tokens.why')}
          />
        </section>
      )}

      {(origin || intent) && (
        <section className="rv-section">
          <div className="rv-kv">
            {origin && (
              <>
                <span>{t('routing.decision.origin')}</span>
                <span><Tag color={origin === 'deterministic' ? 'green' : 'purple'}>{t(`routing.decision.origin.${origin}`)}</Tag></span>
              </>
            )}
            {intent && (
              <>
                <span>{t('routing.decision.intent')}</span>
                <span><Tag className="rv-mono">{intent}</Tag></span>
              </>
            )}
          </div>
        </section>
      )}

      {extracted.length > 0 && (
        <section className="rv-section">
          <h4>{t('routing.decision.extracted')}</h4>
          <div className="rv-kv">
            {extracted.map(([k, val]) => (
              <FragmentKV key={k} k={k} v={show(val)} />
            ))}
          </div>
        </section>
      )}

      {v.trace.length > 0 && (
        <section className="rv-section">
          <h4>{t('routing.decision.rationale')}</h4>
          <ul className="rv-list">
            {v.trace.map((tr, i) => (
              <li key={i}><span className="rv-mono">{tr.action}</span>{tr.detail && <> — {tr.detail}</>}</li>
            ))}
          </ul>
        </section>
      )}

      {(v.validations.length > 0 || v.confidence_signals) && (
        <section className="rv-section">
          <h4>{t('routing.decision.confidence')}</h4>
          <p className="rv-muted rv-small">{t('routing.decision.confidence.none')}</p>
          <div className="rv-tags">
            {v.validations.map((x) => <Tag key={x} color={x.includes('->') ? 'gold' : 'green'}>{x}</Tag>)}
            {Object.entries(v.confidence_signals ?? {}).map(([k, val]) => (
              <Tag key={k} color={val === false ? 'gold' : undefined}>{k}: {show(val)}</Tag>
            ))}
          </div>
        </section>
      )}

      {v.said.length > 0 && (
        <div className="rv-said is-bot">
          <span className="rv-said-who">{t('routing.decision.said')}</span>
          {v.said.map((s, i) => <q key={i}>{s}</q>)}
        </div>
      )}
    </div>
  )
}

function FragmentKV({ k, v }: { k: string; v: string }) {
  return (
    <>
      <span className="rv-mono">{k}</span>
      <span>{v}</span>
    </>
  )
}

export function StatePanel({ model, cursor }: { model: TraceModel; cursor: number }) {
  const [all, setAll] = useState(false)
  const step = cursor >= 0 ? model.steps[Math.min(cursor, model.steps.length - 1)] : null
  if (!step) return <Empty className="rv-empty-panel" description={t('routing.panel.pick')} />
  const diff = Object.entries(step.visit.diff)
  const acc = all ? Object.entries(stateAt(model, cursor)) : []
  return (
    <div className="rv-panel-body">
      <label className="rv-switch">
        <Switch size="small" checked={all} onChange={setAll} />
        <span>{t('routing.state.all')}</span>
      </label>
      {all ? (
        <table className="rv-table">
          <thead><tr><th>{t('routing.state.key')}</th><th>{t('routing.state.after')}</th></tr></thead>
          <tbody>
            {acc.map(([k, val]) => (
              <tr key={k} className={k in step.visit.diff ? 'is-changed' : ''}><td className="rv-mono">{k}</td><td>{show(val)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : diff.length === 0 ? (
        <p className="rv-muted">{t('routing.state.none')}</p>
      ) : (
        <table className="rv-table">
          <caption>{t('routing.state.changed')}</caption>
          <thead><tr><th>{t('routing.state.key')}</th><th>{t('routing.state.before')}</th><th>{t('routing.state.after')}</th></tr></thead>
          <tbody>
            {diff.map(([k, d]) => (
              <tr key={k}>
                <td className="rv-mono">{k}</td>
                <td className="rv-before">{show(d.before)}</td>
                <td className="rv-after">{show(d.after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function ConversationPanel({
  turns,
  current,
  onJump,
}: {
  turns: TurnInfo[]
  current: number | null
  onJump: (turn: number) => void
}) {
  if (turns.length === 0) return <Empty className="rv-empty-panel" description={t('routing.conversation.empty')} />
  return (
    <ol className="rv-conversation">
      {turns.map((turn) => (
        <li key={turn.turn} className={turn.turn === current ? 'is-current' : ''}>
          <button
            type="button"
            className="rv-turn"
            onClick={() => onJump(turn.turn)}
            aria-label={t('routing.conversation.jump', { turn: turn.turn })}
            aria-current={turn.turn === current ? 'step' : undefined}
          >
            <span className="rv-turn-n">{t('routing.timeline.turn', { turn: turn.turn })}</span>
            {turn.caller && <span className="rv-bubble is-caller"><b>{t('routing.conversation.caller')}</b>{turn.caller}</span>}
            {turn.assistant.map((a, i) => (
              <span key={i} className="rv-bubble is-bot"><b>{t('routing.conversation.bot')}</b>{a}</span>
            ))}
          </button>
        </li>
      ))}
    </ol>
  )
}

export function NodePanel({
  node,
  structure,
  onSelect,
  extra,
}: {
  node: StructureNode | null
  structure: GraphStructure
  onSelect: (id: string) => void
  extra?: React.ReactNode
}) {
  if (!node) return <Empty className="rv-empty-panel" description={t('routing.panel.pick')} />
  const exits = structure.edges.filter((e) => e.source === node.id)
  return (
    <div className="rv-panel-body">
      <div className="rv-node-head">
        <NodeChip id={node.id} structure={structure} />
        <span className="rv-mono rv-muted">{node.id}</span>
      </div>
      {node.summary && (
        <section className="rv-section">
          <h4>{t('routing.node.summary')}</h4>
          <p>{node.summary}</p>
        </section>
      )}
      {(node.deterministic_paths ?? []).length > 0 && (
        <section className="rv-section">
          <h4>{t('routing.node.deterministic')}</h4>
          <div className="rv-tags">{node.deterministic_paths!.map((d) => <Tag key={d}>{d}</Tag>)}</div>
        </section>
      )}
      {(node.effects ?? []).length > 0 && (
        <section className="rv-section">
          <h4>{t('routing.node.effects')}</h4>
          <ul className="rv-list">{node.effects!.map((e) => <li key={e}>{e}</li>)}</ul>
        </section>
      )}
      {exits.length > 0 && (
        <section className="rv-section">
          <h4>{t('routing.node.exits')}</h4>
          <ul className="rv-list rv-exits">
            {exits.map((e) => (
              <li key={e.id}>
                <button type="button" className="rv-link" onClick={() => onSelect(e.target)}>
                  <NodeChip id={e.target} structure={structure} />
                </button>
                <span className="rv-muted"> — {e.condition}</span>
                {(e.times ?? 0) > 0 && <Tag className="rv-mono rv-times">{e.times}</Tag>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {extra}
    </div>
  )
}
