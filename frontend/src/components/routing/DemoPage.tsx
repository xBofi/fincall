import { useEffect, useState } from 'react'
import { ConfigProvider, Segmented, theme as antdTheme } from 'antd'
import { MoonOutlined, SunOutlined } from '@ant-design/icons'
import Logo from '../Logo'
import { RoutingView } from './RoutingView'
import { isUiLanguage, t, UI_LANGUAGES, useI18n } from '../../i18n'

type Theme = 'light' | 'dark'

const initialTheme = (): Theme => {
  const asked = new URLSearchParams(location.search).get('theme')
  if (asked === 'dark' || asked === 'light') return asked
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/**
 * Demo autónoma de la vista de enrutado: `/demo` (o `?demo`). No necesita sesión, backend ni
 * telefonía: reproduce una llamada grabada a través del grafo real (`evals/record_demo_call.py`).
 * Pensada para enseñarla en una entrevista o grabar un vídeo. `?theme=dark`, `?lang=ar`.
 */
export default function DemoPage() {
  const { lang, setLang } = useI18n()
  const [mode, setMode] = useState<Theme>(initialTheme)
  useEffect(() => {
    const asked = new URLSearchParams(location.search).get('lang')
    if (isUiLanguage(asked)) setLang(asked)
  }, [setLang])
  useEffect(() => {
    document.documentElement.dataset.theme = mode
    document.documentElement.style.colorScheme = mode
  }, [mode])

  return (
    // Hereda idioma y dirección del I18nProvider; aquí solo cambia el algoritmo de color
    <ConfigProvider theme={{ algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm }}>
      <div className={`rv-demo-page is-${mode}`}>
        <header className="rv-demo-bar">
          <Logo withName />
          <span className="rv-demo-tag">{t('routing.mode.demo')}</span>
          <div className="rv-demo-tools">
            <Segmented
              size="small"
              aria-label={t('routing.demo.language')}
              value={lang}
              onChange={(v) => { if (isUiLanguage(String(v))) setLang(String(v) as typeof lang) }}
              options={UI_LANGUAGES.map((l) => ({ label: l.native, value: l.code }))}
            />
            <Segmented
              size="small"
              aria-label={t('routing.demo.theme')}
              value={mode}
              onChange={(v) => setMode(v as Theme)}
              options={[
                { value: 'light', icon: <SunOutlined />, title: t('routing.demo.light') },
                { value: 'dark', icon: <MoonOutlined />, title: t('routing.demo.dark') },
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
