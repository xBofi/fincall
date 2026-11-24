import { StrictMode, Suspense, lazy, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { I18nProvider } from './i18n'
import App from './App'
import './index.css'

/** Demo autónoma de la vista de enrutado: sin sesión, backend ni telefonía. */
const DemoPage = lazy(() => import('./components/routing/DemoPage'))
const isDemo = location.pathname.replace(/\/$/, '').endsWith('/demo') || new URLSearchParams(location.search).has('demo')

/** Mismos escalones que `html { font-size }` en index.css: Ant Design mide en px, no en rem. */
const SIZE_STEPS: [string, { fontSize: number; controlHeight: number }][] = [
  ['(min-width: 2200px)', { fontSize: 16, controlHeight: 38 }],
  ['(min-width: 1700px)', { fontSize: 15, controlHeight: 35 }],
]

function scaleStep() {
  return SIZE_STEPS.find(([q]) => window.matchMedia(q).matches)?.[1] ?? { fontSize: 14, controlHeight: 32 }
}

function Root() {
  const [token, setToken] = useState(scaleStep)
  useEffect(() => {
    const queries = SIZE_STEPS.map(([q]) => window.matchMedia(q))
    const update = () => setToken(scaleStep())
    queries.forEach((c) => c.addEventListener('change', update))
    return () => queries.forEach((c) => c.removeEventListener('change', update))
  }, [])
  return (
    <I18nProvider theme={{ token }}>
      {isDemo ? <Suspense fallback={null}><DemoPage /></Suspense> : <App />}
    </I18nProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
