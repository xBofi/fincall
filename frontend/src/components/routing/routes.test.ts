// Rutas de un nodo seleccionado y caché de la disposición. `corepack pnpm run test`
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ELK from 'elkjs/lib/elk.bundled.js'
import { BADGE_GAP, badgeAnchor, handlesFor, outgoingRoutes, routeNumbers, targetPoint } from './routes.ts'
import { cachedLayout, elkGraph, layoutKey, placedFrom, rememberLayout, type Placed } from './layoutCache.ts'
import type { DemoRecording, GraphStructure } from './types.ts'

const demo = JSON.parse(
  readFileSync(new URL('./demo/demoCall.json', import.meta.url), 'utf-8'),
) as DemoRecording
const s = demo.structure

// Lo que mide la marca numerada en el lienzo (routing.css: 18 px de alto, hasta dos cifras)
const BADGE = { w: 24, h: 18 }

test('routes of a node: numbered from 1 in manifest order, without retries or self-loops', () => {
  for (const n of s.nodes) {
    const routes = outgoingRoutes(s, n.id)
    assert.ok(routes.every((e) => e.source === n.id && e.kind !== 'retry' && e.target !== n.id))
    const numbers = routeNumbers(s, n.id)
    assert.deepEqual([...numbers.values()], routes.map((_, i) => i + 1))
    // Un número por destino: ninguna ruta repite destino, así que ninguna marca repite sitio
    assert.equal(new Set(routes.map((e) => e.target)).size, routes.length, n.id)
  }
  assert.ok(outgoingRoutes(s, s.hub).length >= 10, 'the hub is the busy case that used to overlap')
  assert.equal(routeNumbers(s, null).size, 0)
})

test('badge anchors sit just outside the side the arrow enters by', () => {
  assert.deepEqual(badgeAnchor(100, 50, 'left'), { x: 100 - BADGE_GAP, y: 50 })
  assert.deepEqual(badgeAnchor(100, 50, 'right'), { x: 100 + BADGE_GAP, y: 50 })
  assert.deepEqual(badgeAnchor(100, 50, 'top'), { x: 100, y: 50 - BADGE_GAP })
  const box: Placed = { x: 10, y: 20, width: 100, height: 40 }
  assert.deepEqual(targetPoint('in', box), { x: 10, y: 40, side: 'left' })
  assert.deepEqual(targetPoint('in', box, true), { x: 110, y: 40, side: 'right' })
  assert.deepEqual(targetPoint('loopin', box), { x: 110, y: 40, side: 'right' })
  assert.deepEqual(targetPoint('up', box), { x: 60, y: 20, side: 'top' })
})

const overlaps = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

/** Para cada nodo seleccionado: sus marcas no se pisan entre sí ni tapan el centro de ninguna
 * caja (pueden rozar un borde: van por encima de las cajas, en z, así que se leen igual). */
function badgeCollisions(structure: GraphStructure, placed: Map<string, Placed>, rtl: boolean): string[] {
  const problems: string[] = []
  for (const n of structure.nodes) {
    const rects = outgoingRoutes(structure, n.id).map((e) => {
      const from = placed.get(e.source)!
      const to = placed.get(e.target)!
      const { targetHandle } = handlesFor(e.kind, from, to, rtl)
      const p = targetPoint(targetHandle, to, rtl)
      const c = badgeAnchor(p.x, p.y, p.side)
      return { id: e.id, x: c.x - BADGE.w / 2, y: c.y - BADGE.h / 2, w: BADGE.w, h: BADGE.h }
    })
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        if (overlaps(rects[i], rects[j])) problems.push(`${rects[i].id} × ${rects[j].id}`)
      }
      for (const [id, box] of placed) {
        // El núcleo de la caja (sin 6 px de borde): título y rol, lo que no se debe tapar
        const core = { x: box.x + 6, y: box.y + 6, w: box.width - 12, h: box.height - 12 }
        if (overlaps(rects[i], core)) problems.push(`${rects[i].id} covers ${id}`)
      }
    }
  }
  return problems
}

for (const [aspect, rtl] of [[2.5, false], [1.5, false], [0.75, false], [2.5, true]] as const) {
  test(`route badges never collide on the real layout (aspect ${aspect}${rtl ? ', RTL' : ''})`, async () => {
    const elk = new ELK()
    const placed = placedFrom(await elk.layout(elkGraph(s, aspect)), rtl)
    assert.deepEqual(badgeCollisions(s, placed, rtl), [])
  })
}

test('layout key: depends on shape, direction and canvas, not on labels', () => {
  const base = layoutKey(s, false, 2)
  const relabelled: GraphStructure = { ...s, nodes: s.nodes.map((n) => ({ ...n, title: `${n.title}!` })), edges: s.edges.map((e) => ({ ...e, condition: 'x' })) }
  assert.equal(layoutKey(relabelled, false, 2), base)
  assert.notEqual(layoutKey(s, true, 2), base)
  assert.notEqual(layoutKey(s, false, 1.5), base)
  const extra: GraphStructure = { ...s, nodes: [...s.nodes, { ...s.nodes[2], id: 'new_node' }] }
  assert.notEqual(layoutKey(extra, false, 2), base)
})

test('layout cache: survives a reload through localStorage and refuses a stale copy', () => {
  const store = new Map<string, string>()
  const fake = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() { return store.size },
    clear: () => store.clear(),
  }
  // Object.keys(localStorage) lista las claves: se emula con un Proxy
  const storage = new Proxy(fake, { ownKeys: () => [...store.keys()], getOwnPropertyDescriptor: (_, k) => (store.has(String(k)) ? { enumerable: true, configurable: true, value: store.get(String(k)) } : undefined) })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  try {
    const placed = new Map(s.nodes.map((n, i) => [n.id, { x: i * 10, y: 0, width: 100, height: 40 }]))
    rememberLayout(layoutKey(s, false, 3), placed)
    assert.equal(store.size, 1)
    // Otra estructura con la misma forma (otro objeto, otros textos): sale de la caché
    const again = cachedLayout({ ...s, nodes: s.nodes.map((n) => ({ ...n, title: 'otro' })) }, false, 3)
    assert.deepEqual(again && [...again], [...placed])
    // Una copia guardada a la que le falta un nodo no se usa (se recalcula)
    const key = [...store.keys()][0]
    store.set(key, JSON.stringify([...placed].slice(1)))
    const fresh = { ...s, nodes: s.nodes.map((n) => ({ ...n })) }
    assert.equal(cachedLayout({ ...fresh, edges: [...fresh.edges] }, true, 3), null)
    // Pocas formas guardadas: las viejas se podan
    for (let i = 0; i < 12; i++) rememberLayout(layoutKey(s, false, 10 + i), placed)
    assert.ok(store.size <= 8, `kept ${store.size}`)
  } finally {
    Reflect.deleteProperty(globalThis, 'localStorage')
  }
})
