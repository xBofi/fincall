/** Contratos de la vista de enrutado. Reflejan `graph/export.py` y `graph/tracing.py`. */

export type Role = 'entry' | 'router' | 'llm' | 'tool' | 'human' | 'end'
export type EdgeKind = 'forward' | 'return' | 'retry' | 'global'

export interface StructureNode {
  id: string
  role: Role
  kind?: string | null
  title: string
  summary: string
  stage: number
  uses_llm: boolean
  deterministic_paths?: string[]
  effects?: string[]
}

export interface StructureEdge {
  id: string
  source: string
  target: string
  condition: string
  conditional: boolean
  router: string
  kind: EdgeKind
  times?: number
}

export interface GraphStructure {
  graph: string
  entry: string
  end: string
  hub: string
  routers: Record<string, string>
  nodes: StructureNode[]
  edges: StructureEdge[]
  entry_targets: string[]
  compiled: { nodes: number; edges: number; conditional_edges: number }
  drift: string[]
  violations?: string[]
}

export interface DiffEntry {
  before: unknown
  after: unknown
}

export interface Visit {
  index: number
  turn: number
  node: string
  at_ms: number
  ms: number
  target: string
  awaiting_user: boolean
  finished: boolean
  consumed_utterance: boolean
  condition: string | null
  declared: boolean
  llm_calls: number
  llm_ms: number
  interpretation: Record<string, unknown> | null
  confidence_signals: Record<string, unknown> | null
  trace: { action: string; detail: string }[]
  validations: string[]
  said: string[]
  diff: Record<string, DiffEntry>
  /** Reconstruida de las decisiones de una llamada antigua: sin tiempos ni motivo. */
  synthetic?: boolean
}

export interface TurnRecord {
  turn: number
  caller: string | null
  assistant: string[]
  timings?: Record<string, unknown>
  visits?: Visit[]
  initial_node?: string | null
  final_node?: string | null
}

export type TraceEvent =
  | { type: 'node_enter'; turn: number; index: number; node: string; at_ms: number }
  | ({ type: 'node_exit' } & Visit)
  | {
      type: 'turn_end'
      turn: number
      caller: string | null
      assistant: string[]
      current_node: string
      awaiting_user: boolean
      finished: boolean
      timings: Record<string, unknown>
    }
  | {
      type: 'snapshot'
      turn: number
      finished: boolean
      visits?: Visit[]
      history?: { role: string; text: string }[]
      bot_messages?: string[]
      timings?: Record<string, unknown>
    }

export interface DemoRecording {
  meta: { recorded_at: string; llm: string; result: string | null; note: string }
  structure: GraphStructure
  turns: TurnRecord[]
}
