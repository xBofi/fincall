/**
 * Console i18n: six languages, right-to-left layout for Arabic and Urdu.
 *
 * - `t(key, vars)` works in components and in plain helpers (`texts.ts`): it reads the current
 *   language from this module, which `I18nProvider` keeps in sync. Components re-render on a
 *   change because `App` consumes the context and the tree under it re-renders with it; an
 *   ongoing call is not remounted.
 * - The console language is the reader's preference (saved in this browser); the call's
 *   language is chosen per call in the simulator and may differ (a Spanish-speaking operator
 *   testing an Arabic call).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ConfigProvider } from 'antd'
import type { Locale } from 'antd/es/locale'
import arEG from 'antd/locale/ar_EG'
import caES from 'antd/locale/ca_ES'
import enUS from 'antd/locale/en_US'
import esES from 'antd/locale/es_ES'
import hiIN from 'antd/locale/hi_IN'
import urPK from 'antd/locale/ur_PK'
import { ar } from './messages/ar'
import { ca } from './messages/ca'
import { en } from './messages/en'
import { es, type MessageKey, type Messages } from './messages/es'
import { hi } from './messages/hi'
import { ur } from './messages/ur'

export type UiLanguage = 'es' | 'en' | 'ca' | 'ar' | 'hi' | 'ur'

interface LanguageInfo {
  code: UiLanguage
  native: string
  rtl: boolean
  antd: Locale
  messages: Messages
}

export const UI_LANGUAGES: LanguageInfo[] = [
  { code: 'es', native: 'Español', rtl: false, antd: esES, messages: es },
  { code: 'en', native: 'English', rtl: false, antd: enUS, messages: en },
  { code: 'ca', native: 'Català', rtl: false, antd: caES, messages: ca },
  { code: 'ar', native: 'العربية', rtl: true, antd: arEG, messages: ar },
  { code: 'hi', native: 'हिन्दी', rtl: false, antd: hiIN, messages: hi },
  { code: 'ur', native: 'اردو', rtl: true, antd: urPK, messages: ur },
]

const BY_CODE = Object.fromEntries(UI_LANGUAGES.map((l) => [l.code, l])) as Record<UiLanguage, LanguageInfo>
const STORAGE_KEY = 'fincall.ui.language'

let current: UiLanguage = 'es'

/** Translates `key` in the console language; `{name}` placeholders come from `vars`. A key a
 * catalog lacks falls back to Spanish, the source language (the types make that rare). */
export function t(key: MessageKey, vars?: Record<string, string | number>): string {
  const text = BY_CODE[current].messages[key] ?? es[key] ?? key
  return vars ? text.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m)) : text
}

export function isUiLanguage(code: string | null | undefined): code is UiLanguage {
  return !!code && code in BY_CODE
}

function initialLanguage(): UiLanguage {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY)
    if (isUiLanguage(saved)) return saved
  } catch {
    /* private mode or blocked storage: fall back to the browser's language */
  }
  const browser = (navigator.language || 'es').slice(0, 2).toLowerCase()
  return isUiLanguage(browser) ? browser : 'es'
}

interface I18nValue {
  lang: UiLanguage
  dir: 'ltr' | 'rtl'
  setLang: (code: UiLanguage) => void
  t: typeof t
}

const I18nContext = createContext<I18nValue | null>(null)

export function I18nProvider({ children, theme }: { children: ReactNode; theme?: Parameters<typeof ConfigProvider>[0]['theme'] }) {
  const [lang, setLangState] = useState<UiLanguage>(() => {
    current = initialLanguage()
    return current
  })
  const info = BY_CODE[lang]
  const dir = info.rtl ? 'rtl' : 'ltr'

  const setLang = useCallback((code: UiLanguage) => {
    current = code
    try {
      window.localStorage.setItem(STORAGE_KEY, code)
    } catch {
      /* the choice just won't survive a reload */
    }
    setLangState(code)
  }, [])

  useEffect(() => {
    document.documentElement.lang = lang
    document.documentElement.dir = dir
  }, [lang, dir])

  const value = useMemo<I18nValue>(() => ({ lang, dir, setLang, t }), [lang, dir, setLang])
  return (
    <I18nContext.Provider value={value}>
      <ConfigProvider locale={info.antd} direction={dir} theme={theme}>
        {children}
      </ConfigProvider>
    </I18nContext.Provider>
  )
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n outside <I18nProvider>')
  return value
}
