import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Statistic, Tooltip, Typography } from 'antd'
import { operations, type ConversationKpis, type LatencySummary } from '../api'
import { t } from '../i18n'
import type { MessageKey } from '../i18n/messages/es'

const percent = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}`)
const number = (v: number | null) => (v === null ? '—' : `${v}`)
const pair = (l: LatencySummary) => (l.p50 === null ? '—' : `${Math.round(l.p50)} / ${Math.round(l.p95 ?? l.p50)}`)

function Kpi({ label, help, value, suffix }: { label: MessageKey; help?: MessageKey; value: string; suffix?: string }) {
  const title = help ? <Tooltip title={t(help)}><span>{t(label)}</span></Tooltip> : t(label)
  return <Statistic title={title} value={value} suffix={value === '—' ? undefined : suffix} />
}

/** Conversation KPIs of the stored calls: containment, handoff, reprompts, emergencies, turns,
 * handle time and latency. Read only; the definitions live in the backend (`kpis.py`). */
export function KpiPanel() {
  const [data, setData] = useState<{ aggregate: ConversationKpis; window: number } | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    operations
      .kpis()
      .then((d) => {
        setData(d)
        setFailed(false)
      })
      .catch(() => setFailed(true))
  }, [])

  useEffect(load, [load])

  const a = data?.aggregate
  return (
    <div>
      <div className="ops-panel-heading">
        <div>
          <p className="ops-eyebrow">{t('kpis.eyebrow')}</p>
          <h2>{t('kpis.title')}</h2>
        </div>
        <span className="ops-mono">
          {data ? t('kpis.window', { n: data.window }) : ''}{' '}
          <Button size="small" onClick={load}>{t('kpis.refresh')}</Button>
        </span>
      </div>
      {failed && <Alert type="warning" showIcon message={t('kpis.error')} />}
      {a && a.calls === 0 && <Typography.Text type="secondary">{t('kpis.noData')}</Typography.Text>}
      {a && a.calls > 0 && (
        <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
          <Kpi label="kpis.containment" help="kpis.containment.help" value={percent(a.containment_rate)} suffix="%" />
          <Kpi label="kpis.handoff" help="kpis.handoff.help" value={percent(a.handoff_rate)} suffix="%" />
          <Kpi label="kpis.needsHuman" value={percent(a.needs_human_rate)} suffix="%" />
          <Kpi label="kpis.reprompt" help="kpis.reprompt.help" value={percent(a.reprompt_rate)} suffix="%" />
          <Kpi label="kpis.emergency" value={percent(a.emergency_rate)} suffix="%" />
          <Kpi label="kpis.turns" value={number(a.turns_per_call)} />
          <Kpi label="kpis.llmCalls" value={number(a.llm_calls_per_call)} />
          <Kpi label="kpis.aht" value={number(a.avg_handle_time_s)} suffix="s" />
          <Kpi label="kpis.latency" value={pair(a.response_ms)} suffix="ms" />
          <Kpi label="kpis.serverLatency" value={pair(a.server_ms)} suffix="ms" />
        </div>
      )}
    </div>
  )
}
