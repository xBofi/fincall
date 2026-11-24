import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Badge, Button, Dropdown, Popover, Segmented, Slider, Spin, Switch, Tooltip, message } from 'antd'
import {
  CheckOutlined,
  CloseOutlined,
  LogoutOutlined,
  PhoneOutlined,
  ReloadOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons'
import {
  api,
  auth,
  inbox,
  operations,
  telephony,
  users as apiUsers,
  type CallRecordDetail,
  type TelephonyStatus,
  type AccessState,
  type ConsoleUser,
  type CallRecord,
  type OperationsSummary,
  type Health,
  type CallView,
} from './api'
import Logo from './components/Logo'
import { PendingAccess } from './components/PendingAccess'
import { Conversation, type VoiceState } from './components/Conversation'
import { Login } from './components/Login'
import { Database } from './components/Database'
import { CallCard } from './components/CallCard'
import { History } from './components/History'
import { StatePanel } from './components/StatePanel'
import type { MeasuredTurn } from './components/LatencyPanel'
import { useCatalog } from './catalog'
import type { LiveKitSession } from './livekit'
import type { TwilioSession } from './twilioCall'
import { openStreaming, type StreamingSession } from './streaming'
import { ROLES, errorMessage } from './texts'
import {
  DEFAULT_VAD,
  NoVoiceError,
  captureTurn,
  captureAvailable,
  speak,
  recognizeInBrowser,
  mute,
  sttBrowserAvailable,
  ttsAvailable,
  enableServerVoice,
} from './voice'

// Pantallas que no se usan al simular: se descargan al abrirlas (el arranque pesa menos)
const UserManagement = lazy(() => import('./components/UserManagement').then((m) => ({ default: m.UserManagement })))
const AccessRequests = lazy(() => import('./components/AccessRequests').then((m) => ({ default: m.AccessRequests })))
const FlowPanel = lazy(() => import('./components/FlowPanel').then((m) => ({ default: m.FlowPanel })))
const Inbox = lazy(() => import('./components/Inbox').then((m) => ({ default: m.Inbox })))
const LatencyPanel = lazy(() => import('./components/LatencyPanel').then((m) => ({ default: m.LatencyPanel })))

type SttMode = 'browser' | 'server' | 'streaming' | 'livekit' | 'twilio'
type Screen = 'simulate' | 'history' | 'inbox' | 'data' | 'flow' | 'latency' | 'users' | 'requests'

/** Lo que se enseña al cliente antes de la primera llamada. Hechos del grafo, no promesas. */
/** El barge-in (cortar al bot cuando el vecino habla) está siempre activo en la consola. */
const BARGE_IN = true

const CAN = [
  'Identifica la finca por la dirección, aunque el vecino la diga con otras palabras.',
  'Localiza la vivienda por portal, piso y puerta.',
  'Entiende el problema, lo clasifica por tipo de avería y decide la urgencia.',
  'Lee un resumen al vecino y le pide confirmación antes de crear nada.',
  'Crea la incidencia y avisa al profesional que la finca tiene asignado para esa avería.',
  'Responde dudas frecuentes (¿se graba la llamada?, ¿cuánto tardarán?).',
]
const CANNOT = [
  'No atiende fincas que no estén dadas de alta: guarda el mensaje para revisarlo.',
  'Sin la vivienda identificada no crea incidencia: avisa al administrador.',
  'Si ningún profesional cubre esa avería, la incidencia queda pendiente para el administrador.',
  'Si el vecino pide hablar con una persona, no transfiere la llamada: avisa al administrador.',
  'No promete hora de visita: todavía nadie ha hablado con el profesional.',
]

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [view, setView] = useState<CallView | null>(null)
  const [busy, setBusy] = useState(false)
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [partial, setPartial] = useState<string | null>(null)
  const [voiceEnabled, setVoiceActive] = useState(ttsAvailable())
  const [handsFree, setHandsFree] = useState(true)
  const [sttMode, setSttMode] = useState<SttMode>('browser')
  const [silenceMs, setSilenceMs] = useState(DEFAULT_VAD.silenceMs)
  const [turns, setTurns] = useState<MeasuredTurn[]>([])
  const [error, setError] = useState<string | null>(null)
  const [streamingInfo, setStreamingInfo] = useState<string | null>(null)
  // Consola: acceso, indicadores, historial de llamadas y la llamada abierta en el historial
  const [access, setAccess] = useState<AccessState | null>(null)
  const [opsSummary, setOpsSummary] = useState<OperationsSummary | null>(null)
  const [callRecords, setCallRecords] = useState<CallRecord[]>([])
  const [selected, setSelected] = useState<CallRecordDetail | null>(null)
  const [screen, setScreen] = useState<Screen>('simulate')
  const [tel, setTel] = useState<TelephonyStatus | null>(null)
  const [requests, setRequests] = useState(0)
  // Incidencias que esperan una decisión del administrador (contador de la pestaña Bandeja)
  const [inboxPending, setInboxPending] = useState(0)

  const refreshOperations = useCallback(async () => {
    try {
      const state = await auth.state()
      setAccess(state)
      if (!state.authenticated || state.user?.access_state !== 'granted') {
        setOpsSummary(null)
        setCallRecords([])
        return
      }
      // El owner ve cuántas personas esperan acceso en la pestaña Solicitudes
      if (state.user.role === 'owner') {
        apiUsers
          .list_all()
          .then((todos) => setRequests(todos.filter((u) => u.access_state === 'requested').length))
          .catch(() => undefined)
      }
      if (state.user.role === 'owner' || state.user.role === 'administrator') {
        inbox
          .list_all({ pending_administrator_only: true })
          .then((rows) => setInboxPending(rows.length))
          .catch(() => undefined)
      }
      const [summary, items] = await Promise.all([operations.summary(), operations.calls()])
      setOpsSummary(summary)
      setCallRecords(items)
      telephony.state().then(setTel).catch(() => undefined)
    } catch {
      // Backend caído: se mantiene el último estado conocido
    }
  }, [])

  const personUpdated = useCallback((user: ConsoleUser) => {
    setAccess({ mode: 'oidc', authenticated: true, user })
  }, [])

  const openCallRecord = useCallback(async (id: string) => {
    try {
      setSelected(await operations.detail(id))
    } catch (e) {
      message.error(errorMessage(e))
    }
  }, [])

  // Referencias para el bucle de voz (evitan closures obsoletas)
  const viewRef = useRef<CallView | null>(null)
  const cancelListeningRef = useRef<() => void>(() => undefined)
  const cancelSpeakingRef = useRef<() => void>(() => undefined)
  const streamingRef = useRef<StreamingSession | null>(null)
  const livekitRef = useRef<LiveKitSession | null>(null)
  const twilioRef = useRef<TwilioSession | null>(null)
  const loopActiveRef = useRef(false)
  // Cada llamada (o colgar) invalida lo que siga en vuelo de la anterior: micrófono, WebSocket
  const generationRef = useRef(0)
  const openingStreamingRef = useRef<Promise<void> | null>(null)
  const optionsRef = useRef({ voiceEnabled, handsFree, sttMode, silenceMs })
  optionsRef.current = { voiceEnabled, handsFree, sttMode, silenceMs }

  useEffect(() => {
    api
      .health()
      .then((s) => {
        setHealth(s)
        enableServerVoice(!!s.server_tts)
        if (s.stt_streaming) setSttMode('streaming')
        else if (s.stt_available && !sttBrowserAvailable()) setSttMode('server')
      })
      .catch((e) => setError(errorMessage(e)))
    void refreshOperations()
  }, [refreshOperations])

  // El historial se refresca mientras está a la vista, no tras cada turno de voz: así no compite
  // con la respuesta del asistente y recoge también las llamadas que entren por teléfono
  useEffect(() => {
    if (screen !== 'history') return
    void refreshOperations()
    const id = window.setInterval(() => {
      if (!document.hidden) void refreshOperations()
    }, 15_000)
    return () => window.clearInterval(id)
  }, [screen, refreshOperations])

  const recordTurn = useCallback((t: MeasuredTurn) => {
    setTurns((prev) => [...prev.filter((x) => x.turn !== t.turn), t])
  }, [])

  /** Reproduce la respuesta y, si procede, vuelve a escuchar. */
  const respond = useCallback(
    async (v: CallView, voiceEndAt: number | null, measurement: Partial<MeasuredTurn>): Promise<void> => {
      viewRef.current = v
      setView(v)
      const text = v.bot_messages.join(' ')
      let ttsStartAt: number | null = null
      let interrupted = false
      const receivedAt = performance.now()
      const mode = optionsRef.current.sttMode
      if (optionsRef.current.voiceEnabled && text) {
        setVoiceState('respondiendo')
        streamingRef.current?.should_pause()
        // En streaming el barge-in lo detecta el propio micrófono de la sesión (onBargeIn);
        // en los otros modos, la vigilancia interna de `hablar`
        const rep = speak(text, BARGE_IN && !v.finished && !streamingRef.current)
        cancelSpeakingRef.current = rep.cancel
        ttsStartAt = await rep.start
        const end = await rep.end
        interrupted = end.interrupted
        streamingRef.current?.resume()
        // Ledger de salida: el servidor sabe qué parte del mensaje oyó el vecino
        if (v.turn >= 0) {
          if (streamingRef.current) streamingRef.current.reportDelivery(v.turn, end.state, end.ratio, end.deliveredText)
          else api.delivery(v.call_id, v.turn, end.state, end.ratio, end.deliveredText).catch(() => undefined)
        }
      }
      const t: MeasuredTurn = {
        turn: v.turn,
        mode: voiceEndAt === null ? 'text' : 'voice',
        stt: voiceEndAt === null ? null : mode === 'streaming' || mode === 'livekit' || mode === 'twilio' ? 'server' : mode,
        server_ms: v.timings.server_ms,
        interpretation_ms: v.timings.interpretation_ms,
        llm_calls: v.timings.llm_calls,
        stt_ms: measurement.stt_ms ?? v.timings.stt_ms,
        vad_silence_ms: measurement.vad_silence_ms,
        network_ms: measurement.network_ms,
        tts_start_ms: ttsStartAt !== null ? Math.round(ttsStartAt - receivedAt) : undefined,
        response_ms: voiceEndAt !== null && ttsStartAt !== null ? Math.round(ttsStartAt - voiceEndAt) : undefined,
        interrupted: interrupted,
      }
      if (v.turn > 0) {
        recordTurn(t)
        api
          .metrics(v.call_id, {
            turn: v.turn,
            vad_silence_ms: t.vad_silence_ms,
            network_ms: t.network_ms,
            tts_start_ms: t.tts_start_ms,
            response_ms: t.response_ms,
            stt_client: mode,
            extra: { interrupted: interrupted, mode: t.mode },
          })
          .catch(() => undefined)
      }
      if (v.finished) {
        streamingRef.current?.close()
        streamingRef.current = null
      }
      setVoiceState(streamingRef.current && !v.finished ? 'awaiting_voice' : 'idle')
      if (!v.finished && optionsRef.current.handsFree && loopActiveRef.current && mode !== 'streaming') {
        void listen()
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [recordTurn],
  )

  const sendText = useCallback(
    async (text: string) => {
      const v = viewRef.current
      if (!v) return
      setBusy(true)
      mute()
      setView({ ...v, history: [...v.history, { role: 'caller', text }], bot_messages: [] })
      try {
        const t0 = performance.now()
        const updated = await api.message(v.call_id, text)
        const red = Math.round(performance.now() - t0 - (updated.timings.server_ms ?? 0))
        await respond(updated, null, { network_ms: Math.max(0, red) })
      } catch (e) {
        message.error(errorMessage(e))
        setVoiceState('idle')
      } finally {
        setBusy(false)
      }
    },
    [respond],
  )

  /** Modo streaming: una sesión por llamada; el proveedor detecta el fin de turno. */
  const openCallStreaming = useCallback(
    (callId: string): Promise<void> => {
      if (openingStreamingRef.current) return openingStreamingRef.current
      streamingRef.current?.close()
      streamingRef.current = null
      const generation = generationRef.current
      let turnVoiceEnd = 0
      const opening = openStreaming(callId, {
        onReady: (ms) => {
          setStreamingInfo(`Reconocimiento de voz conectado (${ms ?? '?'} ms)`)
          setVoiceState('awaiting_voice')
        },
        onPartial: (t) => setPartial(t),
        onFinal: (t, lastVoiceAt) => {
          turnVoiceEnd = lastVoiceAt
          setPartial(t)
          setVoiceState('processing')
        },
        onResponse: (v, lastVoiceAt) => {
          setPartial(null)
          void respond(v, turnVoiceEnd || lastVoiceAt || null, {})
        },
        onError: (m) => message.error(m),
        onVoice: (speaking) => setVoiceState(speaking ? 'speaking' : 'awaiting_voice'),
        onBargeIn: () => {
          if (BARGE_IN) cancelSpeakingRef.current()
        },
        onClosed: () => setStreamingInfo(null),
      })
        .then((session) => {
          // Si se colgó (o empezó otra llamada) mientras se abría el micrófono, no se engancha
          if (generation !== generationRef.current) session.close()
          else streamingRef.current = session
        })
        .finally(() => {
          openingStreamingRef.current = null
        })
      openingStreamingRef.current = opening
      return opening
    },
    [respond],
  )

  /** Un turno de escucha (modos navegador/servidor): captura -> STT -> grafo -> respuesta hablada. */
  const listen = useCallback(async () => {
    const v = viewRef.current
    if (!v || v.finished) return
    const { sttMode: mode, silenceMs: silence } = optionsRef.current
    if (mode === 'streaming') {
      if (!streamingRef.current) await openCallStreaming(v.call_id)
      return
    }
    setPartial(null)
    try {
      let text = ''
      let voiceEndAt = 0
      let sttMs: number | undefined
      let vadMs: number | undefined
      let reply: CallView
      let t0 = 0
      if (mode === 'server') {
        const cap = captureTurn({ ...DEFAULT_VAD, silenceMs: silence }, setVoiceState)
        cancelListeningRef.current = cap.cancel
        const c = await cap.result
        voiceEndAt = c.voiceEndAt
        vadMs = Math.round(c.decidedAt - c.voiceEndAt)
        setVoiceState('processing')
        setPartial('…')
        t0 = performance.now()
        reply = await api.audio(v.call_id, c.blob, c.mime)
        sttMs = reply.timings.stt_ms
        text = reply.transcript ?? ''
        if (reply.no_voice) {
          setPartial(null)
          setVoiceState('idle')
          if (optionsRef.current.handsFree && loopActiveRef.current) void listen()
          return
        }
      } else {
        const rec = recognizeInBrowser(setVoiceState)
        cancelListeningRef.current = rec.cancel
        const r = await rec.result
        voiceEndAt = r.voiceEndAt
        sttMs = Math.round(r.resultAt - r.voiceEndAt)
        text = r.text
        setVoiceState('processing')
        setPartial(text)
        t0 = performance.now()
        reply = await api.message(v.call_id, text)
      }
      const red = Math.round(performance.now() - t0 - (reply.timings.server_ms ?? 0))
      setPartial(null)
      await respond(reply, voiceEndAt, { stt_ms: sttMs, vad_silence_ms: vadMs, network_ms: Math.max(0, red) })
    } catch (e) {
      setPartial(null)
      setVoiceState('idle')
      if (e instanceof NoVoiceError) {
        if (optionsRef.current.handsFree && loopActiveRef.current && !viewRef.current?.finished) void listen()
        return
      }
      if (e instanceof Error && e.message === 'cancelled') return
      message.error(errorMessage(e))
    }
  }, [respond, openCallStreaming])

  /** Corta todo lo que tenga en marcha la llamada: escucha, voz del bot, micrófono y WebSocket. */
  const stopVoice = () => {
    generationRef.current += 1
    cancelListeningRef.current()
    cancelSpeakingRef.current()
    streamingRef.current?.close()
    streamingRef.current = null
    livekitRef.current?.close()
    livekitRef.current = null
    twilioRef.current?.close()
    twilioRef.current = null
    mute()
  }

  /** Por LiveKit el agente del servidor escucha y habla; la consola solo enseña la llamada. */
  const startLiveKit = async () => {
    const v = await api.begin()
    viewRef.current = v
    setView(v)
    const generation = generationRef.current
    // Carga bajo demanda: livekit-client solo hace falta en este modo
    const { openLiveKit } = await import('./livekit')
    const session = await openLiveKit(v.call_id, {
      onView: (updated) => {
        viewRef.current = updated
        setView(updated)
      },
      onTimings: (ts) =>
        setTurns(
          ts.map((t) => ({
            turn: t.turn,
            mode: 'voice',
            stt: 'server',
            stt_ms: t.stt_ms ?? undefined,
            server_ms: t.server_ms,
            interpretation_ms: t.interpretation_ms ?? undefined,
            llm_calls: t.llm_calls,
            response_ms: t.response_ms ?? undefined,
          })),
        ),
      onSpeaking: (who) =>
        setVoiceState(who === 'bot' ? 'respondiendo' : who === 'caller' ? 'speaking' : 'awaiting_voice'),
      onEnded: () => setVoiceState('idle'),
    })
    // Si se colgó mientras se conectaba, la sesión recién abierta no debe quedarse viva
    if (generation !== generationRef.current) session.close()
    else livekitRef.current = session
  }

  /** Por Twilio: el navegador llama a Twilio y la llamada llega al agente como una de teléfono. */
  const startTwilio = async () => {
    const v = await api.begin()
    viewRef.current = v
    setView(v)
    const generation = generationRef.current
    const { openTwilio } = await import('./twilioCall')
    const session = await openTwilio(v.call_id, {
      onView: (updated) => {
        viewRef.current = updated
        setView(updated)
      },
      onTimings: (ts) =>
        setTurns(
          ts.map((t) => ({
            turn: t.turn,
            mode: 'voice',
            stt: 'server',
            stt_ms: t.stt_ms ?? undefined,
            server_ms: t.server_ms,
            interpretation_ms: t.interpretation_ms ?? undefined,
            llm_calls: t.llm_calls,
            response_ms: t.response_ms ?? undefined,
          })),
        ),
      onSpeaking: (who) =>
        setVoiceState(who === 'bot' ? 'respondiendo' : who === 'caller' ? 'speaking' : 'awaiting_voice'),
      onEnded: () => setVoiceState('idle'),
    })
    if (generation !== generationRef.current) session.close()
    else twilioRef.current = session
  }

  const begin = async () => {
    setError(null)
    setBusy(true)
    stopVoice()
    setTurns([])
    loopActiveRef.current = true
    try {
      if (optionsRef.current.sttMode === 'livekit') {
        await startLiveKit()
        return
      }
      if (optionsRef.current.sttMode === 'twilio') {
        await startTwilio()
        return
      }
      const v = await api.begin()
      if (optionsRef.current.sttMode === 'streaming' && optionsRef.current.handsFree) {
        // Abrimos el STT mientras suena el saludo para que la conexión no cueste latencia
        void openCallStreaming(v.call_id).catch((e) => message.error(errorMessage(e)))
      }
      await respond(v, null, {})
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const hang_up = () => {
    loopActiveRef.current = false
    stopVoice()
    setVoiceState('idle')
  }

  const logout = async () => {
    hang_up() // salir con una llamada en curso no puede dejar el micrófono abierto
    setView(null)
    await auth.logout().catch(() => undefined)
    setAccess({ mode: 'oidc', authenticated: false, user: null })
    setOpsSummary(null)
    setCallRecords([])
    setSelected(null)
  }

  const sttServidorOk = !!health?.stt_available
  const sttStreamingOk = !!health?.stt_streaming
  const sttNavegadorOk = sttBrowserAvailable()
  const sessionReady = !!access?.authenticated && access.user?.access_state === 'granted'
  const catalog = useCatalog(sessionReady)

  if (access === null) {
    return (
      <main className="ops-login" aria-busy="true">
        <Spin size="large" tip="Conectando con la consola…"><div style={{ minHeight: 120 }} /></Spin>
      </main>
    )
  }
  if (!access.authenticated || !access.user) return <Login />
  if (access.user.access_state !== 'granted') {
    return <PendingAccess user={access.user} onUpdate={personUpdated} onExit={() => void logout()} />
  }

  const me = access.user
  const isOwner = me.role === 'owner'
  const managesIncidents = isOwner || me.role === 'administrator'
  const screenOptions: { label: React.ReactNode; value: Screen }[] = [
    { label: 'Simular llamada', value: 'simulate' },
    { label: 'Historial', value: 'history' },
    ...(managesIncidents
      ? [
          {
            label: (
              <Badge count={inboxPending} size="small" offset={[8, -2]}>
                Bandeja
              </Badge>
            ),
            value: 'inbox' as Screen,
          },
        ]
      : []),
    { label: 'Fincas y profesionales', value: 'data' },
    { label: 'Flujo', value: 'flow' },
    { label: 'Rendimiento', value: 'latency' },
    ...(isOwner
      ? [
          {
            label: (
              <Badge count={requests} size="small" offset={[8, -2]}>
                Solicitudes
              </Badge>
            ),
            value: 'requests' as Screen,
          },
          { label: 'Usuarios', value: 'users' as Screen },
        ]
      : []),
  ]

  const inProgress = !!view && !view.finished
  const voiceSettings = (
    <div className="sim-settings">
      <label>
        <span>
          <b>Voz del asistente</b>
          <small>Lee las respuestas en voz alta</small>
        </span>
        <Switch checked={voiceEnabled} onChange={setVoiceActive} disabled={!ttsAvailable()} size="small" />
      </label>
      <label>
        <span>
          <b>Manos libres</b>
          <small>Vuelve a escuchar solo después de cada respuesta</small>
        </span>
        <Switch checked={handsFree} onChange={setHandsFree} size="small" />
      </label>
      <div>
        <b>Reconocimiento de voz</b>
        <Segmented
          block
          size="small"
          value={sttMode}
          onChange={(value) => setSttMode(value as SttMode)}
          options={[
            { label: 'Twilio', value: 'twilio', disabled: !health?.twilio_browser },
            { label: 'LiveKit', value: 'livekit', disabled: !health?.livekit },
            { label: 'Servidor', value: 'streaming', disabled: !sttStreamingOk },
            { label: 'Navegador', value: 'browser', disabled: !sttNavegadorOk },
            { label: 'Grabación', value: 'server', disabled: !sttServidorOk || !captureAvailable() },
          ]}
        />
        <small>«LiveKit» es el camino de las llamadas reales: voz, interrupciones y ledger en el servidor.</small>
      </div>
      {sttMode === 'server' && (
        <div>
          <b>Silencio que cierra el turno: {silenceMs} ms</b>
          <Slider min={300} max={1200} step={50} value={silenceMs} onChange={setSilenceMs} />
        </div>
      )}
      {streamingInfo && <small className="ops-mono">{streamingInfo}</small>}
    </div>
  )

  return (
      <main className={`ops-shell ${screen === 'simulate' ? 'is-fixed' : ''}`}>
        <header className="ops-topbar">
          <div className="ops-brand">
            <Logo withName />
            <div className="ops-brand-product">
              <h1>Asistente de incidencias</h1>
              <div className="ops-system">
                <Tooltip title={health?.model_available ? 'El asistente puede atender llamadas' : 'El modelo de lenguaje no responde'}>
                  <span className="ops-state">
                    <span className={`ops-signal ${health?.model_available ? 'is-online' : ''}`} />
                    {health?.model_available ? 'Asistente disponible' : 'Asistente no disponible'}
                  </span>
                </Tooltip>
                {tel && (
                  <Tooltip title={tel.items ? `Teléfono ${tel.number ?? ''}` : 'Pendiente de contratar el número. Mientras tanto, las llamadas se simulan desde esta consola.'}>
                    <span className="ops-state">
                      <span className={`ops-signal ${tel.items ? 'is-online' : ''}`} />
                      {tel.items ? `Línea ${tel.number ?? 'active'}` : 'Línea telefónica pendiente'}
                    </span>
                  </Tooltip>
                )}
              </div>
            </div>
          </div>
          <Segmented<Screen> className="ops-nav" value={screen} onChange={setScreen} options={screenOptions} />
          <Dropdown
            menu={{
              items: [
                { key: 'role', label: `Rol: ${ROLES[me.role] ?? me.role}`, disabled: true },
                { type: 'divider' },
                { key: 'logout', icon: <LogoutOutlined />, label: 'Salir', onClick: () => void logout() },
              ],
            }}
          >
            <Tooltip title={me.name ?? me.username} placement="left">
              <Button className="ops-user" icon={<UserOutlined />}><span>{me.name ?? me.username}</span></Button>
            </Tooltip>
          </Dropdown>
        </header>

        {error && <Alert className="ops-alert" type="error" message={error} showIcon closable onClose={() => setError(null)} />}

        {screen === 'simulate' && (
          <section className="sim">
            <section className="sim-col sim-call" aria-label="Llamada">
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">Llamada simulada</p>
                  <h2>{inProgress ? 'En curso' : view?.finished ? 'Llamada terminada' : 'Línea libre'}</h2>
                </div>
                <div className="sim-actions">
                  <Popover content={voiceSettings} title="Ajustes de voz" trigger="click" placement="bottomRight">
                    <Button icon={<SettingOutlined />} title="Ajustes de voz" />
                  </Popover>
                  {inProgress ? (
                    <Button danger onClick={hang_up}>Colgar</Button>
                  ) : view && (
                    <Button type="primary" icon={<ReloadOutlined />} onClick={() => void begin()} loading={busy}>
                      Nueva llamada
                    </Button>
                  )}
                </div>
              </div>
              <div className="sim-conversation">
                {view ? (
                  <Conversation history={view.history} partial={partial} voiceState={voiceState} busy={busy} finished={view.finished} handsFree={handsFree} onSend={sendText} onListen={() => void listen()} />
                ) : (
                  <div className="sim-welcome">
                    <p className="sim-intro">
                      Haz de vecino: pulsa <b>Iniciar llamada</b> y habla con el micrófono, o escribe lo que diría.
                      Las direcciones y viviendas que existen están en <b>Fincas y profesionales</b>.
                    </p>
                    <Button type="primary" size="large" icon={<PhoneOutlined />} onClick={() => void begin()} loading={busy}>
                      Iniciar llamada
                    </Button>
                    <div className="sim-capabilities">
                      <div>
                        <h3>Qué hace el asistente</h3>
                        <ul>{CAN.map((t) => <li key={t}><CheckOutlined className="is-good" />{t}</li>)}</ul>
                      </div>
                      <div>
                        <h3>Qué no hace</h3>
                        <ul>{CANNOT.map((t) => <li key={t}><CloseOutlined className="is-review" />{t}</li>)}</ul>
                      </div>
                    </div>
                    <small className="sim-footer">En esta demostración los avisos a profesionales y administradores se simulan: no se envía ningún correo ni llamada.</small>
                  </div>
                )}
              </div>
            </section>

            <section className="sim-col sim-card" aria-label="Lo que ha entendido el asistente">
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">En tiempo real</p>
                  <h2>Lo que ha entendido el asistente</h2>
                </div>
              </div>
              <CallCard view={view} />
            </section>

            <section className="sim-col sim-data" aria-label="Fincas y profesionales">
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">Para comprobar</p>
                  <h2>Fincas y profesionales</h2>
                </div>
              </div>
              <Database
                mode="panel"
                catalog={catalog}
                highlight={{
                  estateId: view?.data.estate_id,
                  homeId: view?.data.home_id,
                  professionalId: view?.data.specialist_id,
                  category: view?.data.category,
                }}
              />
            </section>
          </section>
        )}

        {screen === 'history' && (
          <History
            summary={opsSummary}
            callRecords={callRecords}
            selected={selected}
            onOpen={(id) => void openCallRecord(id)}
            catalog={catalog}
            canErase={isOwner}
            onErased={() => {
              setSelected(null)
              void refreshOperations()
            }}
          />
        )}

        {screen === 'data' && (
          <section className="ops-single">
            <div className="ops-panel-heading">
              <div>
                <p className="ops-eyebrow">Lo que el asistente consulta en cada llamada</p>
                <h2>Fincas y profesionales</h2>
              </div>
            </div>
            <Database mode="page" catalog={catalog} editable={me.role !== 'quality'} />
          </section>
        )}

        <Suspense fallback={<Spin className="ops-loading" />}>
        {screen === 'inbox' && managesIncidents && (
          <Inbox catalog={catalog} canAct={managesIncidents} onPending={setInboxPending} />
        )}

        {screen === 'users' && isOwner && (
          <section className="ops-single"><UserManagement me={me} /></section>
        )}

        {screen === 'requests' && isOwner && (
          <section className="ops-single"><AccessRequests onUpdate={() => void refreshOperations()} /></section>
        )}

        {screen === 'flow' && (
          <section className="ops-single"><FlowPanel isOwner={isOwner} /></section>
        )}

        {screen === 'latency' && (
          <section className="ops-single ops-latency">
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">Voz · esta sesión</p><h2>Tiempo de respuesta por turno</h2></div><span className="ops-mono">{turns.length} turnos</span></div>
              <LatencyPanel turns={turns} />
            </div>
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">Llamada en directo</p><h2>Estado interno del asistente</h2></div></div>
              <StatePanel view={view} />
            </div>
          </section>
        )}
        </Suspense>
      </main>
    )
}
