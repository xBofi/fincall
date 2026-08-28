/**
 * STT en streaming: micrófono -> PCM16 16 kHz -> WebSocket del backend -> AssemblyAI (EU).
 * El backend detecta el fin de turno (lo hace el proveedor) y devuelve la respuesta del grafo.
 *
 * `ultimaVozAt` se mide localmente por energía para calcular `respuesta_ms` (fin de voz -> TTS).
 */

import type { VistaLlamada } from './api'

const CODIGO_WORKLET = `
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

export type MensajeServidor =
  | { type: 'listo'; conexion_ms?: number }
  | { type: 'parcial'; texto: string }
  | { type: 'final'; texto: string }
  | ({ type: 'respuesta' } & VistaLlamada)
  | { type: 'error'; mensaje: string }

export interface CallbacksStreaming {
  onListo: (conexionMs?: number) => void
  onParcial: (texto: string) => void
  onFinal: (texto: string, ultimaVozAt: number) => void
  onRespuesta: (vista: VistaLlamada, ultimaVozAt: number) => void
  onError: (mensaje: string) => void
  onVoz: (hablando: boolean) => void
  onCerrado: () => void
}

export interface SesionStreaming {
  pausar: () => void
  reanudar: () => void
  forzarFinTurno: () => void
  cerrar: () => void
}

export async function abrirStreaming(
  llamadaId: string,
  cb: CallbacksStreaming,
  umbralVoz = 0.02,
): Promise<SesionStreaming> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  })
  const ctx = new AudioContext({ sampleRate: 16000 })
  const url = URL.createObjectURL(new Blob([CODIGO_WORKLET], { type: 'application/javascript' }))
  await ctx.audioWorklet.addModule(url)
  URL.revokeObjectURL(url)
  const fuente = ctx.createMediaStreamSource(stream)
  const nodo = new AudioWorkletNode(ctx, 'captura-pcm')
  fuente.connect(nodo)
  // No conectamos a destino: no queremos oír el micrófono

  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/llamadas/${llamadaId}/voz?sample_rate=${ctx.sampleRate}`)
  ws.binaryType = 'arraybuffer'

  let pausado = false
  let ultimaVozAt = 0
  let hablando = false
  let vozDesde = 0
  let cerrado = false

  const marcarVoz = (rms: number) => {
    const ahora = performance.now()
    if (rms >= umbralVoz) {
      vozDesde ||= ahora
      if (!hablando && ahora - vozDesde >= 150 && !pausado) {
        hablando = true
        cb.onVoz(true)
      }
      if (!pausado) ultimaVozAt = ahora
    } else {
      vozDesde = 0
      if (hablando && ahora - ultimaVozAt > 300) {
        hablando = false
        cb.onVoz(false)
      }
    }
  }

  nodo.port.onmessage = (e: MessageEvent<{ pcm?: ArrayBuffer; rms: number }>) => {
    marcarVoz(e.data.rms)
    if (e.data.pcm && ws.readyState === WebSocket.OPEN) ws.send(e.data.pcm)
  }

  ws.onmessage = (e) => {
    const m = JSON.parse(e.data as string) as MensajeServidor
    switch (m.type) {
      case 'listo':
        cb.onListo(m.conexion_ms)
        break
      case 'parcial':
        cb.onParcial(m.texto)
        break
      case 'final':
        hablando = false
        cb.onFinal(m.texto, ultimaVozAt)
        break
      case 'respuesta': {
        const { type: _t, ...vista } = m
        cb.onRespuesta(vista as VistaLlamada, ultimaVozAt)
        break
      }
      case 'error':
        cb.onError(m.mensaje)
        break
    }
  }
  const limpiar = () => {
    if (cerrado) return
    cerrado = true
    nodo.disconnect()
    fuente.disconnect()
    stream.getTracks().forEach((t) => t.stop())
    void ctx.close().catch(() => undefined)
    cb.onCerrado()
  }
  let listo = false
  ws.onclose = (e) => {
    if (!listo) cb.onError(`El WebSocket de voz se cerró antes de estar listo (código ${e.code}). ¿Proxy sin ws:true o backend caído?`)
    limpiar()
  }
  ws.onerror = () => cb.onError('Error de conexión con el servidor de voz')
  const onListoOriginal = cb.onListo
  cb.onListo = (ms) => {
    listo = true
    onListoOriginal(ms)
  }

  const enviar = (o: unknown) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(o))
  return {
    pausar: () => {
      pausado = true
      enviar({ type: 'pausa' })
    },
    reanudar: () => {
      pausado = false
      vozDesde = 0
      enviar({ type: 'reanudar' })
    },
    forzarFinTurno: () => enviar({ type: 'fin_turno' }),
    cerrar: () => {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close()
      limpiar()
    },
  }
}
