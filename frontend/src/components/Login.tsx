import { useEffect, useState } from 'react'
import { Alert, Button, Spin } from 'antd'
import { auth, type AccessProvider } from '../api'
import Logo from './Logo'

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.6 13.3l7.9 6.2C12.4 13.7 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.2 5.6c4.2-3.9 7.1-9.6 7.1-17z" />
      <path fill="#FBBC05" d="M10.5 28.6A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.1.8-4.5l-7.9-6.2A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l7.9-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.2-5.6c-2.2 1.5-5 2.4-8.7 2.4-6.3 0-11.6-4.2-13.5-10l-7.9 6.1C6.6 42.6 14.6 48 24 48z" />
    </svg>
  )
}

function MicrosoftIcon() {
  return (
    <svg viewBox="0 0 23 23" width="18" height="18" aria-hidden="true">
      <path fill="#f35325" d="M1 1h10v10H1z" />
      <path fill="#81bc06" d="M12 1h10v10H12z" />
      <path fill="#05a6f0" d="M1 12h10v10H1z" />
      <path fill="#ffba08" d="M12 12h10v10H12z" />
    </svg>
  )
}

const ICONS: Record<string, () => React.JSX.Element> = { google: GoogleIcon, microsoft: MicrosoftIcon }

/** Entrada a la consola: solo con Google o Microsoft (sin contraseñas). */
export function Login() {
  const [providers, setProviders] = useState<AccessProvider[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const failure = new URLSearchParams(window.location.search).get('access') === 'error'

  useEffect(() => {
    auth
      .providers()
      .then(setProviders)
      .catch(() => setError('No se ha podido contactar con el servidor.'))
  }, [])

  return (
    <main className="ops-login">
      <section className="ops-login-card" aria-labelledby="titulo-acceso">
        <div className="ops-brand">
          <Logo withName />
          <div className="ops-brand-producto">
            <h1 id="titulo-acceso">Centro operativo</h1>
          </div>
        </div>
        <p className="ops-login-note">Entra con tu cuenta. Cada acceso queda registrado.</p>
        {failure && (
          <Alert type="error" showIcon message="No se ha podido completar el inicio de sesión. Vuelve a intentarlo." style={{ marginBottom: 16 }} />
        )}
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        {providers === null && !error && <Spin className="ops-cargando" />}
        {providers?.length === 0 && (
          <Alert type="warning" showIcon message="El inicio de sesión no está configurado en el servidor." />
        )}
        <div className="ops-login-proveedores">
          {providers?.map((p) => {
            const Icon = ICONS[p.id]
            return (
              <Button key={p.id} size="large" block icon={Icon ? <Icon /> : undefined} onClick={() => auth.signInWith(p.id)}>
                Continuar con {p.label}
              </Button>
            )
          })}
        </div>
      </section>
    </main>
  )
}
