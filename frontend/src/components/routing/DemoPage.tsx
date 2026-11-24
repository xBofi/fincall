import { useEffect, useState } from 'react'
import { ConfigProvider, Segmented, theme as antdTheme } from 'antd'
import { MoonOutlined, SunOutlined } from '@ant-design/icons'
import Logo from '../Logo'
import { RoutingView } from './RoutingView'
import { t } from './i18n'

type Theme = 'light' | 'dark'

const initialTheme = (): Theme => {
  const asked = new URLSearchParams(location.search).get('theme')
  if (asked === 'dark' || asked === 'light') return asked
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

const initialLang = () => new URLSearchParams(location.search).get('lang') ?? document.documentElement.lang ?? 'es'

/**
 * Demo autónoma de la vista de enrutado: `/demo` (o `?demo`). No necesita sesión, backend ni
 * telefonía: reproduce una llamada grabada a través del grafo real (`evals/record_demo_call.py`).
 * Pensada para enseñarla en una entrevista o grabar un vídeo.
 */
export default function DemoPage() {
  const [mode, setMode] = useState<Theme>(initialTheme)
  const [lang, setLang] = useState(initialLang)
  useEffect(() => {
    document.documentElement.dataset.theme = mode
    document.documentElement.style.colorScheme = mode
  }, [mode])
  useEffect(() => {
    document.documentElement.lang = lang
    document.documentElement.dir = ['ar', 'ur'].includes(lang) ? 'rtl' : 'ltr'
  }, [lang])

  return (
    <ConfigProvider
      direction={['ar', 'ur'].includes(lang) ? 'rtl' : 'ltr'}
      theme={{ algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm }}
    >
      <div className={`rv-demo-page is-${mode}`}>
        <header className="rv-demo-bar">
          <Logo withName />
          <span className="rv-demo-tag">{t('routing.mode.demo')}</span>
          <div className="rv-demo-tools">
            <Segmented
              size="small"
              aria-label="Language"
              value={lang.slice(0, 2)}
              onChange={(v) => setLang(String(v))}
              options={[{ label: 'ES', value: 'es' }, { label: 'EN', value: 'en' }]}
            />
            <Segmented
              size="small"
              aria-label="Theme"
              value={mode}
              onChange={(v) => setMode(v as Theme)}
              options={[
                { value: 'light', icon: <SunOutlined />, title: 'Light' },
                { value: 'dark', icon: <MoonOutlined />, title: 'Dark' },
              ]}
            />
          </div>
        </header>
        <main className="rv-demo-main" key={lang}>
          <RoutingView structure={null} modes={['demo']} initialMode="demo" />
        </main>
      </div>
    </ConfigProvider>
  )
}
