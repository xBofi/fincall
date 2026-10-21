import { useState } from 'react'
import { Button, message } from 'antd'
import { ClockCircleOutlined, LogoutOutlined, StopOutlined } from '@ant-design/icons'
import { ApiError, auth, type UsuarioConsola } from '../api'
import Logo from './Logo'

const PASOS = [
  {
    titulo: 'Atiende al vecino',
    texto: 'Coge el teléfono a cualquier hora, en español natural, y deja que el vecino cuente la avería a su manera.',
  },
  {
    titulo: 'Entiende y ordena',
    texto: 'Identifica la finca y la vivienda, clasifica la incidencia y valora su urgencia sin hacer preguntas de más.',
  },
  {
    titulo: 'Avisa a quien toca',
    texto: 'Registra la incidencia y avisa al profesional asignado por el administrador. El vecino sabe qué pasará después.',
  },
]

/** Bienvenida para quien ha entrado con Google o Microsoft pero aún no tiene acceso concedido. */
export function AccesoPendiente({
  usuario,
  onCambio,
  onSalir,
}: {
  usuario: UsuarioConsola
  onCambio: (u: UsuarioConsola) => void
  onSalir: () => void
}) {
  const [enviando, setEnviando] = useState(false)

  const solicitar = async () => {
    setEnviando(true)
    try {
      onCambio(await auth.solicitar())
    } catch (e) {
      message.error(e instanceof ApiError ? e.detalle : 'No se ha podido enviar la solicitud.')
    } finally {
      setEnviando(false)
    }
  }

  const quien = usuario.nombre ?? usuario.email ?? usuario.nombre_usuario

  return (
    <main className="landing">
      <header className="landing-top">
        <Logo conNombre />
        <Button type="text" icon={<LogoutOutlined />} onClick={onSalir}>
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
        {PASOS.map((p, i) => (
          <li key={p.titulo}>
            <span className="landing-num">{String(i + 1).padStart(2, '0')}</span>
            <h2>{p.titulo}</h2>
            <p>{p.texto}</p>
          </li>
        ))}
      </ol>

      <section className={`landing-acceso is-${usuario.estado_acceso}`} aria-live="polite">
        {usuario.estado_acceso === 'sin_solicitar' && (
          <>
            <div>
              <h2>Hola, {quien}</h2>
              <p>La consola está en acceso privado. Solicita acceso y el equipo revisará tu petición.</p>
            </div>
            <Button type="primary" size="large" className="ops-primary" loading={enviando} onClick={() => void solicitar()}>
              Solicitar acceso
            </Button>
          </>
        )}
        {usuario.estado_acceso === 'solicitado' && (
          <div className="landing-estado">
            <ClockCircleOutlined />
            <div>
              <h2>Solicitud enviada</h2>
              <p>Te avisaremos cuando tengas acceso. Puedes cerrar esta página: al volver a entrar con {usuario.email ?? 'tu cuenta'} verás el estado.</p>
            </div>
          </div>
        )}
        {usuario.estado_acceso === 'denegado' && (
          <div className="landing-estado">
            <StopOutlined />
            <div>
              <h2>Acceso no concedido</h2>
              <p>De momento esta cuenta no tiene acceso a la consola. Si crees que es un error, vuelve a solicitarlo.</p>
              <Button style={{ marginTop: 12 }} loading={enviando} onClick={() => void solicitar()}>
                Volver a solicitar
              </Button>
            </div>
          </div>
        )}
      </section>
    </main>
  )
}
