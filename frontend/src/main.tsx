import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import esES from 'antd/locale/es_ES'
import App from './App'
import './index.css'

/** Mismos escalones que `html { font-size }` en index.css: Ant Design mide en px, no en rem. */
const ESCALONES: [string, { fontSize: number; controlHeight: number }][] = [
  ['(min-width: 2200px)', { fontSize: 16, controlHeight: 38 }],
  ['(min-width: 1700px)', { fontSize: 15, controlHeight: 35 }],
]

function escalon() {
  return ESCALONES.find(([q]) => window.matchMedia(q).matches)?.[1] ?? { fontSize: 14, controlHeight: 32 }
}

function Raiz() {
  const [token, setToken] = useState(escalon)
  useEffect(() => {
    const consultas = ESCALONES.map(([q]) => window.matchMedia(q))
    const actualizar = () => setToken(escalon())
    consultas.forEach((c) => c.addEventListener('change', actualizar))
    return () => consultas.forEach((c) => c.removeEventListener('change', actualizar))
  }, [])
  return (
    <ConfigProvider locale={esES} theme={{ token }}>
      <App />
    </ConfigProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Raiz />
  </StrictMode>,
)
