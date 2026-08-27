import { Collapse, Descriptions, Steps, Tag, Timeline, Typography } from 'antd'
import type { VistaLlamada } from '../api'

const NODOS_PRINCIPALES = [
  'identificar_finca',
  'identificar_vivienda',
  'registrar_nombre',
  'recoger_solicitud',
  'clasificar_problema',
  'determinar_urgencia',
  'confirmar_resumen',
  'validar_datos',
  'asignar_especialista',
  'crear_ticket',
  'avisar_canal',
]

const COLOR_URGENCIA: Record<string, string> = {
  baja: 'green',
  media: 'gold',
  alta: 'volcano',
  emergencia: 'red',
}

const COLOR_RESULTADO: Record<string, string> = {
  incidencia_creada: 'green',
  finca_no_identificada: 'red',
  vivienda_no_identificada: 'orange',
  no_clasificable: 'orange',
}

function valor(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'sí' : 'no'
  return String(v)
}

export function PanelEstado({ vista }: { vista: VistaLlamada | null }) {
  if (!vista) {
    return <Typography.Text type="secondary">Inicie una llamada para ver el estado del grafo.</Typography.Text>
  }
  const d = vista.datos
  const idx = NODOS_PRINCIPALES.indexOf(vista.nodo_actual)
  const pasoActual = vista.finalizada ? NODOS_PRINCIPALES.length : Math.max(idx, 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <Typography.Text strong>Nodo actual: </Typography.Text>
        <Tag color={vista.finalizada ? 'default' : 'processing'}>{vista.nodo_actual}</Tag>
        {d.resultado && <Tag color={COLOR_RESULTADO[d.resultado] ?? 'default'}>{d.resultado}</Tag>}
        <Tag>grafo: {valor(d.grafo)}</Tag>
      </div>

      <Steps
        size="small"
        direction="vertical"
        current={pasoActual}
        status={d.resultado && d.resultado !== 'incidencia_creada' ? 'error' : undefined}
        items={NODOS_PRINCIPALES.map((n) => ({ title: n }))}
        style={{ maxHeight: 260, overflowY: 'auto' }}
      />

      <Descriptions size="small" column={1} bordered title="Extracción y decisiones">
        <Descriptions.Item label="Finca">
          {valor(d.finca_direccion)} {d.intentos_finca ? <Tag>intentos: {d.intentos_finca}</Tag> : null}
        </Descriptions.Item>
        <Descriptions.Item label="Vivienda">
          {valor(d.vivienda_etiqueta)} {d.intentos_vivienda ? <Tag>intentos: {d.intentos_vivienda}</Tag> : null}
        </Descriptions.Item>
        <Descriptions.Item label="Nombre">{valor(d.nombre_declarado)}</Descriptions.Item>
        <Descriptions.Item label="Solicitud">{valor(d.solicitud)}</Descriptions.Item>
        <Descriptions.Item label="Resumen">{valor(d.resumen)}</Descriptions.Item>
        <Descriptions.Item label="Categoría">{valor(d.categoria)}</Descriptions.Item>
        <Descriptions.Item label="Urgencia">
          {d.urgencia ? <Tag color={COLOR_URGENCIA[d.urgencia]}>{d.urgencia}</Tag> : '—'}
          {d.urgencia_declarada && d.urgencia_declarada !== d.urgencia && (
            <Typography.Text type="secondary"> (declarada: {d.urgencia_declarada})</Typography.Text>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="Confirmado">{valor(d.confirmado)}</Descriptions.Item>
        <Descriptions.Item label="Especialista">{valor(d.especialista_nombre)}</Descriptions.Item>
        <Descriptions.Item label="Aviso">
          {d.canal_aviso ? `${d.canal_aviso} → ${valor(d.destino_aviso)}` : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="Incidencia">{valor(d.incidencia_id)}</Descriptions.Item>
        <Descriptions.Item label="Config. aplicada">{valor(d.configuracion_id)}</Descriptions.Item>
      </Descriptions>

      <div>
        <Typography.Text strong>Validaciones</Typography.Text>
        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {vista.validaciones.length === 0 && <Typography.Text type="secondary">—</Typography.Text>}
          {vista.validaciones.map((v, i) => (
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
            key: 'traza',
            label: `Traza del grafo (${vista.traza.length})`,
            children: (
              <Timeline
                items={vista.traza.map((t) => ({
                  children: (
                    <span>
                      <Typography.Text code>{t.nodo}</Typography.Text> {t.accion}
                      {t.detalle && <Typography.Text type="secondary"> · {t.detalle}</Typography.Text>}
                    </span>
                  ),
                }))}
              />
            ),
          },
          {
            key: 'interpretacion',
            label: `Interpretación del último turno${vista.interpretacion?.origen ? ` (${String(vista.interpretacion.origen)}${vista.interpretacion.llm_ms ? `, ${String(vista.interpretacion.llm_ms)} ms` : ''})` : ''}`,
            children: (
              <div>
                <pre style={{ margin: 0 }}>{JSON.stringify(vista.interpretacion, null, 2)}</pre>
                {Object.keys(vista.pendientes ?? {}).length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <Typography.Text type="secondary">Datos dichos pendientes de usar: </Typography.Text>
                    {Object.entries(vista.pendientes).map(([k, v]) => (
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
            key: 'confianza',
            label: 'Señales de confianza del clasificador',
            children: <pre style={{ margin: 0 }}>{JSON.stringify(vista.senales_confianza, null, 2)}</pre>,
          },
          {
            key: 'config',
            label: 'Configuración efectiva de la finca',
            children: <pre style={{ margin: 0 }}>{JSON.stringify(vista.configuracion, null, 2)}</pre>,
          },
        ]}
      />
    </div>
  )
}
