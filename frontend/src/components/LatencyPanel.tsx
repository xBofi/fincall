import { Statistic, Table, Tag, Typography } from 'antd'
import { rate } from '../texts'
import { t } from '../i18n'

export interface MeasuredTurn {
  turn: number
  mode: 'voice' | 'text'
  stt: 'browser' | 'server' | null
  vad_silence_ms?: number
  stt_ms?: number
  network_ms?: number
  server_ms?: number
  interpretation_ms?: number
  llm_calls?: number
  tts_start_ms?: number
  response_ms?: number
  interrupted?: boolean
}

function p(values: number[], q: number): number | undefined {
  if (!values.length) return undefined
  const order = [...values].sort((a, b) => a - b)
  return order[Math.min(order.length - 1, Math.max(0, Math.round(q * (order.length - 1))))]
}

const ms = (v?: number) => (v === undefined ? '—' : `${Math.round(v)}`)

export function LatencyPanel({ turns }: { turns: MeasuredTurn[] }) {
  const responses = turns.map((t) => t.response_ms).filter((v): v is number => v !== undefined)
  const p50 = p(responses, 0.5)
  const p95 = p(responses, 0.95)
  const last = responses.at(-1)
  const cal = rate(p50)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        <Statistic title={t('latency.last')} value={ms(last)} suffix="ms" />
        <Statistic
          title="p50"
          value={ms(p50)}
          suffix="ms"
          valueStyle={{ color: cal.color === 'default' ? undefined : `var(--ant-color-${cal.color}, inherit)` }}
        />
        <Statistic title="p95" value={ms(p95)} suffix="ms" />
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 14 }}>
            {t('latency.rating')}
          </Typography.Text>
          <div style={{ marginTop: 6 }}>
            <Tag color={cal.color} style={{ fontSize: 14, padding: '2px 10px' }}>
              {cal.name}
            </Tag>
          </div>
        </div>
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t('latency.legend')}
      </Typography.Text>
      <Table
        size="small"
        pagination={false}
        rowKey="turn"
        dataSource={[...turns].reverse()}
        scroll={{ y: 220 }}
        columns={[
          { title: '#', dataIndex: 'turn', width: 40 },
          {
            title: t('latency.col.mode'),
            width: 90,
            render: (_, row) => (row.mode === 'text' ? <Tag>{t('latency.text')}</Tag> : <Tag color="blue">{row.stt}</Tag>),
          },
          { title: 'VAD', dataIndex: 'vad_silence_ms', width: 60, render: ms },
          { title: 'STT', dataIndex: 'stt_ms', width: 60, render: ms },
          {
            title: t('latency.col.server'),
            width: 110,
            render: (_, row) => (
              <span>
                {ms(row.server_ms)}
                {row.llm_calls ? (
                  <Typography.Text type="secondary"> (LLM {ms(row.interpretation_ms)})</Typography.Text>
                ) : (
                  <Typography.Text type="secondary"> (0 LLM)</Typography.Text>
                )}
              </span>
            ),
          },
          { title: t('latency.col.network'), dataIndex: 'network_ms', width: 55, render: ms },
          { title: 'TTS', dataIndex: 'tts_start_ms', width: 55, render: ms },
          {
            title: t('latency.col.response'),
            width: 130,
            render: (_, row) => {
              const c = rate(row.response_ms)
              return (
                <span>
                  <b>{ms(row.response_ms)}</b> {row.response_ms !== undefined && <Tag color={c.color}>{c.name}</Tag>}
                  {row.interrupted && <Tag>{t('latency.cut')}</Tag>}
                </span>
              )
            },
          },
        ]}
      />
    </div>
  )
}
