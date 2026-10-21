/**
 * Voz por LiveKit: la consola entra en una sala con el micrófono y el agente de voz del servidor
 * (`building_call.voz.agente`) escucha, habla e informa del ledger de entrega. La consola solo
 * muestra el estado de la llamada, que sigue en la API.
 */
import { Room, RoomEvent, Track } from 'livekit-client'
import { api, type TiemposTurnoServidor, type VistaLlamada } from './api'

export interface SesionLiveKit {
  cerrar: () => void
}

interface Eventos {
  onVista: (v: VistaLlamada) => void
  /** Tiempos que mide el agente (el navegador aquí no mide: ni escucha ni habla) */
  onTiempos: (turnos: TiemposTurnoServidor[]) => void
  onHablando: (quien: 'bot' | 'vecino' | null) => void
  onCerrada: () => void
}

/** Cada cuánto se refresca la vista de la llamada mientras habla el agente. */
const REFRESCO_MS = 700

export async function abrirLiveKit(llamadaId: string, eventos: Eventos): Promise<SesionLiveKit> {
  const respuesta = await api.tokenLiveKit(llamadaId)
  const token = respuesta.token
  // "/" = LiveKit por el mismo origen que la consola (túnel): Vite reenvía /rtc
  const url = respuesta.url.startsWith('/')
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
    : respuesta.url
  const sala = new Room({ adaptiveStream: true, dynacast: true })
  const audios: HTMLMediaElement[] = []
  let cerrada = false
  let ultimaVersion = ''

  const cerrar = () => {
    if (cerrada) return
    cerrada = true
    window.clearInterval(refresco)
    audios.forEach((a) => a.remove())
    void sala.disconnect()
    // El agente envía los tiempos del último turno justo antes de colgar: una última consulta
    void Promise.all([api.obtener(llamadaId), api.metricasLlamada(llamadaId)])
      .then(([v, m]) => {
        eventos.onVista(v)
        eventos.onTiempos(m.turnos)
      })
      .catch(() => undefined)
    eventos.onCerrada()
  }

  sala.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return
    const el = track.attach()
    el.style.display = 'none'
    document.body.appendChild(el)
    audios.push(el)
  })
  sala.on(RoomEvent.ActiveSpeakersChanged, (activos) => {
    const local = sala.localParticipant.identity
    if (!activos.length) eventos.onHablando(null)
    else eventos.onHablando(activos.some((p) => p.identity === local) ? 'vecino' : 'bot')
  })
  // El agente cuelga borrando la sala al terminar la despedida
  sala.on(RoomEvent.Disconnected, cerrar)

  const refresco = window.setInterval(() => {
    api
      .obtener(llamadaId)
      .then((v) => {
        const version = `${v.turno}:${v.historial.length}:${v.finalizada}:${JSON.stringify(v.entrega_ultima)}`
        if (version !== ultimaVersion) {
          ultimaVersion = version
          eventos.onVista(v)
        }
      })
      .catch(() => undefined)
    // El agente informa de la latencia cuando la respuesta ya ha sonado: se consulta siempre
    api
      .metricasLlamada(llamadaId)
      .then((m) => eventos.onTiempos(m.turnos))
      .catch(() => undefined)
  }, REFRESCO_MS)

  try {
    await sala.connect(url, token)
    await sala.localParticipant.setMicrophoneEnabled(true, {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    })
  } catch (e) {
    cerrar()
    throw e
  }
  return { cerrar }
}
