import { Suspense, lazy, useCallback, useEffect, useRef, useState, type ComponentType } from 'react'
import { Alert, Badge, Button, Dropdown, Popover, Segmented, Select, Slider, Spin, Switch, Tooltip, message } from 'antd'
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
  type LanguagesInfo,
  type PersonaInfo,
} from './api'
import { UI_LANGUAGES, useI18n, type UiLanguage } from './i18n'
import type { MessageKey } from './i18n/messages/es'
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
  setSpeechLanguage,
} from './voice'

// Pantallas que no se usan al simular: se descargan al abrirlas (el arranque pesa menos)
const UserManagement = lazy(() => import('./components/UserManagement').then((m) => ({ default: m.UserManagement })))
const AccessRequests = lazy(() => import('./components/AccessRequests').then((m) => ({ default: m.AccessRequests })))
// «Flujo» se precarga en reposo: un `lazy` que suspende hace esperar a React 19 ~300 ms antes de
// enseñar la pantalla (aunque el código ya esté en caché). Ya cargado se pinta directamente.
const FlowPanel = preloadable(() => import('./components/FlowPanel').then((m) => m.FlowPanel))
const LiveRouting = lazy(() => import('./components/routing/LiveRouting').then((m) => ({ default: m.LiveRouting })))
const Inbox = lazy(() => import('./components/Inbox').then((m) => ({ default: m.Inbox })))
const LatencyPanel = lazy(() => import('./components/LatencyPanel').then((m) => ({ default: m.LatencyPanel })))
const KpiPanel = lazy(() => import('./components/KpiPanel').then((m) => ({ default: m.KpiPanel })))

/** Un `lazy` que además se puede precargar; una vez cargado se pinta sin pasar por Suspense. */
function preloadable<P extends object>(load: () => Promise<ComponentType<P>>) {
  let loaded: ComponentType<P> | null = null
  let pending: Promise<ComponentType<P>> | null = null
  const get = () => (pending ??= load().then((c) => (loaded = c)).catch((e: unknown) => { pending = null; throw e }))
  const Lazy = lazy(() => get().then((c) => ({ default: c }))) as unknown as ComponentType<P>
  function Preloaded(props: P) {
    const Loaded = loaded
    return Loaded ? <Loaded {...props} /> : <Lazy {...props} />
  }
  return Object.assign(Preloaded, { preload: () => { void get().catch(() => undefined) } })
}

type SttMode = 'browser' | 'server' | 'streaming' | 'livekit' | 'twilio' | 'line'
type Screen = 'simulate' | 'history' | 'inbox' | 'data' | 'flow' | 'latency' | 'users' | 'requests'

/** Lo que se enseña al cliente antes de la primera llamada. Hechos del grafo, no promesas. */
/** El barge-in (cortar al bot cuando el vecino habla) está siempre activo en la consola. */
const BARGE_IN = true

const CAN: MessageKey[] = ['sim.can.1', 'sim.can.2', 'sim.can.3', 'sim.can.4', 'sim.can.5', 'sim.can.6']
const CANNOT: MessageKey[] = ['sim.cannot.1', 'sim.cannot.2', 'sim.cannot.3', 'sim.cannot.4', 'sim.cannot.5']

export default function App() {
  // Consuming the context re-renders the whole console (and the `t()` helpers) on a change
  const { t, lang, setLang } = useI18n()
  const [health, setHealth] = useState<Health | null>(null)
  // The call's language ('' = BC_LANGUAGE + detection) and the client's persona ('' = default)
  const [callLanguage, setCallLanguage] = useState('')
  const [persona, setPersona] = useState('')
  const [languages, setLanguages] = useState<LanguagesInfo | null>(null)
  const [personaList, setPersonaList] = useState<PersonaInfo[]>([])
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
  /** Tercera columna de «Simular llamada»: el grafo en vivo o las fincas para comprobar. */
  const [simSide, setSimSide] = useState<'graph' | 'data'>('graph')
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
        // En streaming el barge-in lo decide el servidor por palabras, sin el eco (onBargeIn);
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
        stt: voiceEndAt === null ? null : mode === 'streaming' || mode === 'livekit' || mode === 'twilio' || mode === 'line' ? 'server' : mode,
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
      if (!v.finished && optionsRef.current.handsFree && loopActiveRef.current) {
        // En streaming también: si la sesión se cayó, se reabre para seguir escuchando
        if (mode !== 'streaming' || !streamingRef.current) void listen()
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
    const v = await api.begin({ language: callLanguage || null, persona: persona || null })
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

  /** Por Twilio (la red telefónica real) o por la línea del navegador (el mismo puente, sin
   * Twilio): la llamada llega al agente como una de teléfono y la consola la sigue en directo. */
  const startTwilio = async (carrier: 'twilio' | 'line' = 'twilio') => {
    const v = await api.begin({ language: callLanguage || null, persona: persona || null })
    viewRef.current = v
    setView(v)
    const generation = generationRef.current
    const open =
      carrier === 'line' ? (await import('./phoneLine')).openPhoneLine : (await import('./twilioCall')).openTwilio
    const session = await open(v.call_id, {
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
      if (optionsRef.current.sttMode === 'twilio' || optionsRef.current.sttMode === 'line') {
        await startTwilio(optionsRef.current.sttMode)
        return
      }
      const v = await api.begin({ language: callLanguage || null, persona: persona || null })
      if (optionsRef.current.sttMode === 'streaming' && optionsRef.current.handsFree) {
        // El micrófono se abre ANTES del saludo: si se abría mientras sonaba, cortar el saludo
        // caía en un hueco sin sesión y la llamada se quedaba sin escuchar
        await openCallStreaming(v.call_id).catch((e) => message.error(errorMessage(e)))
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
    setPartial(null)
    const v = viewRef.current
    const mode = optionsRef.current.sttMode
    // LiveKit and the phone line close the call on the server side themselves
    if (v && !v.finished && mode !== 'livekit' && mode !== 'twilio' && mode !== 'line') {
      // Se da por terminada al momento (el botón pasa a «Nueva llamada») y el servidor la cierra
      // como abandonada; antes solo se apagaba el micrófono y la llamada seguía «en curso»
      const ended = { ...v, finished: true }
      viewRef.current = ended
      setView(ended)
      api.hangUp(v.call_id).catch(() => undefined)
    }
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
  useEffect(() => {
    if (!sessionReady) return
    api.languages().then(setLanguages).catch(() => undefined)
    api.personas().then(setPersonaList).catch(() => undefined)
    // Con la consola ya pintada y el navegador libre: la pantalla «Flujo» abre sin espera
    const idle = window.requestIdleCallback ?? ((fn: () => void) => window.setTimeout(fn, 1500))
    idle(() => FlowPanel.preload())
  }, [sessionReady])
  // The browser's recognition and voice follow the call, which may switch language mid-call
  const speechTag = languages?.languages.find((l) => l.code === (view?.language ?? callLanguage))?.browser_lang
  useEffect(() => {
    if (speechTag) setSpeechLanguage(speechTag)
  }, [speechTag])
  const catalog = useCatalog(sessionReady)

  if (access === null) {
    return (
      <main className="ops-login" aria-busy="true">
        <Spin size="large" tip={t('app.connecting')}><div style={{ minHeight: 120 }} /></Spin>
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
    { label: t('nav.simulate'), value: 'simulate' },
    { label: t('nav.history'), value: 'history' },
    ...(managesIncidents
      ? [
          {
            label: (
              <Badge count={inboxPending} size="small" offset={[8, -2]}>
                {t('nav.inbox')}
              </Badge>
            ),
            value: 'inbox' as Screen,
          },
        ]
      : []),
    { label: t('nav.data'), value: 'data' },
    { label: t('nav.flow'), value: 'flow' },
    { label: t('nav.latency'), value: 'latency' },
    ...(isOwner
      ? [
          {
            label: (
              <Badge count={requests} size="small" offset={[8, -2]}>
                {t('nav.requests')}
              </Badge>
            ),
            value: 'requests' as Screen,
          },
          { label: t('nav.users'), value: 'users' as Screen },
        ]
      : []),
  ]

  const inProgress = !!view && !view.finished
  const callOptions = (
    <div className="sim-call-options">
      <label>
        <span>{t('call.language')}</span>
        <Select
          size="small"
          value={callLanguage}
          onChange={setCallLanguage}
          popupMatchSelectWidth={false}
          options={[
            { value: '', label: t('call.language.auto') },
            ...(languages?.languages ?? [])
              .filter((l) => l.enabled)
              .map((l) => ({ value: l.code, label: l.native_name })),
          ]}
        />
      </label>
      {personaList.length > 0 && (
        <label>
          <span>{t('call.persona')}</span>
          <Select
            size="small"
            value={persona}
            onChange={setPersona}
            popupMatchSelectWidth={false}
            options={[
              { value: '', label: t('call.persona.none') },
              ...personaList.map((p) => ({ value: p.id, label: p.client })),
            ]}
          />
        </label>
      )}
    </div>
  )
  const voiceSettings = (
    <div className="sim-settings">
      <label>
        <span>
          <b>{t('voice.assistant')}</b>
          <small>{t('voice.assistant.hint')}</small>
        </span>
        <Switch checked={voiceEnabled} onChange={setVoiceActive} disabled={!ttsAvailable()} size="small" />
      </label>
      <label>
        <span>
          <b>{t('voice.handsFree')}</b>
          <small>{t('voice.handsFree.hint')}</small>
        </span>
        <Switch checked={handsFree} onChange={setHandsFree} size="small" />
      </label>
      <div>
        <b>{t('voice.recognition')}</b>
        <Segmented
          block
          size="small"
          value={sttMode}
          onChange={(value) => setSttMode(value as SttMode)}
          options={[
            { label: 'Twilio', value: 'twilio', disabled: !health?.twilio_browser },
            { label: t('voice.mode.line'), value: 'line', disabled: !health?.browser_line },
            { label: 'LiveKit', value: 'livekit', disabled: !health?.livekit },
            { label: t('voice.mode.server'), value: 'streaming', disabled: !sttStreamingOk },
            { label: t('voice.mode.browser'), value: 'browser', disabled: !sttNavegadorOk },
            { label: t('voice.mode.recording'), value: 'server', disabled: !sttServidorOk || !captureAvailable() },
          ]}
        />
        <small>{t('voice.modes.hint')}</small>
      </div>
      {sttMode === 'server' && (
        <div>
          <b>{t('voice.silence', { ms: silenceMs })}</b>
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
              <h1>{t('app.title')}</h1>
              <div className="ops-system">
                <Tooltip title={health?.model_available ? t('app.available.tip') : t('app.unavailable.tip')}>
                  <span className="ops-state">
                    <span className={`ops-signal ${health?.model_available ? 'is-online' : ''}`} />
                    {health?.model_available ? t('app.available') : t('app.unavailable')}
                  </span>
                </Tooltip>
                {tel && (
                  <Tooltip title={tel.items ? t('app.line.tip.on', { number: tel.number ?? '' }) : t('app.line.tip.off')}>
                    <span className="ops-state">
                      <span className={`ops-signal ${tel.items ? 'is-online' : ''}`} />
                      {tel.items ? t('app.line.on', { number: tel.number ?? '' }) : t('app.line.off')}
                    </span>
                  </Tooltip>
                )}
              </div>
            </div>
          </div>
          <Segmented<Screen> className="ops-nav" value={screen} onChange={setScreen} options={screenOptions} />
          <Select<UiLanguage>
            className="ops-language"
            size="small"
            value={lang}
            onChange={setLang}
            aria-label={t('app.uiLanguage')}
            popupMatchSelectWidth={false}
            options={UI_LANGUAGES.map((l) => ({ value: l.code, label: l.native }))}
          />
          <Dropdown
            menu={{
              items: [
                { key: 'role', label: t('app.role', { role: ROLES[me.role] ?? me.role }), disabled: true },
                { type: 'divider' },
                { key: 'logout', icon: <LogoutOutlined />, label: t('app.logout'), onClick: () => void logout() },
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
            <section className="sim-col sim-call" aria-label={t('sim.eyebrow')}>
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">{t('sim.eyebrow')}</p>
                  <h2>{inProgress ? t('sim.inProgress') : view?.finished ? t('sim.finished') : t('sim.idle')}</h2>
                </div>
                <div className="sim-actions">
                  <Popover content={voiceSettings} title={t('voice.settings')} trigger="click" placement="bottomRight">
                    <Button icon={<SettingOutlined />} title={t('voice.settings')} />
                  </Popover>
                  {inProgress ? (
                    <Button danger onClick={hang_up}>{t('sim.hangUp')}</Button>
                  ) : view && (
                    <Button type="primary" icon={<ReloadOutlined />} onClick={() => void begin()} loading={busy}>
                      {t('sim.newCall')}
                    </Button>
                  )}
                </div>
              </div>
              <div className="sim-conversation">
                {view ? (
                  <Conversation history={view.history} partial={partial} voiceState={voiceState} busy={busy} finished={view.finished} handsFree={handsFree} onSend={sendText} onListen={() => void listen()} />
                ) : (
                  <div className="sim-welcome">
                    <p className="sim-intro">{t('sim.intro')}</p>
                    {callOptions}
                    <Button type="primary" size="large" icon={<PhoneOutlined />} onClick={() => void begin()} loading={busy}>
                      {t('sim.start')}
                    </Button>
                    <div className="sim-capabilities">
                      <div>
                        <h3>{t('sim.can')}</h3>
                        <ul>{CAN.map((k) => <li key={k}><CheckOutlined className="is-good" />{t(k)}</li>)}</ul>
                      </div>
                      <div>
                        <h3>{t('sim.cannot')}</h3>
                        <ul>{CANNOT.map((k) => <li key={k}><CloseOutlined className="is-review" />{t(k)}</li>)}</ul>
                      </div>
                    </div>
                    <small className="sim-footer">{t('sim.footer')}</small>
                  </div>
                )}
              </div>
            </section>

            <section className="sim-col sim-card" aria-label={t('sim.understood')}>
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">{t('sim.live')}</p>
                  <h2>{t('sim.understood')}</h2>
                </div>
              </div>
              <CallCard view={view} />
            </section>

            <section className="sim-col sim-data" aria-label={simSide === 'graph' ? t('routing.sim.title') : t('nav.data')}>
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">{simSide === 'graph' ? t('routing.sim.eyebrow') : t('sim.check')}</p>
                  <h2>{simSide === 'graph' ? t('routing.sim.title') : t('nav.data')}</h2>
                </div>
                <Segmented
                  size="small"
                  value={simSide}
                  onChange={(v) => setSimSide(v as 'graph' | 'data')}
                  options={[{ label: t('routing.sim.graph'), value: 'graph' }, { label: t('routing.sim.data'), value: 'data' }]}
                  aria-label={t('routing.sim.column')}
                />
              </div>
              {simSide === 'graph' ? (
                <Suspense fallback={<Spin className="ops-loading" />}>
                  <LiveRouting view={view} />
                </Suspense>
              ) : (
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
              )}
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
                <p className="ops-eyebrow">{t('data.eyebrow')}</p>
                <h2>{t('nav.data')}</h2>
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
          <section className="ops-single"><FlowPanel isOwner={isOwner} liveView={view} /></section>
        )}

        {screen === 'latency' && (
          <section className="ops-single ops-latency">
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">{t('latency.eyebrow')}</p><h2>{t('latency.title')}</h2></div><span className="ops-mono">{t('latency.turns', { n: turns.length })}</span></div>
              <LatencyPanel turns={turns} />
            </div>
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">{t('live.eyebrow')}</p><h2>{t('live.state')}</h2></div></div>
              <StatePanel view={view} />
            </div>
          </section>
        )}
        {screen === 'latency' && (
          <section className="ops-single"><KpiPanel /></section>
        )}
        </Suspense>
      </main>
    )
}
