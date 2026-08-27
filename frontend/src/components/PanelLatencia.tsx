import { Statistic, Table, Tag, Typography } from 'antd'

export interface TurnoMedido {
  turno: number
  modo: 'voz' | 'texto'
  stt: 'navegador' | 'servidor' | null
  vad_silencio_ms?: number
  stt_ms?: number
  red_ms?: number
  servidor_ms?: number
  interpretacion_ms?: number
  llm_llamadas?: number
  tts_inicio_ms?: number
  respuesta_ms?: number
  interrumpido?: boolean
}

export const UMBRALES: [number, string, string][] = [
  [700, 'excelente', 'green'],
  [1200, 'objetivo', 'cyan'],
  [1500, 'tolerable', 'gold'],
  [2000, 'mal', 'volcano'],
]

export function calificar(ms: number | undefined): { nombre: string; color: string } {
  if (ms === undefined) return { nombre: '—', color: 'default' }
  for (const [limite, nombre, color] of UMBRALES) if (ms <= limite) return { nombre, color }
  return { nombre: 'muy mal', color: 'red' }
}

function p(valores: number[], q: number): number | undefined {
  if (!valores.length) return undefined
  const orden = [...valores].sort((a, b) => a - b)
  return orden[Math.min(orden.length - 1, Math.max(0, Math.round(q * (orden.length - 1))))]
}

const ms = (v?: number) => (v === undefined ? '—' : `${Math.round(v)}`)

export function PanelLatencia({ turnos }: { turnos: TurnoMedido[] }) {
  const respuestas = turnos.map((t) => t.respuesta_ms).filter((v): v is number => v !== undefined)
  const p50 = p(respuestas, 0.5)
  const p95 = p(respuestas, 0.95)
  const ultimo = respuestas.at(-1)
  const cal = calificar(p50)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        <Statistic title="Última respuesta" value={ms(ultimo)} suffix="ms" />
        <Statistic
          title="p50"
          value={ms(p50)}
          suffix="ms"
          valueStyle={{ color: cal.color === 'default' ? undefined : `var(--ant-color-${cal.color}, inherit)` }}
        />
        <Statistic title="p95" value={ms(p95)} suffix="ms" />
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 14 }}>
            Calificación p50
          </Typography.Text>
          <div style={{ marginTop: 6 }}>
            <Tag color={cal.color} style={{ fontSize: 14, padding: '2px 10px' }}>
              {cal.nombre}
            </Tag>
          </div>
        </div>
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Respuesta = fin de la voz del vecino → inicio del audio del asistente. Umbrales: ≤700 excelente ·
        ≤1200 objetivo · ≤1500 tolerable · ≤2000 mal · &gt;2000 muy mal.
      </Typography.Text>
      <Table
        size="small"
        pagination={false}
        rowKey="turno"
        dataSource={[...turnos].reverse()}
        scroll={{ y: 220 }}
        columns={[
          { title: '#', dataIndex: 'turno', width: 40 },
          {
            title: 'Modo',
            width: 90,
            render: (_, t) => (t.modo === 'texto' ? <Tag>texto</Tag> : <Tag color="blue">{t.stt}</Tag>),
          },
          { title: 'VAD', dataIndex: 'vad_silencio_ms', width: 60, render: ms },
          { title: 'STT', dataIndex: 'stt_ms', width: 60, render: ms },
          {
            title: 'Servidor',
            width: 110,
            render: (_, t) => (
              <span>
                {ms(t.servidor_ms)}
                {t.llm_llamadas ? (
                  <Typography.Text type="secondary"> (LLM {ms(t.interpretacion_ms)})</Typography.Text>
                ) : (
                  <Typography.Text type="secondary"> (0 LLM)</Typography.Text>
                )}
              </span>
            ),
          },
          { title: 'Red', dataIndex: 'red_ms', width: 55, render: ms },
          { title: 'TTS', dataIndex: 'tts_inicio_ms', width: 55, render: ms },
          {
            title: 'Respuesta',
            width: 130,
            render: (_, t) => {
              const c = calificar(t.respuesta_ms)
              return (
                <span>
                  <b>{ms(t.respuesta_ms)}</b> {t.respuesta_ms !== undefined && <Tag color={c.color}>{c.nombre}</Tag>}
                  {t.interrumpido && <Tag>cortado</Tag>}
                </span>
              )
            },
          },
        ]}
      />
    </div>
  )
}
