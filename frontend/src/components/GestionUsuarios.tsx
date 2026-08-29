import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import { KeyOutlined, PlusOutlined, UserDeleteOutlined } from '@ant-design/icons'
import { ApiError, usuarios as apiUsuarios, type Rol, type UsuarioConsola } from '../api'

const ROLES: { value: Rol; label: string; ayuda: string }[] = [
  { value: 'owner', label: 'Owner', ayuda: 'Todo, incluida la gestión de usuarios' },
  { value: 'administrador', label: 'Administrador', ayuda: 'Expedientes, grabaciones y auditoría' },
  { value: 'calidad', label: 'Calidad', ayuda: 'Expedientes y grabaciones' },
]

const COLOR_ROL: Record<Rol, string> = { owner: 'geekblue', administrador: 'cyan', calidad: 'green' }

function detalle(e: unknown): string {
  return e instanceof ApiError ? e.detalle : String(e)
}

/** Alta, rol, activación, restablecimiento y baja de usuarios. Solo owner. */
export function GestionUsuarios({ yo }: { yo: UsuarioConsola }) {
  const [filas, setFilas] = useState<UsuarioConsola[]>([])
  const [cargando, setCargando] = useState(true)
  const [altaAbierta, setAltaAbierta] = useState(false)
  const [temporal, setTemporal] = useState<{ usuario: string; contrasena: string } | null>(null)
  const [formAlta] = Form.useForm<{ nombre_usuario: string; nombre?: string; rol: Rol }>()

  const cargar = useCallback(async () => {
    try {
      setFilas(await apiUsuarios.listar())
    } catch (e) {
      message.error(detalle(e))
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => {
    void cargar()
  }, [cargar])

  const accion = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn()
      if (ok) message.success(ok)
      await cargar()
    } catch (e) {
      message.error(detalle(e))
    }
  }

  const crear = async () => {
    const v = await formAlta.validateFields()
    await accion(async () => {
      const creado = await apiUsuarios.crear(v)
      setAltaAbierta(false)
      formAlta.resetFields()
      if (creado.contrasena_temporal) setTemporal({ usuario: creado.nombre_usuario, contrasena: creado.contrasena_temporal })
    })
  }

  const restablecer = (u: UsuarioConsola) =>
    accion(async () => {
      const r = await apiUsuarios.restablecer(u.id)
      setTemporal({ usuario: r.nombre_usuario, contrasena: r.contrasena_temporal })
    })

  return (
    <section className="ops-users" aria-label="Gestión de usuarios">
      <div className="ops-panel-heading">
        <div>
          <p className="ops-eyebrow">Acceso a la consola</p>
          <h2>Usuarios</h2>
        </div>
        <Button type="primary" className="ops-primary" icon={<PlusOutlined />} onClick={() => setAltaAbierta(true)}>
          Nuevo usuario
        </Button>
      </div>
      <p className="ops-queue-note" style={{ marginTop: 8 }}>
        Las altas y los restablecimientos generan una contraseña temporal que se muestra una sola vez: entrégala en mano.
        El usuario deberá cambiarla en su primer acceso. Desactivar o eliminar cierra sus sesiones al instante.
      </p>

      <Table<UsuarioConsola>
        rowKey="id"
        size="small"
        loading={cargando}
        dataSource={filas}
        pagination={false}
        style={{ marginTop: 16 }}
        columns={[
          {
            title: 'Usuario',
            dataIndex: 'nombre_usuario',
            render: (v: string, u) => (
              <span>
                <b>{v}</b>
                {u.id === yo.id && <Tag style={{ marginLeft: 8 }}>tú</Tag>}
                {u.nombre && <div><Typography.Text type="secondary">{u.nombre}</Typography.Text></div>}
              </span>
            ),
          },
          {
            title: 'Rol',
            dataIndex: 'rol',
            width: 190,
            render: (rol: Rol, u) =>
              u.id === yo.id ? (
                <Tag color={COLOR_ROL[rol]}>{rol}</Tag>
              ) : (
                <Select<Rol>
                  size="small"
                  value={rol}
                  style={{ width: 170 }}
                  options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
                  onChange={(nuevo) => void accion(() => apiUsuarios.modificar(u.id, { rol: nuevo }), 'Rol actualizado')}
                />
              ),
          },
          {
            title: 'Activo',
            dataIndex: 'activo',
            width: 90,
            render: (activo: boolean, u) => (
              <Switch
                size="small"
                checked={activo}
                disabled={u.id === yo.id}
                onChange={(v) => void accion(() => apiUsuarios.modificar(u.id, { activo: v }), v ? 'Usuario activado' : 'Usuario desactivado')}
              />
            ),
          },
          {
            title: 'Estado',
            width: 200,
            render: (_, u) => (u.debe_cambiar_contrasena ? <Tag color="gold">contraseña temporal</Tag> : <Tag color="green">operativo</Tag>),
          },
          {
            title: '',
            width: 240,
            render: (_, u) => (
              <Space>
                <Popconfirm
                  title={`Restablecer la contraseña de ${u.nombre_usuario}`}
                  description="Se generará una temporal y se cerrarán sus sesiones."
                  okText="Restablecer"
                  cancelText="Cancelar"
                  onConfirm={() => void restablecer(u)}
                >
                  <Button size="small" icon={<KeyOutlined />}>Restablecer</Button>
                </Popconfirm>
                <Popconfirm
                  title={`Eliminar a ${u.nombre_usuario}`}
                  description="La baja es definitiva. Su rastro en la auditoría se conserva."
                  okText="Eliminar"
                  okButtonProps={{ danger: true }}
                  cancelText="Cancelar"
                  disabled={u.id === yo.id}
                  onConfirm={() => void accion(() => apiUsuarios.eliminar(u.id), 'Usuario eliminado')}
                >
                  <Button size="small" danger icon={<UserDeleteOutlined />} disabled={u.id === yo.id}>Eliminar</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        open={altaAbierta}
        title="Nuevo usuario"
        okText="Crear"
        cancelText="Cancelar"
        onOk={() => void crear()}
        onCancel={() => setAltaAbierta(false)}
        destroyOnHidden
      >
        <Form form={formAlta} layout="vertical" requiredMark={false} initialValues={{ rol: 'calidad' }}>
          <Form.Item
            name="nombre_usuario"
            label="Nombre de usuario"
            normalize={(v: string) => v?.toLowerCase()}
            rules={[
              { required: true, message: 'Obligatorio' },
              { pattern: /^[a-z0-9][a-z0-9._-]{1,63}$/, message: 'Minúsculas, números, punto, guion o guion bajo' },
            ]}
          >
            <Input autoComplete="off" placeholder="p. ej. marta.garcia" />
          </Form.Item>
          <Form.Item name="nombre" label="Nombre para mostrar (opcional)">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="rol" label="Rol" rules={[{ required: true }]}>
            <Select options={ROLES.map((r) => ({ value: r.value, label: `${r.label} · ${r.ayuda}` }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={temporal !== null}
        title="Contraseña temporal"
        okText="Ya la he anotado"
        cancelButtonProps={{ style: { display: 'none' } }}
        onOk={() => setTemporal(null)}
        onCancel={() => setTemporal(null)}
      >
        {temporal && (
          <>
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="Solo se muestra una vez. Entrégala al usuario por un canal seguro; deberá cambiarla al entrar."
            />
            <p>Usuario: <b>{temporal.usuario}</b></p>
            <Typography.Paragraph copyable className="ops-mono" style={{ fontSize: '1.1rem' }}>
              {temporal.contrasena}
            </Typography.Paragraph>
          </>
        )}
      </Modal>
    </section>
  )
}
