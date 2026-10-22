/**
 * Voz por LiveKit: la consola entra en una sala con el micrófono y el agente de voz del servidor
 * (`building_call.voz.agente`) escucha, habla e informa del ledger de entrega. La consola solo
 * muestra el estado de la llamada, que sigue en la API.
 */
import { Room, RoomEvent, Track } from 'livekit-client'
import { api, type ServerTurnTimings, type CallView } from './api'

export interface LiveKitSession {
  close: () => void
}

interface Events {
  onView: (v: CallView) => void
  /** Tiempos que mide el agente (el navegador aquí no mide: ni escucha ni habla) */
  onTimings: (turns: ServerTurnTimings[]) => void
  onSpeaking: (who: 'bot' | 'caller' | null) => void
  onEnded: () => void
}

/** Cada cuánto se refresca la vista de la llamada mientras habla el agente. */
const REFRESH_MS = 700

export async function openLiveKit(callId: string, events: Events): Promise<LiveKitSession> {
  const reply = await api.liveKitToken(callId)
  const token = reply.token
  // "/" = LiveKit por el mismo origen que la consola (túnel): Vite reenvía /rtc
  const url = reply.url.startsWith('/')
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
    : reply.url
  const room = new Room({ adaptiveStream: true, dynacast: true })
  const audios: HTMLMediaElement[] = []
  let closed = false
  let lastVersion = ''

  const close = () => {
    if (closed) return
    closed = true
    window.clearInterval(refresco)
    audios.forEach((a) => a.remove())
    void room.disconnect()
    // El agente envía los tiempos del último turno justo antes de colgar: una última consulta
    void Promise.all([api.get(callId), api.callMetrics(callId)])
      .then(([v, m]) => {
        events.onView(v)
        events.onTimings(m.turns)
      })
      .catch(() => undefined)
    events.onEnded()
  }

  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return
    const el = track.attach()
    el.style.display = 'none'
    document.body.appendChild(el)
    audios.push(el)
  })
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
    const local = room.localParticipant.identity
    if (!speakers.length) events.onSpeaking(null)
    else events.onSpeaking(speakers.some((p) => p.identity === local) ? 'caller' : 'bot')
  })
  // El agente cuelga borrando la sala al terminar la despedida
  room.on(RoomEvent.Disconnected, close)

  const refresco = window.setInterval(() => {
    api
      .get(callId)
      .then((v) => {
        const version = `${v.turn}:${v.history.length}:${v.finished}:${JSON.stringify(v.last_delivery)}`
        if (version !== lastVersion) {
          lastVersion = version
          events.onView(v)
        }
      })
      .catch(() => undefined)
    // El agente informa de la latencia cuando la respuesta ya ha sonado: se consulta siempre
    api
      .callMetrics(callId)
      .then((m) => events.onTimings(m.turns))
      .catch(() => undefined)
  }, REFRESH_MS)

  try {
    await room.connect(url, token)
    await room.localParticipant.setMicrophoneEnabled(true, {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    })
  } catch (e) {
    close()
    throw e
  }
  return { close }
}
