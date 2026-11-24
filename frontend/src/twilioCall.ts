/**
 * Voice through Twilio: the browser dials Twilio with the Voice JavaScript SDK and Twilio sends
 * the call to the API exactly like a call to the phone number (TwiML webhook + Media Streams,
 * μ-law 8 kHz, the server's TTS, barge-in and delivery ledger). The console opens the call first
 * and passes its id, so it shows the call live while Twilio carries the audio.
 */
import { Call, Device } from '@twilio/voice-sdk'
import { api, telephony, type CallView, type ServerTurnTimings } from './api'

export interface TwilioSession {
  close: () => void
}

interface Events {
  onView: (v: CallView) => void
  onTimings: (turns: ServerTurnTimings[]) => void
  onSpeaking: (who: 'bot' | 'caller' | null) => void
  onEnded: () => void
}

/** How often the call view is refreshed while the agent talks over the phone line. */
const REFRESH_MS = 700

export async function openTwilio(callId: string, events: Events): Promise<TwilioSession> {
  const { token } = await telephony.browserToken()
  const device = new Device(token, { codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU] })
  let call: Call | null = null
  let closed = false
  let lastVersion = ''

  const close = () => {
    if (closed) return
    closed = true
    window.clearInterval(refresh)
    try {
      call?.disconnect()
    } catch {
      /* already gone */
    }
    device.destroy()
    void Promise.all([api.get(callId), api.callMetrics(callId)])
      .then(([v, m]) => {
        events.onView(v)
        events.onTimings(m.turns)
      })
      .catch(() => undefined)
    events.onEnded()
  }

  const refresh = window.setInterval(() => {
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
    api
      .callMetrics(callId)
      .then((m) => events.onTimings(m.turns))
      .catch(() => undefined)
  }, REFRESH_MS)

  try {
    call = await device.connect({ params: { call_id: callId } })
  } catch (e) {
    close()
    throw e
  }
  // A Twilio trial account plays "press any key to execute your code" before the call goes
  // through: the console presses it. On a paid account the digit just reaches the Media Stream,
  // which ignores DTMF.
  call.on('accept', () => {
    for (const ms of [1500, 4000]) window.setTimeout(() => !closed && call?.sendDigits('1'), ms)
  })
  call.on('volume', (input: number, output: number) => {
    if (input > 0.05) events.onSpeaking('caller')
    else if (output > 0.05) events.onSpeaking('bot')
    else events.onSpeaking(null)
  })
  // The agent hangs up after its farewell (it closes the Media Stream)
  call.on('disconnect', close)
  call.on('cancel', close)
  call.on('error', close)
  return { close }
}
