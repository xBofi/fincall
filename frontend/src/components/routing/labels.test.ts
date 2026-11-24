// Los textos de la vista de enrutado existen en los seis idiomas. `corepack pnpm run test`
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { es } from '../../i18n/messages/es.ts'
import { en } from '../../i18n/messages/en.ts'
import { ca } from '../../i18n/messages/ca.ts'
import { ar } from '../../i18n/messages/ar.ts'
import { hi } from '../../i18n/messages/hi.ts'
import { ur } from '../../i18n/messages/ur.ts'
import { edgeConditionKey, nodeTitleKey } from './labelKeys.ts'
import type { DemoRecording } from './types.ts'

const CATALOGS: Record<string, Record<string, string>> = { es, en, ca, ar, hi, ur }
const OURS = Object.keys(es).filter((k) => k.startsWith('routing.') || k.startsWith('flow.'))
const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
const SCRIPT: Record<string, RegExp> = { ar: /[؀-ۿ]/, ur: /[؀-ۿ]/, hi: /[ऀ-ॿ]/ }

const demo = JSON.parse(
  readFileSync(new URL('./demo/demoCall.json', import.meta.url), 'utf-8'),
) as DemoRecording

for (const [lang, messages] of Object.entries(CATALOGS)) {
  test(`${lang}: every routing and flow key, with the same placeholders`, () => {
    for (const key of OURS) {
      const text = messages[key]
      assert.ok(text && text.trim(), `${lang} lacks ${key}`)
      assert.deepEqual(placeholders(text), placeholders(es[key as keyof typeof es]), `${lang} ${key}`)
    }
  })

  test(`${lang}: every node and route of the graph has a translated label`, () => {
    for (const n of demo.structure.nodes) assert.ok(messages[nodeTitleKey(n.id)], `${lang} node ${n.id}`)
    for (const e of demo.structure.edges) assert.ok(messages[edgeConditionKey(e)], `${lang} edge ${e.id}`)
  })

  if (SCRIPT[lang]) {
    test(`${lang}: the texts are in its own script, not copied from Spanish`, () => {
      const prose = OURS.filter((k) => /[a-záéíóúñ]{4}/i.test(es[k as keyof typeof es].replace(/\{\w+\}/g, '')))
      const foreign = prose.filter((k) => !SCRIPT[lang].test(messages[k]))
      assert.deepEqual(foreign, [])
    })
  }
}

test('the Spanish labels match what the backend exports, so nothing is lost in translation', () => {
  for (const n of demo.structure.nodes) assert.equal(es[nodeTitleKey(n.id) as keyof typeof es], n.title)
  for (const e of demo.structure.edges) assert.equal(es[edgeConditionKey(e) as keyof typeof es], e.condition)
})
