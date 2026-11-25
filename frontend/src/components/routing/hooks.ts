import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { operations, routing, type CallView } from '../../api'
import type { TraceEvent, TurnRecord } from './types'
import { applyEvent, dwellMs, emptyTrace, withTurns, type TraceModel } from './trace'

/** Tema, idioma y dirección del documento. La vista sigue `<html data-theme|lang|dir>`.
 * Con `useSyncExternalStore`: React vuelve a leer tras suscribirse, así que no se pierde un
 * cambio hecho entre el render y el efecto (antes, abrir `/demo?lang=ar` dejaba el lienzo en
 * LTR hasta recargar: el proveedor de idioma cambia `dir` en un efecto). */
const subscribeDocumentAttrs = (onChange: () => void) => {
  const obs = new MutationObserver(onChange)
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'dir', 'lang'] })
  return () => obs.disconnect()
}
const documentAttrsKey = () => {
  const el = document.documentElement
  return `${el.dataset.theme === 'dark' ? 'dark' : 'light'}|${el.dir === 'rtl' ? 'rtl' : 'ltr'}|${el.lang}`
}

export function useDocumentAttrs() {
  const key = useSyncExternalStore(subscribeDocumentAttrs, documentAttrsKey)
  return useMemo(() => {
    const [theme, dir, lang] = key.split('|')
    return { dark: theme === 'dark', rtl: dir === 'rtl', lang }
  }, [key])
}

export type LiveStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'ended'

/**
 * Traza en vivo de una llamada: el expediente guardado (turnos anteriores), el SSE (cada nodo
 * según entra y sale) y la respuesta de cada turno que ya tiene la consola. Las tres fuentes
 * se fusionan por `turno:índice`, así que el orden de llegada da igual.
 */
export function useLiveTrace(callId: string | null, view: CallView | null) {
  const [model, setModel] = useState<TraceModel>(emptyTrace)
  const [status, setStatus] = useState<LiveStatus>('idle')

  useEffect(() => {
    setModel(emptyTrace())
    if (!callId) {
      setStatus('idle')
      return
    }
    let closed = false
    setStatus('connecting')
    operations
      .detail(callId)
      .then((d) => { if (!closed) setModel((m) => withTurns(m, d.turns as TurnRecord[])) })
      .catch(() => undefined) // sin rol de revisión: solo lo que llegue en vivo
    const source = new EventSource(routing.eventsUrl(callId))
    const onEvent = (e: MessageEvent<string>) => {
      const event = JSON.parse(e.data) as TraceEvent
      setModel((m) => applyEvent(m, event))
      if ((event.type === 'turn_end' || event.type === 'snapshot') && event.finished) {
        setStatus('ended')
        source.close()
      } else setStatus('live')
    }
    for (const type of ['snapshot', 'node_enter', 'node_exit', 'turn_end']) {
      source.addEventListener(type, onEvent as EventListener)
    }
    source.onerror = () => {
      if (!closed && source.readyState !== EventSource.CLOSED) setStatus('reconnecting')
    }
    return () => {
      closed = true
      source.close()
    }
  }, [callId])

  useEffect(() => {
    if (!view || view.call_id !== callId) return
    setModel((m) => applyEvent(m, {
      type: 'snapshot',
      turn: view.turn,
      finished: view.finished,
      visits: view.visits ?? [],
      history: view.history,
      bot_messages: view.bot_messages,
      timings: view.timings as Record<string, unknown>,
    }))
    if (view.finished) setStatus('ended')
  }, [view, callId])

  return { model, status }
}

/** Cursor de reproducción: paso a paso, con la duración medida de cada nodo. */
export function usePlayback(model: TraceModel) {
  const [cursor, setCursor] = useState(-1)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  useEffect(() => {
    if (!playing) return
    if (cursor >= model.steps.length - 1) {
      setPlaying(false)
      return
    }
    const wait = cursor < 0 ? 400 : dwellMs(model.steps[cursor], speed)
    const id = window.setTimeout(() => setCursor((c) => c + 1), wait)
    return () => window.clearTimeout(id)
  }, [playing, cursor, model, speed])
  return { cursor, setCursor, playing, setPlaying, speed, setSpeed }
}
