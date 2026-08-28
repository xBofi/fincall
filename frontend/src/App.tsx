import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Col, Layout, Row, Segmented, Slider, Space, Switch, Tabs, Tag, Typography, message } from 'antd'
import { PhoneOutlined, ReloadOutlined, SoundOutlined } from '@ant-design/icons'
import { api, type Finca, type Salud, type VistaLlamada } from './api'
import { Conversacion, type EstadoVoz } from './components/Conversacion'
import { PanelEstado } from './components/PanelEstado'
import { PanelLatencia, type TurnoMedido } from './components/PanelLatencia'
import { abrirStreaming, type SesionStreaming } from './streaming'
import {
  VAD_POR_DEFECTO,
  SinVozError,
  capturarTurno,
  capturaDisponible,
  hablar,
  reconocerEnNavegador,
  silenciar,
  sttNavegadorDisponible,
  ttsDisponible,
} from './voz'

type ModoStt = 'navegador' | 'servidor' | 'streaming'

export default function App() {
  const [salud, setSalud] = useState<Salud | null>(null)
  const [fincas, setFincas] = useState<Finca[]>([])
  const [vista, setVista] = useState<VistaLlamada | null>(null)
  const [ocupado, setOcupado] = useState(false)
  const [estadoVoz, setEstadoVoz] = useState<EstadoVoz>('inactivo')
  const [parcial, setParcial] = useState<string | null>(null)
  const [vozActiva, setVozActiva] = useState(ttsDisponible())
  const [manosLibres, setManosLibres] = useState(true)
  const [bargeIn, setBargeIn] = useState(true)
  const [modoStt, setModoStt] = useState<ModoStt>('navegador')
  const [silencioMs, setSilencioMs] = useState(VAD_POR_DEFECTO.silencioMs)
  const [turnos, setTurnos] = useState<TurnoMedido[]>([])
  const [error, setError] = useState<string | null>(null)
  const [streamingInfo, setStreamingInfo] = useState<string | null>(null)

  // Referencias para el bucle de voz (evitan closures obsoletas)
  const vistaRef = useRef<VistaLlamada | null>(null)
  const cancelarEscuchaRef = useRef<() => void>(() => undefined)
  const cancelarHablaRef = useRef<() => void>(() => undefined)
  const streamingRef = useRef<SesionStreaming | null>(null)
  const bucleActivoRef = useRef(false)
  const opcionesRef = useRef({ vozActiva, manosLibres, bargeIn, modoStt, silencioMs })
  opcionesRef.current = { vozActiva, manosLibres, bargeIn, modoStt, silencioMs }

  useEffect(() => {
    api
      .salud()
      .then((s) => {
        setSalud(s)
        if (s.stt_streaming) setModoStt('streaming')
        else if (s.stt_disponible && !sttNavegadorDisponible()) setModoStt('servidor')
      })
      .catch((e) => setError(String(e)))
    api.fincas().then(setFincas).catch(() => undefined)
  }, [])

  const registrarTurno = useCallback((t: TurnoMedido) => {
    setTurnos((prev) => [...prev.filter((x) => x.turno !== t.turno), t])
  }, [])

  /** Reproduce la respuesta y, si procede, vuelve a escuchar. */
  const responder = useCallback(
    async (v: VistaLlamada, finVozAt: number | null, medicion: Partial<TurnoMedido>): Promise<void> => {
      vistaRef.current = v
      setVista(v)
      const texto = v.mensajes_bot.join(' ')
      let ttsInicioAt: number | null = null
      let interrumpida = false
      const recibidoAt = performance.now()
      const modo = opcionesRef.current.modoStt
      if (opcionesRef.current.vozActiva && texto) {
        setEstadoVoz('respondiendo')
        streamingRef.current?.pausar()
        const rep = hablar(texto, opcionesRef.current.bargeIn && !v.finalizada)
        cancelarHablaRef.current = rep.cancelar
        ttsInicioAt = await rep.inicio
        const fin = await rep.fin
        interrumpida = fin.interrumpida
        streamingRef.current?.reanudar()
      }
      const t: TurnoMedido = {
        turno: v.turno,
        modo: finVozAt === null ? 'texto' : 'voz',
        stt: finVozAt === null ? null : modo === 'streaming' ? 'servidor' : modo,
        servidor_ms: v.tiempos.servidor_ms,
        interpretacion_ms: v.tiempos.interpretacion_ms,
        llm_llamadas: v.tiempos.llm_llamadas,
        stt_ms: medicion.stt_ms ?? v.tiempos.stt_ms,
        vad_silencio_ms: medicion.vad_silencio_ms,
        red_ms: medicion.red_ms,
        tts_inicio_ms: ttsInicioAt !== null ? Math.round(ttsInicioAt - recibidoAt) : undefined,
        respuesta_ms: finVozAt !== null && ttsInicioAt !== null ? Math.round(ttsInicioAt - finVozAt) : undefined,
        interrumpido: interrumpida,
      }
      if (v.turno > 0) {
        registrarTurno(t)
        api
          .metricas(v.llamada_id, {
            turno: v.turno,
            vad_silencio_ms: t.vad_silencio_ms,
            red_ms: t.red_ms,
            tts_inicio_ms: t.tts_inicio_ms,
            respuesta_ms: t.respuesta_ms,
            stt_cliente: modo,
            extra: { interrumpido: interrumpida, modo: t.modo },
          })
          .catch(() => undefined)
      }
      if (v.finalizada) {
        streamingRef.current?.cerrar()
        streamingRef.current = null
      }
      setEstadoVoz(streamingRef.current && !v.finalizada ? 'esperando_voz' : 'inactivo')
      if (!v.finalizada && opcionesRef.current.manosLibres && bucleActivoRef.current && modo !== 'streaming') {
        void escuchar()
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [registrarTurno],
  )

  const enviarTexto = useCallback(
    async (texto: string) => {
      const v = vistaRef.current
      if (!v) return
      setOcupado(true)
      silenciar()
      setVista({ ...v, historial: [...v.historial, { rol: 'vecino', texto }], mensajes_bot: [] })
      try {
        const t0 = performance.now()
        const nueva = await api.mensaje(v.llamada_id, texto)
        const red = Math.round(performance.now() - t0 - (nueva.tiempos.servidor_ms ?? 0))
        await responder(nueva, null, { red_ms: Math.max(0, red) })
      } catch (e) {
        message.error(String(e))
        setEstadoVoz('inactivo')
      } finally {
        setOcupado(false)
      }
    },
    [responder],
  )

  /** Modo streaming: una sesión por llamada; el proveedor detecta el fin de turno. */
  const abrirStreamingLlamada = useCallback(
    async (llamadaId: string) => {
      streamingRef.current?.cerrar()
      let finVozTurno = 0
      streamingRef.current = await abrirStreaming(llamadaId, {
        onListo: (ms) => {
          setStreamingInfo(`STT streaming conectado (${ms ?? '?'} ms)`)
          setEstadoVoz('esperando_voz')
        },
        onParcial: (t) => setParcial(t),
        onFinal: (t, ultimaVozAt) => {
          finVozTurno = ultimaVozAt
          setParcial(t)
          setEstadoVoz('procesando')
        },
        onRespuesta: (v, ultimaVozAt) => {
          setParcial(null)
          void responder(v, finVozTurno || ultimaVozAt || null, {
            stt_ms: v.tiempos.stt_eou_a_respuesta_ms !== undefined ? undefined : undefined,
          })
        },
        onError: (m) => message.error(m),
        onVoz: (hablando) => setEstadoVoz(hablando ? 'hablando' : 'esperando_voz'),
        onCerrado: () => setStreamingInfo(null),
      })
    },
    [responder],
  )

  /** Un turno de escucha (modos navegador/servidor): captura -> STT -> grafo -> respuesta hablada. */
  const escuchar = useCallback(async () => {
    const v = vistaRef.current
    if (!v || v.finalizada) return
    const { modoStt: modo, silencioMs: silencio } = opcionesRef.current
    if (modo === 'streaming') {
      if (!streamingRef.current) await abrirStreamingLlamada(v.llamada_id)
      return
    }
    setParcial(null)
    try {
      let texto = ''
      let finVozAt = 0
      let sttMs: number | undefined
      let vadMs: number | undefined
      let respuesta: VistaLlamada
      let t0 = 0
      if (modo === 'servidor') {
        const cap = capturarTurno({ ...VAD_POR_DEFECTO, silencioMs: silencio }, setEstadoVoz)
        cancelarEscuchaRef.current = cap.cancelar
        const c = await cap.resultado
        finVozAt = c.finVozAt
        vadMs = Math.round(c.decididoAt - c.finVozAt)
        setEstadoVoz('procesando')
        setParcial('…')
        t0 = performance.now()
        respuesta = await api.audio(v.llamada_id, c.blob, c.mime)
        sttMs = respuesta.tiempos.stt_ms
        texto = respuesta.transcripcion ?? ''
        if (respuesta.sin_voz) {
          setParcial(null)
          setEstadoVoz('inactivo')
          if (opcionesRef.current.manosLibres && bucleActivoRef.current) void escuchar()
          return
        }
      } else {
        const rec = reconocerEnNavegador(setEstadoVoz)
        cancelarEscuchaRef.current = rec.cancelar
        const r = await rec.resultado
        finVozAt = r.finVozAt
        sttMs = Math.round(r.resultadoAt - r.finVozAt)
        texto = r.texto
        setEstadoVoz('procesando')
        setParcial(texto)
        t0 = performance.now()
        respuesta = await api.mensaje(v.llamada_id, texto)
      }
      const red = Math.round(performance.now() - t0 - (respuesta.tiempos.servidor_ms ?? 0))
      setParcial(null)
      await responder(respuesta, finVozAt, { stt_ms: sttMs, vad_silencio_ms: vadMs, red_ms: Math.max(0, red) })
    } catch (e) {
      setParcial(null)
      setEstadoVoz('inactivo')
      if (e instanceof SinVozError) {
        if (opcionesRef.current.manosLibres && bucleActivoRef.current && !vistaRef.current?.finalizada) void escuchar()
        return
      }
      if (e instanceof Error && e.message === 'cancelado') return
      message.error(String(e))
    }
  }, [responder, abrirStreamingLlamada])

  const iniciar = async () => {
    setError(null)
    setOcupado(true)
    cancelarEscuchaRef.current()
    cancelarHablaRef.current()
    streamingRef.current?.cerrar()
    streamingRef.current = null
    silenciar()
    setTurnos([])
    bucleActivoRef.current = true
    try {
      const v = await api.iniciar()
      if (opcionesRef.current.modoStt === 'streaming' && opcionesRef.current.manosLibres) {
        // Abrimos el STT mientras suena el saludo para que la conexión no cueste latencia
        void abrirStreamingLlamada(v.llamada_id).catch((e) => message.error(String(e)))
      }
      await responder(v, null, {})
    } catch (e) {
      setError(String(e))
    } finally {
      setOcupado(false)
    }
  }

  const colgar = () => {
    bucleActivoRef.current = false
    cancelarEscuchaRef.current()
    cancelarHablaRef.current()
    streamingRef.current?.cerrar()
    streamingRef.current = null
    silenciar()
    setEstadoVoz('inactivo')
  }

  const sttServidorOk = !!salud?.stt_disponible
  const sttStreamingOk = !!salud?.stt_streaming
  const sttNavegadorOk = sttNavegadorDisponible()

  return (
    <Layout style={{ height: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center', gap: 16, color: '#fff' }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0, flex: 1 }}>
          Building Call Assistant · Consola de validación
        </Typography.Title>
        {salud && (
          <Space>
            <Tag color={salud.modelo_disponible ? 'green' : 'red'}>{salud.modelo}</Tag>
            {salud.stt_streaming && <Tag color="green">STT streaming: {salud.stt_streaming}</Tag>}
            <Tag color={salud.stt_disponible ? 'green' : 'default'}>STT lotes: {salud.stt_servidor}</Tag>
          </Space>
        )}
        <Button type="primary" icon={vista ? <ReloadOutlined /> : <PhoneOutlined />} onClick={iniciar} loading={ocupado && !vista}>
          {vista ? 'Nueva llamada' : 'Iniciar llamada'}
        </Button>
        {vista && !vista.finalizada && <Button danger onClick={colgar}>Colgar</Button>}
      </Layout.Header>
      <Layout.Content style={{ padding: 16, overflow: 'hidden' }}>
        {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} showIcon />}
        {salud && !salud.modelo_disponible && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={`El modelo ${salud.modelo} no está disponible. Revise Ollama o las credenciales de Workers AI.`} />
        )}
        <Row gutter={16} style={{ height: '100%' }}>
          <Col span={12} style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Card size="small" title="Pipeline de voz" styles={{ body: { padding: '8px 12px' } }}>
              <Space wrap size="middle">
                <Space>
                  <SoundOutlined />
                  <Switch checked={vozActiva} onChange={setVozActiva} disabled={!ttsDisponible()} size="small" />
                  <Typography.Text>Voz del asistente</Typography.Text>
                </Space>
                <Space>
                  <Switch checked={manosLibres} onChange={setManosLibres} size="small" />
                  <Typography.Text>Manos libres</Typography.Text>
                </Space>
                <Space>
                  <Switch checked={bargeIn} onChange={setBargeIn} size="small" />
                  <Typography.Text>Interrumpible</Typography.Text>
                </Space>
                <Space>
                  <Typography.Text>STT</Typography.Text>
                  <Segmented
                    size="small"
                    value={modoStt}
                    onChange={(v) => setModoStt(v as ModoStt)}
                    options={[
                      { label: 'Streaming (AssemblyAI EU)', value: 'streaming', disabled: !sttStreamingOk },
                      { label: 'Navegador', value: 'navegador', disabled: !sttNavegadorOk },
                      { label: 'Whisper', value: 'servidor', disabled: !sttServidorOk || !capturaDisponible() },
                    ]}
                  />
                </Space>
                {modoStt === 'servidor' && (
                  <Space style={{ minWidth: 220 }}>
                    <Typography.Text>Silencio VAD {silencioMs} ms</Typography.Text>
                    <Slider min={300} max={1200} step={50} value={silencioMs} onChange={setSilencioMs} style={{ width: 120 }} />
                  </Space>
                )}
                {streamingInfo && <Tag color="blue">{streamingInfo}</Tag>}
              </Space>
            </Card>
            <Card
              title="Llamada simulada"
              style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
              styles={{ body: { flex: 1, padding: 0, overflow: 'hidden' } }}
            >
              {vista ? (
                <Conversacion
                  historial={vista.historial}
                  parcial={parcial}
                  estadoVoz={estadoVoz}
                  ocupado={ocupado}
                  finalizada={vista.finalizada}
                  manosLibres={manosLibres}
                  onEnviar={enviarTexto}
                  onEscuchar={() => void escuchar()}
                />
              ) : (
                <div style={{ padding: 16 }}>
                  <Typography.Paragraph>
                    Pulse <b>Iniciar llamada</b>. El asistente saluda por voz y, en <b>manos libres</b>, escucha
                    automáticamente. En modo <b>Streaming</b> el fin de turno lo detecta AssemblyAI (EU) en tiempo real.
                    Puede interrumpirle hablando. También puede escribir.
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
            <Tabs
              items={[
                { key: 'latencia', label: 'Latencia', children: <Card><PanelLatencia turnos={turnos} /></Card> },
                { key: 'estado', label: 'Estado del grafo', children: <Card><PanelEstado vista={vista} /></Card> },
              ]}
            />
          </Col>
        </Row>
      </Layout.Content>
    </Layout>
  )
}
