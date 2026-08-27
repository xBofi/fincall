/**
 * Voz con la Web Speech API del navegador (sin proveedores de pago).
 * STT: SpeechRecognition (Chrome/Edge). TTS: SpeechSynthesis (todos los navegadores modernos).
 */

type RecognitionCtor = new () => SpeechRecognition

function obtenerReconocimiento(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export const sttDisponible = (): boolean => obtenerReconocimiento() !== null
export const ttsDisponible = (): boolean => 'speechSynthesis' in window

export function escuchar(
  onResultado: (texto: string) => void,
  onError: (mensaje: string) => void,
  onFin: () => void,
): (() => void) | null {
  const Ctor = obtenerReconocimiento()
  if (!Ctor) return null
  const rec = new Ctor()
  rec.lang = 'es-ES'
  rec.interimResults = false
  rec.maxAlternatives = 1
  rec.onresult = (e: SpeechRecognitionEvent) => {
    const texto = e.results[0]?.[0]?.transcript ?? ''
    if (texto) onResultado(texto)
  }
  rec.onerror = (e: Event & { error?: string }) => onError(e.error ?? 'error de reconocimiento')
  rec.onend = onFin
  rec.start()
  return () => rec.stop()
}

export function hablar(texto: string, onFin?: () => void): void {
  if (!ttsDisponible()) {
    onFin?.()
    return
  }
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(texto)
  u.lang = 'es-ES'
  const voz = window.speechSynthesis.getVoices().find((v) => v.lang.startsWith('es'))
  if (voz) u.voice = voz
  u.onend = () => onFin?.()
  u.onerror = () => onFin?.()
  window.speechSynthesis.speak(u)
}

export function silenciar(): void {
  if (ttsDisponible()) window.speechSynthesis.cancel()
}
