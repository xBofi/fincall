/**
 * Voz en el navegador, sin proveedores de pago.
 *
 * - Captura con detección de fin de turno (VAD por energía): MediaRecorder + AudioContext.
 * - STT: Web Speech API (SpeechRecognition, Chrome/Edge) o el servidor (faster-whisper).
 * - TTS: SpeechSynthesis. Se mide el instante real de inicio de audio (`onstart`).
 * - Barge-in: vigilancia de energía del micrófono mientras habla el asistente.
 *
 * Todos los instantes son `performance.now()` para poder calcular la métrica principal:
 * final del audio del vecino -> inicio del audio del asistente.
 */

type RecognitionCtor = new () => SpeechRecognition

function obtenerReconocimiento(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export const sttNavegadorDisponible = (): boolean => obtenerReconocimiento() !== null
export const ttsDisponible = (): boolean => 'speechSynthesis' in window
export const capturaDisponible = (): boolean =>
  typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && 'MediaRecorder' in window

export interface OpcionesVad {
  /** Silencio tras voz que cierra el turno (ms). Cuanto menor, menor latencia y más cortes. */
  silencioMs: number
  /** Voz sostenida necesaria para considerar que ha empezado a hablar (ms). */
  inicioMs: number
  /** Umbral RMS de energía (0-1). */
  umbral: number
  /** Duración máxima de un turno (ms). */
  maxTurnoMs: number
  /** Sin voz durante este tiempo -> se aborta (ms). */
  sinVozMs: number
}

export const VAD_POR_DEFECTO: OpcionesVad = {
  silencioMs: 550,
  inicioMs: 200,
  umbral: 0.02,
  maxTurnoMs: 20_000,
  sinVozMs: 10_000,
}

export interface Captura {
  blob: Blob
  mime: string
  /** Instante en que terminó la voz del vecino (inicio del silencio), performance.now(). */
  finVozAt: number
  /** Instante en que el VAD decidió cerrar el turno. */
  decididoAt: number
  vozMs: number
}

export class SinVozError extends Error {}

/**
 * Graba hasta detectar fin de turno. `onEstado` informa 'esperando_voz' | 'hablando'.
 * Devuelve una función para cancelar.
 */
export function capturarTurno(
  opts: OpcionesVad,
  onEstado: (estado: 'esperando_voz' | 'hablando') => void,
): { resultado: Promise<Captura>; cancelar: () => void } {
  // `cancelar` se devuelve antes de tener micrófono: se marca y se aplica en cuanto lo haya
  let cancelado = false
  let detener = () => {}
  const resultado = new Promise<Captura>((resolve, reject) => {
    if (!capturaDisponible()) {
      reject(new Error('El navegador no permite capturar audio.'))
      return
    }
    navigator.mediaDevices
      .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      .then((stream) => {
        if (cancelado) {
          stream.getTracks().forEach((t) => t.stop())
          reject(new Error('cancelado'))
          return
        }
        const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : ''
        const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
        const chunks: BlobPart[] = []
        const ctx = new AudioContext()
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        const muestras = new Uint8Array(analyser.fftSize)
        const fuente = ctx.createMediaStreamSource(stream)
        fuente.connect(analyser)
        const inicio = performance.now()
        let candidatoVozAt = 0
        let vozAt = 0
        let silencioAt = 0
        let finVozAt = 0
        let decididoAt = 0
        let motivo = ''
        let frame = 0
        let terminado = false
        let temporizador = 0

        const terminar = (razon: string) => {
          if (terminado) return
          terminado = true
          motivo = razon
          decididoAt = performance.now()
          if (frame) cancelAnimationFrame(frame)
          window.clearTimeout(temporizador)
          if (recorder.state === 'recording') recorder.stop()
          else limpiar()
        }
        const limpiar = () => {
          stream.getTracks().forEach((t) => t.stop())
          fuente.disconnect()
          void ctx.close().catch(() => undefined)
        }
        detener = () => terminar('cancelado')

        const analizar = () => {
          if (terminado) return
          analyser.getByteTimeDomainData(muestras)
          let energia = 0
          for (const v of muestras) {
            const n = (v - 128) / 128
            energia += n * n
          }
          const esVoz = Math.sqrt(energia / muestras.length) >= opts.umbral
          const ahora = performance.now()
          if (esVoz) {
            candidatoVozAt ||= ahora
            if (!vozAt && ahora - candidatoVozAt >= opts.inicioMs) {
              vozAt = candidatoVozAt
              onEstado('hablando')
            }
            silencioAt = 0
          } else {
            candidatoVozAt = 0
          }
          if (!esVoz && vozAt) {
            silencioAt ||= ahora
            if (ahora - silencioAt >= opts.silencioMs) {
              finVozAt = silencioAt
              terminar('fin_de_turno')
              return
            }
          } else if (!vozAt && ahora - inicio >= opts.sinVozMs) {
            terminar('sin_voz')
            return
          }
          frame = requestAnimationFrame(analizar)
        }

        recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data)
        recorder.onerror = () => {
          terminado = true
          if (frame) cancelAnimationFrame(frame)
          window.clearTimeout(temporizador)
          limpiar()
          reject(new Error('No se ha podido grabar el audio.'))
        }
        recorder.onstop = () => {
          limpiar()
          if (motivo === 'sin_voz') {
            reject(new SinVozError('No he detectado voz.'))
            return
          }
          if (motivo === 'cancelado') {
            reject(new Error('cancelado'))
            return
          }
          if (!finVozAt) finVozAt = decididoAt
          resolve({
            blob: new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }),
            mime: recorder.mimeType || 'audio/webm',
            finVozAt,
            decididoAt,
            vozMs: Math.round(finVozAt - vozAt),
          })
        }
        onEstado('esperando_voz')
        recorder.start(250)
        temporizador = window.setTimeout(() => terminar(vozAt ? 'max_turno' : 'sin_voz'), opts.maxTurnoMs)
        analizar()
      })
      .catch((e: unknown) => reject(e instanceof Error ? e : new Error(String(e))))
  })
  const cancelar = () => {
    cancelado = true
    detener()
  }
  return { resultado, cancelar }
}

export interface ReconocimientoNavegador {
  texto: string
  /** `onspeechend` del navegador (aprox. fin de voz). */
  finVozAt: number
  resultadoAt: number
}

/** STT del navegador (Chrome/Edge). Un único enunciado; termina solo. */
export function reconocerEnNavegador(
  onEstado: (estado: 'esperando_voz' | 'hablando') => void,
): { resultado: Promise<ReconocimientoNavegador>; cancelar: () => void } {
  const Ctor = obtenerReconocimiento()
  let cancelar = () => {}
  const resultado = new Promise<ReconocimientoNavegador>((resolve, reject) => {
    if (!Ctor) {
      reject(new Error('Reconocimiento de voz no disponible en este navegador.'))
      return
    }
    const rec = new Ctor()
    rec.lang = 'es-ES'
    rec.interimResults = true
    rec.maxAlternatives = 1
    let finVozAt = 0
    let texto = ''
    let resuelto = false
    rec.onspeechstart = () => onEstado('hablando')
    rec.onspeechend = () => {
      finVozAt = performance.now()
    }
    rec.onresult = (e: SpeechRecognitionEvent) => {
      const r = e.results[e.results.length - 1]
      if (r?.isFinal) {
        texto = r[0]?.transcript ?? ''
        if (!finVozAt) finVozAt = performance.now()
        resuelto = true
        resolve({ texto: texto.trim(), finVozAt, resultadoAt: performance.now() })
        rec.stop()
      }
    }
    rec.onerror = (e: Event & { error?: string }) => {
      if (resuelto) return
      if (e.error === 'no-speech') reject(new SinVozError('No he detectado voz.'))
      else reject(new Error(e.error ?? 'error de reconocimiento'))
    }
    rec.onend = () => {
      if (!resuelto) reject(new SinVozError('No he detectado voz.'))
    }
    cancelar = () => {
      resuelto = true
      rec.abort()
      reject(new Error('cancelado'))
    }
    onEstado('esperando_voz')
    rec.start()
  })
  return { resultado, cancelar }
}

/** Ledger de salida: qué parte del texto llegó a sonar de verdad. */
export interface ResultadoReproduccion {
  interrumpida: boolean
  /** 'completada' | 'interrumpida' | 'no_reproducida' */
  estado: 'completada' | 'interrumpida' | 'no_reproducida'
  /** Fracción del texto entregada (0-1), por límites de palabra del sintetizador. */
  ratio: number
  textoEntregado: string
}

export interface Reproduccion {
  /** Se resuelve cuando empieza a sonar el audio (performance.now()). */
  inicio: Promise<number>
  /** Se resuelve al terminar (o al interrumpirse) con el ledger de lo entregado. */
  fin: Promise<ResultadoReproduccion>
  cancelar: () => void
}

let vozEs: SpeechSynthesisVoice | undefined
function elegirVoz(): SpeechSynthesisVoice | undefined {
  if (vozEs) return vozEs
  const voces = window.speechSynthesis.getVoices()
  vozEs =
    voces.find((v) => v.lang === 'es-ES' && /natural|online/i.test(v.name)) ??
    voces.find((v) => v.lang === 'es-ES') ??
    voces.find((v) => v.lang.startsWith('es'))
  return vozEs
}
if (ttsDisponible()) {
  window.speechSynthesis.onvoiceschanged = () => {
    vozEs = undefined
    elegirVoz()
  }
}

/** Habla y permite barge-in: si `permitirInterrupcion`, al detectar voz del vecino se corta. */
export function hablar(texto: string, permitirInterrupcion: boolean): Reproduccion {
  let resolverInicio: (t: number) => void = () => {}
  let resolverFin: (r: ResultadoReproduccion) => void = () => {}
  const inicio = new Promise<number>((r) => (resolverInicio = r))
  const fin = new Promise<ResultadoReproduccion>((r) => (resolverFin = r))
  let pararVigilancia = () => {}
  let terminado = false
  let empezo = false
  let charIndex = 0 // último límite de palabra alcanzado por el sintetizador
  let inicioAt = 0
  const ledger = (interrumpida: boolean): ResultadoReproduccion => {
    if (!empezo) return { interrumpida, estado: 'no_reproducida', ratio: 0, textoEntregado: '' }
    if (!interrumpida) return { interrumpida, estado: 'completada', ratio: 1, textoEntregado: texto }
    // Si el navegador no emite límites de palabra, se estima por tiempo (~14 caracteres/s)
    let corte = charIndex
    if (corte === 0 && inicioAt) corte = Math.min(texto.length, Math.round(((performance.now() - inicioAt) / 1000) * 14))
    return {
      interrumpida,
      estado: 'interrumpida',
      ratio: Math.max(0, Math.min(1, corte / Math.max(1, texto.length))),
      textoEntregado: texto.slice(0, corte),
    }
  }
  const terminar = (interrumpida: boolean) => {
    if (terminado) return
    terminado = true
    pararVigilancia()
    resolverFin(ledger(interrumpida))
  }
  if (!ttsDisponible()) {
    resolverInicio(performance.now())
    terminar(false)
    return { inicio, fin, cancelar: () => undefined }
  }
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(texto)
  u.lang = 'es-ES'
  u.rate = 1.05
  const voz = elegirVoz()
  if (voz) u.voice = voz
  u.onstart = () => {
    empezo = true
    inicioAt = performance.now()
    resolverInicio(inicioAt)
    if (permitirInterrupcion) {
      pararVigilancia = vigilarInterrupcion(() => {
        window.speechSynthesis.cancel()
        terminar(true)
      })
    }
  }
  u.onboundary = (e: SpeechSynthesisEvent) => {
    if (typeof e.charIndex === 'number') charIndex = e.charIndex + (e.charLength || 0)
  }
  u.onend = () => terminar(false)
  u.onerror = () => {
    resolverInicio(performance.now())
    terminar(!empezo ? false : true)
  }
  window.speechSynthesis.speak(u)
  return {
    inicio,
    fin,
    cancelar: () => {
      window.speechSynthesis.cancel()
      terminar(true)
    },
  }
}

export function silenciar(): void {
  if (ttsDisponible()) window.speechSynthesis.cancel()
}

/** Vigila el micrófono mientras habla el asistente; llama a `onVoz` si hay voz sostenida. */
function vigilarInterrupcion(onVoz: () => void): () => void {
  if (!capturaDisponible()) return () => undefined
  let parado = false
  let frame = 0
  let stream: MediaStream | undefined
  let ctx: AudioContext | undefined
  const inicio = performance.now()
  const parar = () => {
    parado = true
    if (frame) cancelAnimationFrame(frame)
    stream?.getTracks().forEach((t) => t.stop())
    void ctx?.close().catch(() => undefined)
  }
  navigator.mediaDevices
    .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
    .then((s) => {
      if (parado) {
        s.getTracks().forEach((t) => t.stop())
        return
      }
      stream = s
      ctx = new AudioContext()
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 1024
      const muestras = new Uint8Array(analyser.fftSize)
      ctx.createMediaStreamSource(s).connect(analyser)
      let vozAt = 0
      const analizar = () => {
        if (parado) return
        analyser.getByteTimeDomainData(muestras)
        let energia = 0
        for (const v of muestras) {
          const n = (v - 128) / 128
          energia += n * n
        }
        const ahora = performance.now()
        // Umbral más alto que el VAD normal: el altavoz puede colarse por el micrófono
        const voz = Math.sqrt(energia / muestras.length) >= 0.06
        if (ahora - inicio > 400 && voz) {
          vozAt ||= ahora
          if (ahora - vozAt >= 250) {
            parar()
            onVoz()
            return
          }
        } else if (!voz) {
          vozAt = 0
        }
        frame = requestAnimationFrame(analizar)
      }
      analizar()
    })
    .catch(() => undefined)
  return parar
}
