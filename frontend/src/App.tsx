import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Layout, Row, Space, Switch, Tag, Typography, message } from 'antd'
import { PhoneOutlined, ReloadOutlined, SoundOutlined } from '@ant-design/icons'
import { api, type Finca, type Salud, type VistaLlamada } from './api'
import { Conversacion } from './components/Conversacion'
import { PanelEstado } from './components/PanelEstado'
import { hablar, silenciar, ttsDisponible } from './voz'

export default function App() {
  const [salud, setSalud] = useState<Salud | null>(null)
  const [fincas, setFincas] = useState<Finca[]>([])
  const [vista, setVista] = useState<VistaLlamada | null>(null)
  const [ocupado, setOcupado] = useState(false)
  const [vozActiva, setVozActiva] = useState(ttsDisponible())
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.salud().then(setSalud).catch((e) => setError(String(e)))
    api.fincas().then(setFincas).catch(() => undefined)
  }, [])

  const aplicar = useCallback(
    (v: VistaLlamada) => {
      setVista(v)
      if (vozActiva && v.mensajes_bot.length) hablar(v.mensajes_bot.join(' '))
    },
    [vozActiva],
  )

  const iniciar = async () => {
    setError(null)
    setOcupado(true)
    silenciar()
    try {
      aplicar(await api.iniciar())
    } catch (e) {
      setError(String(e))
    } finally {
      setOcupado(false)
    }
  }

  const enviar = async (texto: string) => {
    if (!vista) return
    setOcupado(true)
    silenciar()
    // Eco inmediato del turno del vecino mientras responde el grafo
    setVista({ ...vista, historial: [...vista.historial, { rol: 'vecino', texto }], mensajes_bot: [] })
    try {
      aplicar(await api.mensaje(vista.llamada_id, texto))
    } catch (e) {
      message.error(String(e))
    } finally {
      setOcupado(false)
    }
  }

  return (
    <Layout style={{ height: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center', gap: 16, color: '#fff' }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0, flex: 1 }}>
          Building Call Assistant · Consola de validación
        </Typography.Title>
        {salud && (
          <Space>
            <Tag color={salud.modelo_disponible ? 'green' : 'red'}>{salud.modelo}</Tag>
            <Tag>{salud.repositorio}</Tag>
          </Space>
        )}
        <Space>
          <SoundOutlined />
          <Switch checked={vozActiva} onChange={setVozActiva} disabled={!ttsDisponible()} size="small" />
        </Space>
        <Button
          type="primary"
          icon={vista ? <ReloadOutlined /> : <PhoneOutlined />}
          onClick={iniciar}
          loading={ocupado && !vista}
        >
          {vista ? 'Nueva llamada' : 'Iniciar llamada'}
        </Button>
      </Layout.Header>
      <Layout.Content style={{ padding: 16, overflow: 'hidden' }}>
        {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} showIcon />}
        {salud && !salud.modelo_disponible && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`El modelo ${salud.modelo} no está disponible en Ollama. Ejecute: ollama pull ${salud.modelo}`}
          />
        )}
        <Row gutter={16} style={{ height: '100%' }}>
          <Col span={12} style={{ height: '100%' }}>
            <Card
              title="Llamada simulada"
              style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
              styles={{ body: { flex: 1, padding: 0, overflow: 'hidden' } }}
            >
              {vista ? (
                <Conversacion
                  historial={vista.historial}
                  ocupado={ocupado}
                  finalizada={vista.finalizada}
                  onEnviar={enviar}
                />
              ) : (
                <div style={{ padding: 16 }}>
                  <Typography.Paragraph>
                    Pulse <b>Iniciar llamada</b>. Puede hablar con el micrófono (Chrome/Edge) o escribir.
                    El asistente responde por voz con la síntesis del navegador.
                  </Typography.Paragraph>
                  <Typography.Text strong>Fincas de demostración</Typography.Text>
                  <ul>
                    {fincas.map((f) => (
                      <li key={f.id}>
                        {f.direccion}{' '}
                        <Typography.Text type="secondary">
                          ({f.viviendas.length} viviendas: {f.viviendas.slice(0, 4).join(', ')}…)
                        </Typography.Text>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          </Col>
          <Col span={12} style={{ height: '100%', overflowY: 'auto' }}>
            <Card title="Estado del grafo">
              <PanelEstado vista={vista} />
            </Card>
          </Col>
        </Row>
      </Layout.Content>
    </Layout>
  )
}
