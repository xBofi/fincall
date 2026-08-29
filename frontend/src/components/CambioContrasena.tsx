import { useState } from 'react'
import { Alert, Form, Input, Modal } from 'antd'
import { ApiError, auth, type UsuarioConsola } from '../api'

interface Props {
  abierto: boolean
  /** Primer acceso o contraseña restablecida: no se puede cerrar sin cambiarla. */
  obligatorio: boolean
  onCerrar: () => void
  onCambiada: (usuario: UsuarioConsola) => void
}

export function CambioContrasena({ abierto, obligatorio, onCerrar, onCambiada }: Props) {
  const [form] = Form.useForm<{ actual: string; nueva: string; repetir: string }>()
  const [error, setError] = useState<string | null>(null)
  const [cargando, setCargando] = useState(false)

  const guardar = async () => {
    const v = await form.validateFields()
    setCargando(true)
    setError(null)
    try {
      onCambiada(await auth.cambiarContrasena(v.actual, v.nueva))
      form.resetFields()
    } catch (e) {
      setError(e instanceof ApiError ? e.detalle : 'No se ha podido cambiar la contraseña.')
    } finally {
      setCargando(false)
    }
  }

  return (
    <Modal
      open={abierto}
      title={obligatorio ? 'Elige una contraseña nueva' : 'Cambiar contraseña'}
      okText="Guardar"
      cancelText="Cancelar"
      onOk={() => void guardar()}
      onCancel={obligatorio ? undefined : onCerrar}
      closable={!obligatorio}
      maskClosable={!obligatorio}
      keyboard={!obligatorio}
      cancelButtonProps={obligatorio ? { style: { display: 'none' } } : undefined}
      confirmLoading={cargando}
      destroyOnHidden
    >
      {obligatorio && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="Tu contraseña es temporal. Cámbiala para poder usar la consola."
        />
      )}
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical" requiredMark={false} autoComplete="off">
        <Form.Item name="actual" label="Contraseña actual" rules={[{ required: true, message: 'Obligatoria' }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="nueva"
          label="Contraseña nueva (mínimo 10 caracteres)"
          rules={[{ required: true, message: 'Obligatoria' }, { min: 10, message: 'Al menos 10 caracteres' }]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="repetir"
          label="Repite la contraseña nueva"
          dependencies={['nueva']}
          rules={[
            { required: true, message: 'Obligatoria' },
            ({ getFieldValue }) => ({
              validator: (_, valor) =>
                valor === getFieldValue('nueva') ? Promise.resolve() : Promise.reject(new Error('No coincide')),
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
