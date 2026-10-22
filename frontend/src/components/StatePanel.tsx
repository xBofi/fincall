import { Collapse, Descriptions, Steps, Tag, Timeline, Typography } from 'antd'
import type { CallView } from '../api'

const MAIN_NODES = [
  'identify_estate',
  'identify_home',
  'collect_request',
  'classify_problem',
  'determine_urgency',
  'handle_emergency',
  'confirm_summary',
  'validate_data',
  'assign_specialist',
  'create_ticket',
  'notify_channel',
]

const URGENCY_COLOR: Record<string, string> = {
  baja: 'green',
  medium: 'gold',
  high: 'volcano',
  emergency: 'red',
}

const RESULT_COLOR: Record<string, string> = {
  incident_created: 'green',
  estate_not_identified: 'red',
  home_not_identified: 'orange',
  unclassifiable: 'orange',
}

function value(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'sí' : 'no'
  return String(v)
}

export function StatePanel({ view }: { view: CallView | null }) {
  if (!view) {
    return <Typography.Text type="secondary">Inicie una llamada para ver el estado del grafo.</Typography.Text>
  }
  const d = view.data
  const idx = MAIN_NODES.indexOf(view.current_node)
  const currentStep = view.finished ? MAIN_NODES.length : Math.max(idx, 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <Typography.Text strong>Nodo actual: </Typography.Text>
        <Tag color={view.finished ? 'default' : 'processing'}>{view.current_node}</Tag>
        {d.result && <Tag color={RESULT_COLOR[d.result] ?? 'default'}>{d.result}</Tag>}
        <Tag>grafo: {value(d.graph)}</Tag>
      </div>

      <Steps
        size="small"
        direction="vertical"
        current={currentStep}
        status={d.result && d.result !== 'incident_created' ? 'error' : undefined}
        items={MAIN_NODES.map((n) => ({ title: n }))}
        style={{ maxHeight: 260, overflowY: 'auto' }}
      />

      <Descriptions size="small" column={1} bordered title="Extracción y decisiones">
        <Descriptions.Item label="Finca">
          {value(d.estate_address)} {d.estate_attempts ? <Tag>intentos: {d.estate_attempts}</Tag> : null}
        </Descriptions.Item>
        <Descriptions.Item label="Vivienda">
          {value(d.home_label)} {d.home_attempts ? <Tag>intentos: {d.home_attempts}</Tag> : null}
        </Descriptions.Item>
        <Descriptions.Item label="Nombre">{value(d.declared_name)}</Descriptions.Item>
        <Descriptions.Item label="Solicitud">{value(d.request)}</Descriptions.Item>
        <Descriptions.Item label="Resumen">{value(d.summary)}</Descriptions.Item>
        <Descriptions.Item label="Categoría">{value(d.category)}</Descriptions.Item>
        <Descriptions.Item label="Urgencia">
          {d.urgency ? <Tag color={URGENCY_COLOR[d.urgency]}>{d.urgency}</Tag> : '—'}
          {d.declared_urgency && d.declared_urgency !== d.urgency && (
            <Typography.Text type="secondary"> (declarada: {d.declared_urgency})</Typography.Text>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="Confirmado">{value(d.confirmed)}</Descriptions.Item>
        <Descriptions.Item label="Especialista">{value(d.specialist_name)}</Descriptions.Item>
        <Descriptions.Item label="Aviso">
          {d.notice_channel ? `${d.notice_channel} → ${value(d.notice_target)}` : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="Incidencia">{value(d.incident_id)}</Descriptions.Item>
        <Descriptions.Item label="Config. aplicada">{value(d.config_id)}</Descriptions.Item>
      </Descriptions>

      <div>
        <Typography.Text strong>Validaciones</Typography.Text>
        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {view.validations.length === 0 && <Typography.Text type="secondary">—</Typography.Text>}
          {view.validations.map((v, i) => (
            <Tag key={i} color={v.startsWith('fallo:') ? 'red' : 'green'}>
              {v}
            </Tag>
          ))}
        </div>
      </div>

      <Collapse
        size="small"
        items={[
          {
            key: 'trace',
            label: `Traza del grafo (${view.trace.length})`,
            children: (
              <Timeline
                items={view.trace.map((t) => ({
                  children: (
                    <span>
                      <Typography.Text code>{t.node}</Typography.Text> {t.action}
                      {t.detail && <Typography.Text type="secondary"> · {t.detail}</Typography.Text>}
                    </span>
                  ),
                }))}
              />
            ),
          },
          {
            key: 'interpretation',
            label: `Interpretación del último turno${view.interpretation?.origin ? ` (${String(view.interpretation.origin)}${view.interpretation.llm_ms ? `, ${String(view.interpretation.llm_ms)} ms` : ''})` : ''}`,
            children: (
              <div>
                <pre style={{ margin: 0 }}>{JSON.stringify(view.interpretation, null, 2)}</pre>
                {Object.keys(view.pending ?? {}).length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <Typography.Text type="secondary">Datos dichos pendientes de usar: </Typography.Text>
                    {Object.entries(view.pending).map(([k, v]) => (
                      <Tag key={k}>
                        {k}: {v}
                      </Tag>
                    ))}
                  </div>
                )}
              </div>
            ),
          },
          {
            key: 'confidence',
            label: 'Señales de confianza del clasificador',
            children: <pre style={{ margin: 0 }}>{JSON.stringify(view.confidence_signals, null, 2)}</pre>,
          },
          {
            key: 'config',
            label: 'Configuración efectiva de la finca',
            children: <pre style={{ margin: 0 }}>{JSON.stringify(view.config, null, 2)}</pre>,
          },
        ]}
      />
    </div>
  )
}
