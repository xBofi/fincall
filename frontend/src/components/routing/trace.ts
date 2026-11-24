/**
 * De eventos y turnos guardados a lo que la vista pinta. Puro y sin React: lo prueban
 * `trace.test.ts` con `node --test`.
 *
 * Un *paso* es una visita a un nodo. Los pasos se ordenan por (turno, índice) y se identifican
 * por `turno:índice`, así que da igual que una visita llegue por SSE, en la respuesta del turno
 * o desde el expediente guardado: se fusiona, nunca se duplica.
 */
import type { TraceEvent, TurnRecord, Visit } from './types.ts'

export const END_ID = '__end__'
export const ENTRY_ID = '__start__'

export interface Step {
  key: string
  turn: number
  index: number
  visit: Visit
}

export interface TurnInfo {
  turn: number
  caller: string | null
  assistant: string[]
  timings: Record<string, unknown>
  ended: boolean
}

export interface TraceModel {
  steps: Step[]
  turns: TurnInfo[]
  /** Nodo en ejecución ahora mismo (entró y aún no salió). Solo en vivo. */
  running: { turn: number; index: number; node: string; at_ms: number } | null
  finished: boolean
}

export const emptyTrace = (): TraceModel => ({ steps: [], turns: [], running: null, finished: false })

const stepKey = (turn: number, index: number) => `${turn}:${index}`

const byOrder = (a: Step, b: Step) => a.turn - b.turn || a.index - b.index

function withSteps(model: TraceModel, visits: Visit[]): TraceModel {
  if (visits.length === 0) return model
  const map = new Map(model.steps.map((s) => [s.key, s]))
  for (const v of visits) {
    const key = stepKey(v.turn, v.index)
    map.set(key, { key, turn: v.turn, index: v.index, visit: v })
  }
  const steps = [...map.values()].sort(byOrder)
  const last = steps[steps.length - 1]
  const running =
    model.running && map.has(stepKey(model.running.turn, model.running.index)) ? null : model.running
  return { ...model, steps, running, finished: model.finished || Boolean(last?.visit.finished) }
}

function withTurn(model: TraceModel, info: Partial<TurnInfo> & { turn: number }): TraceModel {
  const turns = [...model.turns]
  const i = turns.findIndex((t) => t.turn === info.turn)
  const base: TurnInfo = i >= 0 ? turns[i] : { turn: info.turn, caller: null, assistant: [], timings: {}, ended: false }
  const merged: TurnInfo = {
    ...base,
    ...Object.fromEntries(Object.entries(info).filter(([, v]) => v !== undefined)),
  } as TurnInfo
  if (i >= 0) turns[i] = merged
  else turns.push(merged)
  turns.sort((a, b) => a.turn - b.turn)
  return { ...model, turns }
}

/** Un evento del SSE (`/api/calls/{id}/events`). */
export function applyEvent(model: TraceModel, event: TraceEvent): TraceModel {
  switch (event.type) {
    case 'node_enter':
      if (model.steps.some((s) => s.key === stepKey(event.turn, event.index))) return model
      return { ...model, running: { turn: event.turn, index: event.index, node: event.node, at_ms: event.at_ms } }
    case 'node_exit': {
      const { type: _type, ...visit } = event
      void _type
      return withSteps(model, [visit])
    }
    case 'turn_end':
      return {
        ...withTurn(model, {
          turn: event.turn,
          caller: event.caller,
          assistant: event.assistant,
          timings: event.timings,
          ended: true,
        }),
        running: null,
        finished: model.finished || event.finished,
      }
    case 'snapshot': {
      let next = withSteps(model, event.visits ?? [])
      if ((event.visits ?? []).length > 0) {
        const history = event.history ?? []
        const lastCaller = [...history].reverse().find((h) => h.role === 'caller')
        next = withTurn(next, {
          turn: event.turn,
          caller: event.turn === 0 ? null : lastCaller?.text ?? null,
          assistant: event.bot_messages ?? [],
          timings: event.timings ?? {},
          ended: true,
        })
      }
      return { ...next, finished: next.finished || event.finished }
    }
  }
}

/** Turnos guardados (expediente o grabación de la demo). */
export function fromTurns(turns: TurnRecord[]): TraceModel {
  return withTurns(emptyTrace(), turns)
}

/** Añade turnos guardados a un modelo que ya puede tener eventos en vivo. */
export function withTurns(initial: TraceModel, turns: TurnRecord[]): TraceModel {
  let model = initial
  for (const t of turns) {
    model = withSteps(model, t.visits ?? [])
    model = withTurn(model, {
      turn: t.turn,
      caller: t.caller,
      assistant: t.assistant ?? [],
      timings: (t.timings ?? {}) as Record<string, unknown>,
      ended: true,
    })
  }
  return model
}

/**
 * Llamadas grabadas antes de que existieran las visitas: se reconstruye la secuencia de nodos
 * con el nodo inicial de cada turno y sus decisiones. Sin tiempos ni motivo, y marcada como tal.
 */
export function fromDecisions(
  turns: TurnRecord[],
  decisions: { turn: number; node: string; action: string; detail: string }[],
): TraceModel {
  const synthetic: TurnRecord[] = turns.map((t) => {
    const nodes: string[] = []
    const push = (n: string | null | undefined) => {
      if (n && nodes[nodes.length - 1] !== n) nodes.push(n)
    }
    push(t.initial_node ?? null)
    const own = decisions.filter((d) => d.turn === t.turn)
    for (const d of own) push(d.node)
    push(t.final_node ?? null)
    const visits: Visit[] = nodes.map((node, index) => ({
      index,
      turn: t.turn,
      node,
      at_ms: 0,
      ms: 0,
      target: nodes[index + 1] ?? node,
      awaiting_user: index === nodes.length - 1,
      finished: false,
      consumed_utterance: false,
      condition: null,
      declared: true,
      llm_calls: 0,
      llm_ms: 0,
      interpretation: null,
      confidence_signals: null,
      trace: own.filter((d) => d.node === node).map((d) => ({ action: d.action, detail: d.detail })),
      validations: [],
      said: [],
      diff: {},
      synthetic: true,
    }))
    return { ...t, visits }
  })
  return fromTurns(synthetic)
}

/** Arista que la visita tomó al salir, con el id de `graph/export.py`, o null si se quedó a esperar. */
export function edgeIdOf(visit: Visit): string | null {
  const target = visit.target === 'end' ? END_ID : visit.target
  if (target === visit.node) return visit.consumed_utterance ? `${visit.node}->${visit.node}` : null
  return `${visit.node}->${target}`
}

/** Arista con la que el turno entra en el grafo: del punto de entrada al nodo donde se retoma. */
export function entryEdgeOf(step: Step): string | null {
  return step.index === 0 ? `${ENTRY_ID}->${step.visit.node}` : null
}

export interface ViewAt {
  /** Nodo del paso actual (o en ejecución). */
  node: string | null
  /** Arista tomada al salir del paso actual. */
  edge: string | null
  /** El turno entra por aquí. */
  entryEdge: string | null
  /** Visitas por nodo hasta el cursor. */
  visits: Map<string, number>
  /** Veces que se tomó cada arista hasta el cursor. */
  taken: Map<string, number>
  /** Aristas tomadas en el turno del cursor, en orden. */
  turnEdges: string[]
  /** Último tiempo medido por nodo. */
  lastMs: Map<string, number>
  /** Nodos que repreguntaron en el turno del cursor. */
  retried: Set<string>
  waiting: boolean
  turn: number | null
}

/** Lo que se ve con el cursor en `cursor` (índice de paso; -1 = nada aún). */
export function viewAt(model: TraceModel, cursor: number): ViewAt {
  const visits = new Map<string, number>()
  const taken = new Map<string, number>()
  const lastMs = new Map<string, number>()
  const retried = new Set<string>()
  const upto = Math.min(cursor, model.steps.length - 1)
  const current = upto >= 0 ? model.steps[upto] : null
  const turnEdges: string[] = []
  for (let i = 0; i <= upto; i++) {
    const s = model.steps[i]
    visits.set(s.visit.node, (visits.get(s.visit.node) ?? 0) + 1)
    if (!s.visit.synthetic) lastMs.set(s.visit.node, s.visit.ms)
    const edge = edgeIdOf(s.visit)
    if (edge) taken.set(edge, (taken.get(edge) ?? 0) + 1)
    if (current && s.turn === current.turn) {
      const entry = entryEdgeOf(s)
      if (entry) turnEdges.push(entry)
      if (edge) turnEdges.push(edge)
      if (edge && s.visit.target === s.visit.node) retried.add(s.visit.node)
    }
  }
  const live = cursor >= model.steps.length && model.running
  return {
    node: live ? model.running!.node : current?.visit.node ?? null,
    edge: live ? null : current ? edgeIdOf(current.visit) : null,
    entryEdge: current ? entryEdgeOf(current) : null,
    visits,
    taken,
    turnEdges,
    lastMs,
    retried,
    waiting: Boolean(current && !live && current.visit.awaiting_user),
    turn: live ? model.running!.turn : current?.turn ?? null,
  }
}

/** Estado de negocio acumulado tras el paso `cursor`: el "después" de cada diff, en orden. */
export function stateAt(model: TraceModel, cursor: number): Record<string, unknown> {
  const state: Record<string, unknown> = {}
  for (let i = 0; i <= Math.min(cursor, model.steps.length - 1); i++) {
    for (const [k, d] of Object.entries(model.steps[i].visit.diff)) state[k] = d.after
  }
  return state
}

/** Primer paso de un turno, para saltar desde la conversación. */
export function firstStepOfTurn(model: TraceModel, turn: number): number {
  return model.steps.findIndex((s) => s.turn === turn)
}

/**
 * Cuánto se queda la reproducción en un paso (ms). Las visitas con modelo duran lo que midieron
 * (acotado para que la demo no aburra); las deterministas, lo justo para verse.
 */
export function dwellMs(step: Step, speed = 1): number {
  const base = step.visit.llm_calls > 0 ? Math.min(Math.max(step.visit.ms, 700), 2400) : 520
  const turnPause = step.index === 0 && step.turn > 0 ? 900 : 0
  return Math.round((base + turnPause) / speed)
}
