import { useState } from 'react'
import { Button, message } from 'antd'
import { ClockCircleOutlined, LogoutOutlined, StopOutlined } from '@ant-design/icons'
import { ApiError, auth, type ConsoleUser } from '../api'
import Logo from './Logo'

const STEPS = [
  {
    title: 'Atiende al vecino',
    text: 'Coge el teléfono a cualquier hora, en español natural, y deja que el vecino cuente la avería a su manera.',
  },
  {
    title: 'Entiende y ordena',
    text: 'Identifica la finca y la vivienda, clasifica la incidencia y valora su urgencia sin hacer preguntas de más.',
  },
  {
    title: 'Avisa a quien toca',
    text: 'Registra la incidencia y avisa al profesional asignado por el administrador. El vecino sabe qué pasará después.',
  },
]

/** Bienvenida para quien ha entrado con Google o Microsoft pero aún no tiene acceso concedido. */
export function PendingAccess({
  user,
  onUpdate,
  onExit,
}: {
  user: ConsoleUser
  onUpdate: (u: ConsoleUser) => void
  onExit: () => void
}) {
  const [sending, setSending] = useState(false)

  const submit_request = async () => {
    setSending(true)
    try {
      onUpdate(await auth.submit_request())
    } catch (e) {
      message.error(e instanceof ApiError ? e.detail : 'No se ha podido enviar la solicitud.')
    } finally {
      setSending(false)
    }
  }

  const who = user.name ?? user.email ?? user.username

  return (
    <main className="landing">
      <header className="landing-top">
        <Logo withName />
        <Button type="text" icon={<LogoutOutlined />} onClick={onExit}>
          Salir
        </Button>
      </header>

      <section className="landing-hero" aria-labelledby="landing-titulo">
        <p className="ops-eyebrow">Asistente de incidencias para comunidades</p>
        <h1 id="landing-titulo">
          Las averías de la comunidad,
          <br />
          atendidas al primer tono.
        </h1>
        <p className="landing-intro">
          Fincall es un asistente de voz con inteligencia artificial que contesta las llamadas de los vecinos, entiende qué
          ha pasado y lo deja todo listo para el administrador de fincas y sus profesionales.
        </p>
      </section>

      <ol className="landing-pasos">
        {STEPS.map((p, i) => (
          <li key={p.title}>
            <span className="landing-num">{String(i + 1).padStart(2, '0')}</span>
            <h2>{p.title}</h2>
            <p>{p.text}</p>
          </li>
        ))}
      </ol>

      <section className={`landing-acceso is-${user.access_state}`} aria-live="polite">
        {user.access_state === 'not_requested' && (
          <>
            <div>
              <h2>Hola, {who}</h2>
              <p>La consola está en acceso privado. Solicita acceso y el equipo revisará tu petición.</p>
            </div>
            <Button type="primary" size="large" className="ops-primary" loading={sending} onClick={() => void submit_request()}>
              Solicitar acceso
            </Button>
          </>
        )}
        {user.access_state === 'requested' && (
          <div className="landing-estado">
            <ClockCircleOutlined />
            <div>
              <h2>Solicitud enviada</h2>
              <p>Te avisaremos cuando tengas acceso. Puedes cerrar esta página: al volver a entrar con {user.email ?? 'tu cuenta'} verás el estado.</p>
            </div>
          </div>
        )}
        {user.access_state === 'denied' && (
          <div className="landing-estado">
            <StopOutlined />
            <div>
              <h2>Acceso no concedido</h2>
              <p>De momento esta cuenta no tiene acceso a la consola. Si crees que es un error, vuelve a solicitarlo.</p>
              <Button style={{ marginTop: 12 }} loading={sending} onClick={() => void submit_request()}>
                Volver a solicitar
              </Button>
            </div>
          </div>
        )}
      </section>
    </main>
  )
}
