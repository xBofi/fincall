/**
 * STT en streaming: micrófono -> PCM16 16 kHz -> WebSocket del backend -> AssemblyAI (EU).
 * El backend detecta el fin de turno (lo hace el proveedor) y devuelve la respuesta del grafo.
 *
 * `ultimaVozAt` se mide localmente por energía para calcular `respuesta_ms` (fin de voz -> TTS).
 */

import type { CallView } from './api'

const WORKLET_CODE = `
class CapturaPcm extends AudioWorkletProcessor {
  constructor() { super(); this.acum = []; this.n = 0; }
  process(inputs) {
    const canal = inputs[0] && inputs[0][0];
    if (!canal) return true;
    // Energía para el VAD local (métrica), y PCM16 para el servidor
    let energia = 0;
    const pcm = new Int16Array(canal.length);
    for (let i = 0; i < canal.length; i++) {
      const s = Math.max(-1, Math.min(1, canal[i]));
      energia += s * s;
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.acum.push(pcm); this.n += canal.length;
    // ~100 ms a 16 kHz
    if (this.n >= 1600) {
      const todo = new Int16Array(this.n); let o = 0;
      for (const p of this.acum) { todo.set(p, o); o += p.length; }
      this.port.postMessage({ pcm: todo.buffer, rms: Math.sqrt(energia / canal.length) }, [todo.buffer]);
      this.acum = []; this.n = 0;
    } else {
      this.port.postMessage({ rms: Math.sqrt(energia / canal.length) });
    }
    return true;
  }
}
registerProcessor('captura-pcm', CapturaPcm);
`

export type ServerMessage =
  | { type: 'ready'; connection_ms?: number }
  | { type: 'partial'; text: string }
  | { type: 'final'; text: string }
  | ({ type: 'reply' } & CallView)
  | { type: 'error'; message: string }

export interface CallbacksStreaming {
  onReady: (connectionMs?: number) => void
  onPartial: (text: string) => void
  onFinal: (text: string, lastVoiceAt: number) => void
  onResponse: (view: CallView, lastVoiceAt: number) => void
  onError: (message: string) => void
  onVoice: (speaking: boolean) => void
  /** El vecino habla mientras el asistente está en pausa (reproduciendo): barge-in. */
  onBargeIn?: () => void
  onClosed: () => void
}

export interface StreamingSession {
  should_pause: () => void
  resume: () => void
  /** Ledger de salida: informa al servidor de qué parte del mensaje del turno llegó a sonar. */
  reportDelivery: (turn: number, state: string, ratio: number, deliveredText: string) => void
  close: () => void
}

export async function openStreaming(
  callId: string,
  cb: CallbacksStreaming,
  voiceThreshold = 0.02,
): Promise<StreamingSession> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  })
  const ctx = new AudioContext({ sampleRate: 16000 })
  let source: MediaStreamAudioSourceNode
  let node: AudioWorkletNode
  try {
    const url = URL.createObjectURL(new Blob([WORKLET_CODE], { type: 'application/javascript' }))
    await ctx.audioWorklet.addModule(url)
    URL.revokeObjectURL(url)
    source = ctx.createMediaStreamSource(stream)
    node = new AudioWorkletNode(ctx, 'captura-pcm')
    source.connect(node)
  } catch (e) {
    // Sin esto el micrófono quedaría encendido aunque la llamada no llegue a empezar
    stream.getTracks().forEach((t) => t.stop())
    void ctx.close().catch(() => undefined)
    throw e
  }
  // No conectamos a destino: no queremos oír el micrófono

  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/calls/${callId}/voice?sample_rate=${ctx.sampleRate}`)
  ws.binaryType = 'arraybuffer'

  let paused = false
  let pausedSince = 0
  let lastVoiceAt = 0
  let speaking = false
  let voiceSince = 0
  let closed = false

  const markVoice = (rms: number) => {
    const now = performance.now()
    if (paused) {
      // Barge-in: el asistente está hablando; el altavoz puede colarse por el micrófono, así que
      // el umbral es más alto, se exige voz sostenida y se ignoran los primeros 400 ms
      if (rms >= voiceThreshold * 2.5 && now - pausedSince > 400) {
        voiceSince ||= now
        if (now - voiceSince >= 250) {
          voiceSince = 0
          cb.onBargeIn?.()
        }
      } else {
        voiceSince = 0
      }
      return
    }
    if (rms >= voiceThreshold) {
      voiceSince ||= now
      if (!speaking && now - voiceSince >= 150) {
        speaking = true
        cb.onVoice(true)
      }
      lastVoiceAt = now
    } else {
      voiceSince = 0
      if (speaking && now - lastVoiceAt > 300) {
        speaking = false
        cb.onVoice(false)
      }
    }
  }

  node.port.onmessage = (e: MessageEvent<{ pcm?: ArrayBuffer; rms: number }>) => {
    markVoice(e.data.rms)
    if (e.data.pcm && ws.readyState === WebSocket.OPEN) ws.send(e.data.pcm)
  }

  ws.onmessage = (e) => {
    let m: ServerMessage
    try {
      m = JSON.parse(e.data as string) as ServerMessage
    } catch {
      return // mensaje que no es del protocolo: se ignora
    }
    switch (m.type) {
      case 'ready':
        cb.onReady(m.connection_ms)
        break
      case 'partial':
        cb.onPartial(m.text)
        break
      case 'final':
        speaking = false
        cb.onFinal(m.text, lastVoiceAt)
        break
      case 'reply': {
        const { type: _t, ...view } = m
        cb.onResponse(view as CallView, lastVoiceAt)
        break
      }
      case 'error':
        cb.onError(m.message)
        break
    }
  }
  const clean_up = () => {
    if (closed) return
    closed = true
    node.disconnect()
    source.disconnect()
    stream.getTracks().forEach((t) => t.stop())
    void ctx.close().catch(() => undefined)
    cb.onClosed()
  }
  let ready = false
  ws.onclose = (e) => {
    if (!ready) {
      console.error(`WebSocket de voz cerrado antes de estar listo (código ${e.code})`)
      cb.onError('No se pudo conectar el reconocimiento de voz. Comprueba que el servidor está en marcha.')
    }
    clean_up()
  }
  ws.onerror = () => cb.onError('Error de conexión con el servidor de voz')
  const onReadyOriginal = cb.onReady
  cb.onReady = (ms) => {
    ready = true
    onReadyOriginal(ms)
  }

  const send = (o: unknown) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(o))
  return {
    should_pause: () => {
      paused = true
      pausedSince = performance.now()
      voiceSince = 0
      send({ type: 'pause' })
    },
    resume: () => {
      paused = false
      voiceSince = 0
      send({ type: 'resume' })
    },
    reportDelivery: (turn, state, ratio, deliveredText) =>
      send({ type: 'delivery', turn, state, ratio, delivered_text: deliveredText }),
    close: () => {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close()
      clean_up()
    },
  }
}
