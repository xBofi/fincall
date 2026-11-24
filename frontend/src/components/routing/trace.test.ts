// Mapeo evento -> vista de la traza de enrutado. `corepack pnpm run test`
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  applyEvent,
  dwellMs,
  edgeIdOf,
  emptyTrace,
  firstStepOfTurn,
  fromDecisions,
  fromTurns,
  stateAt,
  viewAt,
} from './trace.ts'
import type { DemoRecording, TraceEvent, Visit } from './types.ts'

const visit = (over: Partial<Visit>): Visit => ({
  index: 0,
  turn: 1,
  node: 'next_step',
  at_ms: 0,
  ms: 0.2,
  target: 'identify_estate',
  awaiting_user: false,
  finished: false,
  consumed_utterance: false,
  condition: 'no hay finca',
  declared: true,
  llm_calls: 0,
  llm_ms: 0,
  interpretation: null,
  confidence_signals: null,
  trace: [],
  validations: [],
  said: [],
  diff: {},
  ...over,
})

const demo = JSON.parse(
  readFileSync(new URL('./demo/demoCall.json', import.meta.url), 'utf-8'),
) as DemoRecording

test('edge ids match the exported ids, and waiting is not an edge', () => {
  assert.equal(edgeIdOf(visit({})), 'next_step->identify_estate')
  assert.equal(edgeIdOf(visit({ node: 'notify_channel', target: 'end' })), 'notify_channel->__end__')
  // Ask on arrival: no edge. Ask again after an answer: the declared self edge
  assert.equal(edgeIdOf(visit({ node: 'identify_estate', target: 'identify_estate' })), null)
  assert.equal(
    edgeIdOf(visit({ node: 'identify_estate', target: 'identify_estate', consumed_utterance: true })),
    'identify_estate->identify_estate',
  )
})

test('live events: enter marks the running node, exit turns it into a step', () => {
  let m = emptyTrace()
  m = applyEvent(m, { type: 'node_enter', turn: 1, index: 0, node: 'collect_request', at_ms: 10 })
  assert.equal(m.running?.node, 'collect_request')
  assert.equal(viewAt(m, m.steps.length).node, 'collect_request')
  const exit = { type: 'node_exit', ...visit({ node: 'collect_request', target: 'next_step', llm_calls: 1, ms: 1200 }) } as TraceEvent
  m = applyEvent(m, exit)
  assert.equal(m.running, null)
  assert.equal(m.steps.length, 1)
  // The same visit again (SSE + turn response) never duplicates
  m = applyEvent(m, exit)
  assert.equal(m.steps.length, 1)
  m = applyEvent(m, {
    type: 'turn_end', turn: 1, caller: 'hay una fuga', assistant: ['¿Es correcto?'],
    current_node: 'confirm_summary', awaiting_user: true, finished: false, timings: { graph_ms: 1201 },
  })
  assert.equal(m.turns[0].caller, 'hay una fuga')
  assert.equal(m.turns[0].ended, true)
})

test('a late node_enter for a step already known is ignored', () => {
  let m = applyEvent(emptyTrace(), { type: 'node_exit', ...visit({}) } as TraceEvent)
  m = applyEvent(m, { type: 'node_enter', turn: 1, index: 0, node: 'next_step', at_ms: 1 })
  assert.equal(m.running, null)
})

test('snapshot seeds the last turn and the finished flag', () => {
  const m = applyEvent(emptyTrace(), {
    type: 'snapshot', turn: 2, finished: true,
    visits: [visit({ turn: 2, node: 'notify_channel', target: 'end', finished: true })],
    history: [{ role: 'caller', text: 'sí' }, { role: 'bot', text: 'adiós' }],
    bot_messages: ['adiós'],
  })
  assert.equal(m.finished, true)
  assert.equal(m.turns[0].caller, 'sí')
})

test('steps sort by turn and index whatever the arrival order', () => {
  let m = emptyTrace()
  for (const v of [visit({ turn: 2, index: 1 }), visit({ turn: 1, index: 0 }), visit({ turn: 2, index: 0 })]) {
    m = applyEvent(m, { type: 'node_exit', ...v } as TraceEvent)
  }
  assert.deepEqual(m.steps.map((s) => s.key), ['1:0', '2:0', '2:1'])
})

test('the recorded demo maps to a coherent view', () => {
  const m = fromTurns(demo.turns)
  const edges = new Set(demo.structure.edges.map((e) => e.id))
  for (const s of m.steps) {
    const e = edgeIdOf(s.visit)
    if (e) assert.ok(edges.has(e), `${e} is not an exported edge`)
  }
  const last = m.steps.length - 1
  assert.equal(m.finished, true)
  const end = viewAt(m, last)
  assert.equal(end.node, 'notify_channel')
  assert.equal(end.edge, 'notify_channel->__end__')
  assert.ok((end.visits.get('next_step') ?? 0) >= 5)
  assert.ok(end.turnEdges.includes('confirm_summary->validate_data'))
  assert.equal(end.turnEdges[0], '__start__->confirm_summary')
  // The state is the fold of the diffs: by the end there is an estate, a category and a home
  const state = stateAt(m, last)
  assert.equal(state.category, 'plumbing')
  assert.ok(state.estate_id)
  assert.ok(state.home_id)
  // Every caller turn links to its first node visit
  for (const t of m.turns) assert.ok(firstStepOfTurn(m, t.turn) >= 0)
})

test('the cursor before the first step shows nothing', () => {
  const v = viewAt(fromTurns(demo.turns), -1)
  assert.equal(v.node, null)
  assert.equal(v.visits.size, 0)
})

test('a waiting step is flagged, and retries are tracked per turn', () => {
  const m = fromTurns([{
    turn: 1, caller: 'perdone, ¿cómo?', assistant: [],
    visits: [visit({ node: 'identify_estate', target: 'identify_estate', consumed_utterance: true, awaiting_user: true })],
  }])
  const v = viewAt(m, 0)
  assert.equal(v.waiting, true)
  assert.ok(v.retried.has('identify_estate'))
})

test('legacy calls are rebuilt from decisions and marked synthetic', () => {
  const m = fromDecisions(
    [{ turn: 1, caller: 'calle Mayor 12', assistant: [], initial_node: 'identify_estate', final_node: 'identify_home' }],
    [{ turn: 1, node: 'identify_estate', action: 'estate_confirmed', detail: 'finca-mayor-12' }],
  )
  assert.deepEqual(m.steps.map((s) => s.visit.node), ['identify_estate', 'identify_home'])
  assert.ok(m.steps.every((s) => s.visit.synthetic))
  assert.equal(m.steps[0].visit.trace[0].action, 'estate_confirmed')
})

test('playback dwells on LLM visits for what they measured, within bounds', () => {
  const m = fromTurns(demo.turns)
  const llm = m.steps.find((s) => s.visit.llm_calls > 0)!
  const fast = m.steps.find((s) => s.visit.llm_calls === 0 && s.index > 0)!
  assert.ok(dwellMs(llm) > dwellMs(fast))
  assert.ok(dwellMs(llm) <= 2400 + 900)
  assert.equal(dwellMs(fast, 2), Math.round(dwellMs(fast) / 2))
})
