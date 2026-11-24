/**
 * A phone line in the browser without Twilio: the microphone goes to the server as μ-law 8 kHz
 * in 20 ms frames over a WebSocket that speaks the Twilio Media Streams protocol, and the agent's
 * voice comes back the same way. The server plugs it into the same bridge as a phone call
 * (server TTS, barge-in, delivery ledger, recording), so the console hears exactly what a
 * caller would, in any language, with no Twilio account or number.
 *
 * Same events as `twilioCall.ts`, so the simulator treats both alike.
 */
import { api, type CallView, type ServerTurnTimings } from './api'

export interface PhoneLineSession {
  close: () => void
}

interface Events {
  onView: (v: CallView) => void
  onTimings: (turns: ServerTurnTimings[]) => void
  onSpeaking: (who: 'bot' | 'caller' | null) => void
  onEnded: () => void
}

const RATE = 8000
/** 20 ms at 8 kHz: what the phone network (and the bridge) expects per frame */
const FRAME = 160
const REFRESH_MS = 700

// G.711 μ-law, as in ITU-T G.711 (the same table Python's audioop uses)
const BIAS = 0x84
const CLIP = 32635

function encodeSample(sample: number): number {
  let s = Math.max(-32768, Math.min(32767, Math.round(sample)))
  const sign = s < 0 ? 0x80 : 0
  if (sign) s = -s
  if (s > CLIP) s = CLIP
  s += BIAS
  let exponent = 7
  for (let mask = 0x4000; (s & mask) === 0 && exponent > 0; mask >>= 1) exponent--
  const mantissa = (s >> (exponent + 3)) & 0x0f
  return ~(sign | (exponent << 4) | mantissa) & 0xff
}

function decodeSample(byte: number): number {
  const u = ~byte & 0xff
  const sign = u & 0x80
  const exponent = (u >> 4) & 0x07
  const mantissa = u & 0x0f
  const magnitude = (((mantissa << 3) + BIAS) << exponent) - BIAS
  return sign ? -magnitude : magnitude
}

function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (const b of bytes) binary += String.fromCharCode(b)
  return btoa(binary)
}

function fromBase64(text: string): Uint8Array {
  const binary = atob(text)
  const out = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i)
  return out
}

const WORKLET = `
class Line8k extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(${FRAME}); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === ${FRAME}) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
    }
    return true;
  }
}
registerProcessor('line-8k', Line8k);
`

export async function openPhoneLine(callId: string, events: Events): Promise<PhoneLineSession> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  })
  // The browser resamples the microphone to the context's rate: 8 kHz, like a phone line
  const ctx = new AudioContext({ sampleRate: RATE })
  const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }))
  try {
    await ctx.audioWorklet.addModule(url)
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop())
    void ctx.close()
    throw e
  } finally {
    URL.revokeObjectURL(url)
  }
  const source = ctx.createMediaStreamSource(stream)
  const capture = new AudioWorkletNode(ctx, 'line-8k')
  source.connect(capture)

  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/telephony/browser/line`)
  let closed = false
  let lastVersion = ''
  // Playback: frames are scheduled back to back; a mark is answered when audio up to it played
  let playhead = 0
  const playing = new Set<AudioBufferSourceNode>()
  const pendingMarks: { name: string; at: number }[] = []

  const send = (o: unknown) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(o))

  capture.port.onmessage = (e: MessageEvent<Float32Array>) => {
    const frame = new Uint8Array(FRAME)
    let energy = 0
    e.data.forEach((s, i) => {
      frame[i] = encodeSample(s * 32767)
      energy += s * s
    })
    if (Math.sqrt(energy / FRAME) > 0.03 && playing.size === 0) events.onSpeaking('caller')
    send({ event: 'media', media: { payload: toBase64(frame) } })
  }

  const play = (ulaw: Uint8Array) => {
    const buffer = ctx.createBuffer(1, ulaw.length, RATE)
    const data = buffer.getChannelData(0)
    ulaw.forEach((b, i) => (data[i] = decodeSample(b) / 32768))
    const node = ctx.createBufferSource()
    node.buffer = buffer
    node.connect(ctx.destination)
    playhead = Math.max(playhead, ctx.currentTime + 0.05)
    node.start(playhead)
    playhead += buffer.duration
    playing.add(node)
    node.onended = () => {
      playing.delete(node)
      if (playing.size === 0) events.onSpeaking(null)
    }
    events.onSpeaking('bot')
  }

  const flushMarks = window.setInterval(() => {
    while (pendingMarks.length && pendingMarks[0].at <= ctx.currentTime) {
      send({ event: 'mark', mark: { name: pendingMarks.shift()!.name } })
    }
  }, 50)

  const refresh = window.setInterval(() => {
    api
      .get(callId)
      .then((v) => {
        const version = `${v.turn}:${v.history.length}:${v.finished}:${v.language}`
        if (version !== lastVersion) {
          lastVersion = version
          events.onView(v)
        }
      })
      .catch(() => undefined)
    api
      .callMetrics(callId)
      .then((m) => events.onTimings(m.turns))
      .catch(() => undefined)
  }, REFRESH_MS)

  const close = () => {
    if (closed) return
    closed = true
    window.clearInterval(refresh)
    window.clearInterval(flushMarks)
    send({ event: 'stop' })
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close()
    capture.disconnect()
    source.disconnect()
    stream.getTracks().forEach((t) => t.stop())
    void ctx.close().catch(() => undefined)
    void api.get(callId).then(events.onView).catch(() => undefined)
    events.onEnded()
  }

  ws.onopen = () => send({ event: 'start', start: { customParameters: { call_id: callId } } })
  ws.onmessage = (e) => {
    let m: { event?: string; media?: { payload: string }; mark?: { name: string } }
    try {
      m = JSON.parse(e.data as string)
    } catch {
      return
    }
    if (m.event === 'media' && m.media) play(fromBase64(m.media.payload))
    else if (m.event === 'mark' && m.mark) pendingMarks.push({ name: m.mark.name, at: playhead })
    else if (m.event === 'clear') {
      // Barge-in: the server stops the agent; what was queued is dropped
      playing.forEach((n) => n.stop())
      playing.clear()
      pendingMarks.length = 0
      playhead = ctx.currentTime
    }
  }
  // The agent hangs up after its farewell (it closes the line)
  ws.onclose = close
  ws.onerror = close
  return { close }
}
