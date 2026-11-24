/**
 * Voz en el navegador, sin proveedores de pago.
 *
 * - Captura con detección de fin de turno (VAD por energía): MediaRecorder + AudioContext.
 * - STT: Web Speech API (SpeechRecognition, Chrome/Edge) o el servidor (faster-whisper).
 * - TTS: la voz del servidor (la misma del teléfono) en streaming si la hay; si no, o si falla,
 *   SpeechSynthesis. Se mide el instante real de inicio de audio.
 * - Barge-in: vigilancia de energía del micrófono mientras habla el asistente.
 *
 * Todos los instantes son `performance.now()` para poder calcular la métrica principal:
 * final del audio del vecino -> inicio del audio del asistente.
 */

type RecognitionCtor = new () => SpeechRecognition

function getRecognition(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export const sttBrowserAvailable = (): boolean => getRecognition() !== null
// Voz del servidor (`/api/voz-bot`): la consola suena como sonará el teléfono
let serverVoice = false
export function enableServerVoice(active: boolean): void {
  serverVoice = active
}
export const ttsAvailable = (): boolean => serverVoice || 'speechSynthesis' in window
export const captureAvailable = (): boolean =>
  typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && 'MediaRecorder' in window

export interface VadOptions {
  /** Silencio tras voz que cierra el turno (ms). Cuanto menor, menor latencia y más cortes. */
  silenceMs: number
  /** Voz sostenida necesaria para considerar que ha empezado a hablar (ms). */
  startMs: number
  /** Umbral RMS de energía (0-1). */
  threshold: number
  /** Duración máxima de un turno (ms). */
  maxTurnMs: number
  /** Sin voz durante este tiempo -> se aborta (ms). */
  noVoiceMs: number
}

export const DEFAULT_VAD: VadOptions = {
  silenceMs: 550,
  startMs: 200,
  threshold: 0.02,
  maxTurnMs: 20_000,
  noVoiceMs: 10_000,
}

export interface Capture {
  blob: Blob
  mime: string
  /** Instante en que terminó la voz del vecino (inicio del silencio), performance.now(). */
  voiceEndAt: number
  /** Instante en que el VAD decidió cerrar el turno. */
  decidedAt: number
  voiceMs: number
}

export class NoVoiceError extends Error {}

/**
 * Graba hasta detectar fin de turno. `onEstado` informa 'esperando_voz' | 'hablando'.
 * Devuelve una función para cancelar.
 */
export function captureTurn(
  opts: VadOptions,
  onState: (state: 'awaiting_voice' | 'speaking') => void,
): { result: Promise<Capture>; cancel: () => void } {
  // `cancelar` se devuelve antes de tener micrófono: se marca y se aplica en cuanto lo haya
  let cancelled = false
  let stop = () => {}
  const result = new Promise<Capture>((resolve, reject) => {
    if (!captureAvailable()) {
      reject(new Error('El navegador no permite capturar audio.'))
      return
    }
    navigator.mediaDevices
      .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          reject(new Error('cancelled'))
          return
        }
        const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : ''
        const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
        const chunks: BlobPart[] = []
        const ctx = new AudioContext()
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        const samples = new Uint8Array(analyser.fftSize)
        const source = ctx.createMediaStreamSource(stream)
        source.connect(analyser)
        const start = performance.now()
        let voiceCandidateAt = 0
        let voiceAt = 0
        let silenceAt = 0
        let voiceEndAt = 0
        let decidedAt = 0
        let reason = ''
        let frame = 0
        let done = false
        let timer = 0

        const finish = (reason: string) => {
          if (done) return
          done = true
          reason = reason
          decidedAt = performance.now()
          if (frame) cancelAnimationFrame(frame)
          window.clearTimeout(timer)
          if (recorder.state === 'recording') recorder.stop()
          else clean_up()
        }
        const clean_up = () => {
          stream.getTracks().forEach((t) => t.stop())
          source.disconnect()
          void ctx.close().catch(() => undefined)
        }
        stop = () => finish('cancelled')

        const analyze = () => {
          if (done) return
          analyser.getByteTimeDomainData(samples)
          let energy = 0
          for (const v of samples) {
            const n = (v - 128) / 128
            energy += n * n
          }
          const isVoice = Math.sqrt(energy / samples.length) >= opts.threshold
          const now = performance.now()
          if (isVoice) {
            voiceCandidateAt ||= now
            if (!voiceAt && now - voiceCandidateAt >= opts.startMs) {
              voiceAt = voiceCandidateAt
              onState('speaking')
            }
            silenceAt = 0
          } else {
            voiceCandidateAt = 0
          }
          if (!isVoice && voiceAt) {
            silenceAt ||= now
            if (now - silenceAt >= opts.silenceMs) {
              voiceEndAt = silenceAt
              finish('end_of_turn')
              return
            }
          } else if (!voiceAt && now - start >= opts.noVoiceMs) {
            finish('no_voice')
            return
          }
          frame = requestAnimationFrame(analyze)
        }

        recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data)
        recorder.onerror = () => {
          done = true
          if (frame) cancelAnimationFrame(frame)
          window.clearTimeout(timer)
          clean_up()
          reject(new Error('No se ha podido grabar el audio.'))
        }
        recorder.onstop = () => {
          clean_up()
          if (reason === 'no_voice') {
            reject(new NoVoiceError('No he detectado voz.'))
            return
          }
          if (reason === 'cancelled') {
            reject(new Error('cancelled'))
            return
          }
          if (!voiceEndAt) voiceEndAt = decidedAt
          resolve({
            blob: new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }),
            mime: recorder.mimeType || 'audio/webm',
            voiceEndAt,
            decidedAt,
            voiceMs: Math.round(voiceEndAt - voiceAt),
          })
        }
        onState('awaiting_voice')
        recorder.start(250)
        timer = window.setTimeout(() => finish(voiceAt ? 'max_turn' : 'no_voice'), opts.maxTurnMs)
        analyze()
      })
      .catch((e: unknown) => reject(e instanceof Error ? e : new Error(String(e))))
  })
  const cancel = () => {
    cancelled = true
    stop()
  }
  return { result, cancel }
}

export interface BrowserRecognition {
  text: string
  /** `onspeechend` del navegador (aprox. fin de voz). */
  voiceEndAt: number
  resultAt: number
}

/** BCP-47 tag of the call's language for the browser's speech APIs (`GET /api/languages`,
 * `browser_lang`). The console sets it when a call starts or switches language. */
let speechLang = 'es-ES'

export function setSpeechLanguage(tag: string): void {
  if (tag && tag !== speechLang) {
    speechLang = tag
    chosenVoice = undefined
  }
}

/** STT del navegador (Chrome/Edge). Un único enunciado; termina solo. */
export function recognizeInBrowser(
  onState: (state: 'awaiting_voice' | 'speaking') => void,
): { result: Promise<BrowserRecognition>; cancel: () => void } {
  const Ctor = getRecognition()
  let cancel = () => {}
  const result = new Promise<BrowserRecognition>((resolve, reject) => {
    if (!Ctor) {
      reject(new Error('Reconocimiento de voz no disponible en este navegador.'))
      return
    }
    const rec = new Ctor()
    rec.lang = speechLang
    rec.interimResults = true
    rec.maxAlternatives = 1
    let voiceEndAt = 0
    let text = ''
    let resuelto = false
    rec.onspeechstart = () => onState('speaking')
    rec.onspeechend = () => {
      voiceEndAt = performance.now()
    }
    rec.onresult = (e: SpeechRecognitionEvent) => {
      const r = e.results[e.results.length - 1]
      if (r?.isFinal) {
        text = r[0]?.transcript ?? ''
        if (!voiceEndAt) voiceEndAt = performance.now()
        resuelto = true
        resolve({ text: text.trim(), voiceEndAt, resultAt: performance.now() })
        rec.stop()
      }
    }
    rec.onerror = (e: Event & { error?: string }) => {
      if (resuelto) return
      if (e.error === 'no-speech') reject(new NoVoiceError('No he detectado voz.'))
      else reject(new Error(e.error ?? 'error de reconocimiento'))
    }
    rec.onend = () => {
      if (!resuelto) reject(new NoVoiceError('No he detectado voz.'))
    }
    cancel = () => {
      resuelto = true
      rec.abort()
      reject(new Error('cancelled'))
    }
    onState('awaiting_voice')
    rec.start()
  })
  return { result, cancel }
}

/** Ledger de salida: qué parte del texto llegó a sonar de verdad. */
export interface PlaybackResult {
  interrupted: boolean
  /** 'completada' | 'interrumpida' | 'no_reproducida' */
  state: 'completed' | 'interrupted' | 'not_played'
  /** Fracción del texto entregada (0-1), por límites de palabra del sintetizador. */
  ratio: number
  deliveredText: string
}

export interface Playback {
  /** Se resuelve cuando empieza a sonar el audio (performance.now()). */
  start: Promise<number>
  /** Se resuelve al terminar (o al interrumpirse) con el ledger de lo entregado. */
  end: Promise<PlaybackResult>
  cancel: () => void
}

let chosenVoice: SpeechSynthesisVoice | undefined
/** The best installed voice for the call's language: a natural/online one if there is any. */
function chooseVoice(): SpeechSynthesisVoice | undefined {
  if (chosenVoice) return chosenVoice
  const voices = window.speechSynthesis.getVoices()
  const base = speechLang.slice(0, 2)
  chosenVoice =
    voices.find((v) => v.lang === speechLang && /natural|online/i.test(v.name)) ??
    voices.find((v) => v.lang === speechLang) ??
    voices.find((v) => v.lang.startsWith(base))
  return chosenVoice
}
if (ttsAvailable()) {
  window.speechSynthesis.onvoiceschanged = () => {
    chosenVoice = undefined
    chooseVoice()
  }
}

/** Habla y permite barge-in: si `permitirInterrupcion`, al detectar voz del vecino se corta. */
export function speak(text: string, allowInterruption: boolean): Playback {
  return serverVoice ? speakWithServer(text, allowInterruption) : speakWithBrowser(text, allowInterruption)
}

let voiceCtx: AudioContext | undefined
let stopServer = () => {}

/** Reproduce el PCM16 de `/api/voz-bot` según llega: suena con el primer trozo, no con la frase. */
function speakWithServer(text: string, allowInterruption: boolean): Playback {
  let resolveStart: (t: number) => void = () => {}
  let resolveEnd: (r: PlaybackResult) => void = () => {}
  const start = new Promise<number>((r) => (resolveStart = r))
  const end = new Promise<PlaybackResult>((r) => (resolveEnd = r))
  stopServer()
  const aborter = new AbortController()
  const ctx = (voiceCtx ??= new AudioContext())
  void ctx.resume()
  const sources: AudioBufferSourceNode[] = []
  let startCtx = 0 // instante del contexto de audio en que empieza a sonar
  let upcoming = 0 // instante en que empieza el próximo trozo
  let started = false
  let full = false // ya llegó todo el audio
  let done = false
  let respaldo: Playback | undefined
  let stopWatching = () => {}

  const finish = (interrupted: boolean) => {
    if (done) return
    done = true
    const played = started ? Math.max(0, ctx.currentTime - startCtx) : 0
    const total = upcoming - startCtx
    stopWatching()
    aborter.abort()
    sources.forEach((f) => {
      try {
        f.stop()
      } catch {
        // aún no había empezado
      }
    })
    if (!started) {
      resolveStart(performance.now())
      resolveEnd({ interrupted, state: 'not_played', ratio: 0, deliveredText: '' })
      return
    }
    if (!interrupted) {
      resolveEnd({ interrupted, state: 'completed', ratio: 1, deliveredText: text })
      return
    }
    // Lo que sonó, por tiempo: exacto si ya llegó todo el audio; si no, ~14 caracteres/s
    const estimated = full && total > 0 ? played / total : (played * 14) / Math.max(1, text.length)
    const ratio = Math.max(0, Math.min(1, estimated))
    resolveEnd({ interrupted, state: 'interrupted', ratio, deliveredText: text.slice(0, Math.round(ratio * text.length)) })
  }

  const enqueue = (data: Uint8Array, rate: number) => {
    const samples = new Int16Array(data.slice().buffer)
    const buffer = ctx.createBuffer(1, samples.length, rate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768
    const source = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(ctx.destination)
    if (!started) {
      started = true
      startCtx = ctx.currentTime + 0.03
      upcoming = startCtx
      resolveStart(performance.now() + (startCtx - ctx.currentTime) * 1000)
      if (allowInterruption) stopWatching = watchInterruption(() => finish(true))
    }
    const en = Math.max(upcoming, ctx.currentTime)
    source.start(en)
    upcoming = en + buffer.duration
    sources.push(source)
    source.onended = () => {
      if (full && source === sources[sources.length - 1]) finish(false)
    }
  }

  void (async () => {
    try {
      const r = await fetch('/api/voz-bot', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: aborter.signal,
      })
      if (!r.ok || !r.body) throw new Error(`voz del servidor: ${r.status}`)
      const rate = Number(r.headers.get('X-Tasa')) || 16000
      const reader = r.body.getReader()
      let remainder: Uint8Array | null = null // PCM16 va por pares de bytes
      for (;;) {
        const { done, value } = await reader.read()
        if (done || done) break
        let data = value
        if (remainder) {
          data = new Uint8Array(remainder.length + value.length)
          data.set(remainder)
          data.set(value, remainder.length)
          remainder = null
        }
        const evenLength = data.length - (data.length % 2)
        if (evenLength < data.length) remainder = data.slice(evenLength)
        if (evenLength) enqueue(data.subarray(0, evenLength), rate)
      }
      full = true
      if (!started || ctx.currentTime >= upcoming) finish(false)
    } catch {
      if (done) return
      if (started) {
        finish(true)
        return
      }
      // Sin voz del servidor: habla el navegador para no dejar al vecino en silencio
      respaldo = speakWithBrowser(text, allowInterruption)
      void respaldo.start.then(resolveStart)
      void respaldo.end.then((f) => {
        done = true
        resolveEnd(f)
      })
    }
  })()
  const cancel = () => (respaldo ? respaldo.cancel() : finish(true))
  stopServer = cancel
  return { start, end, cancel }
}

function speakWithBrowser(text: string, allowInterruption: boolean): Playback {
  let resolveStart: (t: number) => void = () => {}
  let resolveEnd: (r: PlaybackResult) => void = () => {}
  const start = new Promise<number>((r) => (resolveStart = r))
  const end = new Promise<PlaybackResult>((r) => (resolveEnd = r))
  let stopWatching = () => {}
  let done = false
  let started = false
  let charIndex = 0 // último límite de palabra alcanzado por el sintetizador
  let startAt = 0
  const ledger = (interrupted: boolean): PlaybackResult => {
    if (!started) return { interrupted, state: 'not_played', ratio: 0, deliveredText: '' }
    if (!interrupted) return { interrupted, state: 'completed', ratio: 1, deliveredText: text }
    // Si el navegador no emite límites de palabra, se estima por tiempo (~14 caracteres/s)
    let cut = charIndex
    if (cut === 0 && startAt) cut = Math.min(text.length, Math.round(((performance.now() - startAt) / 1000) * 14))
    return {
      interrupted,
      state: 'interrupted',
      ratio: Math.max(0, Math.min(1, cut / Math.max(1, text.length))),
      deliveredText: text.slice(0, cut),
    }
  }
  const finish = (interrupted: boolean) => {
    if (done) return
    done = true
    stopWatching()
    resolveEnd(ledger(interrupted))
  }
  if (!('speechSynthesis' in window)) {
    resolveStart(performance.now())
    finish(false)
    return { start, end, cancel: () => undefined }
  }
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text)
  u.lang = speechLang
  u.rate = 1.05
  const voice = chooseVoice()
  if (voice) u.voice = voice
  u.onstart = () => {
    started = true
    startAt = performance.now()
    resolveStart(startAt)
    if (allowInterruption) {
      stopWatching = watchInterruption(() => {
        window.speechSynthesis.cancel()
        finish(true)
      })
    }
  }
  u.onboundary = (e: SpeechSynthesisEvent) => {
    if (typeof e.charIndex === 'number') charIndex = e.charIndex + (e.charLength || 0)
  }
  u.onend = () => finish(false)
  u.onerror = () => {
    resolveStart(performance.now())
    finish(!started ? false : true)
  }
  window.speechSynthesis.speak(u)
  return {
    start,
    end,
    cancel: () => {
      window.speechSynthesis.cancel()
      finish(true)
    },
  }
}

export function mute(): void {
  stopServer()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}

/** Vigila el micrófono mientras habla el asistente; llama a `onVoz` si hay voz sostenida. */
function watchInterruption(onVoice: () => void): () => void {
  if (!captureAvailable()) return () => undefined
  let parado = false
  let frame = 0
  let stream: MediaStream | undefined
  let ctx: AudioContext | undefined
  const start = performance.now()
  const stop = () => {
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
      const samples = new Uint8Array(analyser.fftSize)
      ctx.createMediaStreamSource(s).connect(analyser)
      let voiceAt = 0
      const analyze = () => {
        if (parado) return
        analyser.getByteTimeDomainData(samples)
        let energy = 0
        for (const v of samples) {
          const n = (v - 128) / 128
          energy += n * n
        }
        const now = performance.now()
        // Umbral más alto que el VAD normal: el altavoz puede colarse por el micrófono
        const voice = Math.sqrt(energy / samples.length) >= 0.06
        if (now - start > 400 && voice) {
          voiceAt ||= now
          if (now - voiceAt >= 250) {
            stop()
            onVoice()
            return
          }
        } else if (!voice) {
          voiceAt = 0
        }
        frame = requestAnimationFrame(analyze)
      }
      analyze()
    })
    .catch(() => undefined)
  return stop
}
