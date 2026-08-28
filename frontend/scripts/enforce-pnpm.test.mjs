import assert from 'node:assert/strict'
import test from 'node:test'

import { assertPnpm } from './enforce-pnpm.mjs'

test('allows pnpm user agents', () => {
  assert.doesNotThrow(() => assertPnpm('pnpm/11.24.0 npm/? node/v22'))
})

test('rejects npm user agents', () => {
  assert.throws(() => assertPnpm('npm/11.0.0 node/v22'), /pnpm/)
})
