import { useState } from 'react'
import { Alert, Button, Form, Input } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { ApiError, auth, type UsuarioConsola } from '../api'

/** Pantalla de acceso a la consola privada: usuario y contraseña internos. */
export function Login({ onEntrar }: { onEntrar: (usuario: UsuarioConsola) => void }) {
  const [error, setError] = useState<string | null>(null)
  const [cargando, setCargando] = useState(false)

  const entrar = async (valores: { usuario: string; contrasena: string }) => {
    setCargando(true)
    setError(null)
    try {
      onEntrar(await auth.login(valores.usuario.trim(), valores.contrasena))
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) setError('Demasiados intentos fallidos. Espera unos minutos.')
      else if (e instanceof ApiError && e.status === 401) setError('Usuario o contraseña incorrectos.')
      else setError('No se ha podido contactar con el servidor.')
    } finally {
      setCargando(false)
    }
  }

  return (
    <main className="ops-login">
      <section className="ops-login-card" aria-labelledby="titulo-acceso">
        <div className="ops-brand">
          <span className="ops-brand-mark" aria-hidden="true">B</span>
          <div>
            <p className="ops-eyebrow">Building Call Assistant</p>
            <h1 id="titulo-acceso">Centro operativo</h1>
          </div>
        </div>
        <p className="ops-login-note">Acceso restringido al personal autorizado. Cada acceso queda registrado.</p>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={entrar} requiredMark={false} autoComplete="on">
          <Form.Item name="usuario" label="Usuario" rules={[{ required: true, message: 'Indica tu usuario' }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus size="large" />
          </Form.Item>
          <Form.Item name="contrasena" label="Contraseña" rules={[{ required: true, message: 'Indica tu contraseña' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={cargando} className="ops-primary">
            Entrar
          </Button>
        </Form>
      </section>
    </main>
  )
}
