// Vista de enrutado en un navegador real, sobre la demo autónoma (`/demo`: sin backend ni sesión).
// Necesita el build: `corepack pnpm run build && corepack pnpm run test:e2e`
// (la primera vez, `corepack pnpm exec playwright install chromium`).
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const PORT = Number(process.env.E2E_PORT ?? 4317)
const BASE = `http://127.0.0.1:${PORT}`
const root = fileURLToPath(new URL('..', import.meta.url))
let server
let browser

before(async () => {
  server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--port', String(PORT), '--strictPort', '--host', '127.0.0.1'], { cwd: root, stdio: 'ignore' })
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(BASE)).ok) break
    } catch { /* todavía arrancando */ }
    await new Promise((r) => setTimeout(r, 200))
  }
  browser = await chromium.launch()
})

after(async () => {
  await browser?.close()
  server?.kill()
})

async function openDemo(query = '') {
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(`${BASE}/demo${query}`)
  await page.waitForSelector('.react-flow__edge', { state: 'attached', timeout: 30000 })
  // La demo se reproduce sola: se lleva al último paso (y se detiene) para que nada se mueva
  await page.locator('.rv-timeline').press('End')
  await page.waitForTimeout(200)
  const ids = await page.$$eval('.react-flow__node', (ns) => ns.map((n) => n.getAttribute('data-id')))
  return { page, errors, ids }
}

const node = (page, id) => page.locator(`.react-flow__node[data-id="${id}"]`)

test('clicking through many nodes never blanks the view (Maximum update depth)', async () => {
  const { page, errors, ids } = await openDemo()
  assert.ok(ids.length > 10)
  // Antes: el segundo nodo seleccionado ya dejaba la página en blanco
  for (let round = 0; round < 2; round++) {
    for (const id of ids) {
      await node(page, id).click()
      await page.waitForTimeout(40)
    }
  }
  await page.waitForTimeout(300)
  assert.deepEqual(errors, [])
  assert.equal(await page.locator('.rv .react-flow').count(), 1, 'the canvas is still there')
  assert.equal(await page.locator('.rv-crash').count(), 0)
  await page.close()
})

test('Enter on a focused node selects it, like a click', async () => {
  const { page, errors } = await openDemo()
  await node(page, 'confirm_summary').focus()
  await page.keyboard.press('Enter')
  await page.waitForTimeout(150)
  assert.ok(await node(page, 'confirm_summary').locator('.rv-node.is-selected').count())
  assert.ok((await page.locator('.rv-edge-badge').count()) > 0)
  assert.deepEqual(errors, [])
  await page.close()
})

for (const query of ['', '?theme=dark&lang=ar']) {
  test(`selected node: numbered route badges on top of every box, none overlapping ${query}`, async () => {
    const { page, errors } = await openDemo(query)
    await node(page, 'next_step').click()
    await page.waitForTimeout(300)
    const rows = await page.locator('.rv-exit').count()
    const badges = page.locator('.rv-edge-badge')
    assert.ok(rows >= 10)
    assert.equal(await badges.count(), rows, 'one badge per route listed in the panel')
    const boxes = []
    for (let i = 0; i < rows; i++) {
      const n = badges.nth(i).locator('.rv-edge-badge-n')
      const b = await n.boundingBox()
      // Lo que se ve en el centro de la marca es la marca: ninguna caja ni otra etiqueta encima
      const onTop = await n.evaluate((el) => {
        const r = el.getBoundingClientRect()
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
        return el.contains(hit)
      })
      assert.ok(onTop, `badge ${i + 1} is hidden under something`)
      boxes.push(b)
    }
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const [a, b] = [boxes[i], boxes[j]]
        const overlap = a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
        assert.ok(!overlap, `badges ${i + 1} and ${j + 1} overlap`)
      }
    }
    // Pasar por una ruta del panel enseña su condición junto a la marca, legible y por encima
    await page.locator('.rv-exit').nth(2).hover()
    const text = page.locator('.rv-edge-badge.is-hovered .rv-edge-badge-text')
    await text.waitFor({ state: 'visible' })
    const condition = (await page.locator('.rv-exit').nth(2).locator('.rv-exit-condition').textContent())?.trim()
    assert.equal((await text.textContent())?.trim(), condition)
    assert.ok(await text.evaluate((el) => {
      const r = el.getBoundingClientRect()
      return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2))
    }), 'the hovered condition is readable (on top)')
    assert.deepEqual(errors, [])
    await page.close()
  })
}
