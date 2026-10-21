import { useCallback, useEffect, useState } from 'react'
import { Button, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import { UserDeleteOutlined } from '@ant-design/icons'
import { usuarios as apiUsuarios, type EstadoAccesoUsuario, type Rol, type UsuarioConsola } from '../api'
import { PROVEEDOR, ROLES, detalle } from './rolesUsuario'

const COLOR_ROL: Record<Rol, string> = { owner: 'geekblue', administrador: 'cyan', calidad: 'green' }

const ESTADO: Record<EstadoAccesoUsuario, { texto: string; color: string }> = {
  concedido: { texto: 'con acceso', color: 'green' },
  solicitado: { texto: 'solicita acceso', color: 'gold' },
  sin_solicitar: { texto: 'no ha solicitado', color: 'default' },
  denegado: { texto: 'denegado', color: 'red' },
}

/** Quién puede entrar en la consola: rol, activación y baja. Solo owner. */
export function GestionUsuarios({ yo }: { yo: UsuarioConsola }) {
  const [filas, setFilas] = useState<UsuarioConsola[]>([])
  const [cargando, setCargando] = useState(true)

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

  return (
    <section className="ops-users" aria-label="Gestión de usuarios">
      <div className="ops-panel-heading">
        <div>
          <p className="ops-eyebrow">Acceso a la consola</p>
          <h2>Usuarios</h2>
        </div>
      </div>
      <p className="ops-queue-note" style={{ marginTop: 8 }}>
        Se entra con Google o Microsoft. Quien entra por primera vez aparece aquí y en Solicitudes; desactivar o eliminar
        cierra sus sesiones al instante.
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
            title: 'Persona',
            dataIndex: 'nombre_usuario',
            render: (v: string, u) => (
              <span>
                <b>{u.nombre ?? v}</b>
                {u.id === yo.id && <Tag style={{ marginLeft: 8 }}>tú</Tag>}
                <div>
                  <Typography.Text type="secondary">
                    {u.email ?? v}
                    {u.proveedor ? ` · ${PROVEEDOR[u.proveedor] ?? u.proveedor}` : ' · cuenta antigua, no puede entrar'}
                  </Typography.Text>
                </div>
              </span>
            ),
          },
          {
            title: 'Acceso',
            dataIndex: 'estado_acceso',
            width: 150,
            render: (estado: EstadoAccesoUsuario) => <Tag color={ESTADO[estado].color}>{ESTADO[estado].texto}</Tag>,
          },
          {
            title: 'Rol',
            dataIndex: 'rol',
            width: 190,
            render: (rol: Rol, u) =>
              u.id === yo.id || u.estado_acceso !== 'concedido' ? (
                <Tag color={COLOR_ROL[rol]}>{ROLES.find((r) => r.value === rol)?.label ?? rol}</Tag>
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
            title: '',
            width: 130,
            render: (_, u) => (
              <Space>
                <Popconfirm
                  title={`Eliminar a ${u.nombre ?? u.nombre_usuario}`}
                  description="Si vuelve a entrar con su cuenta, aparecerá como una persona nueva."
                  okText="Eliminar"
                  okButtonProps={{ danger: true }}
                  cancelText="Cancelar"
                  disabled={u.id === yo.id}
                  onConfirm={() => void accion(() => apiUsuarios.eliminar(u.id), 'Usuario eliminado')}
                >
                  <Button size="small" danger icon={<UserDeleteOutlined />} disabled={u.id === yo.id}>
                    Eliminar
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
    </section>
  )
}
