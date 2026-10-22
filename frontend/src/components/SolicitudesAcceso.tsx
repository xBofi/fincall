import { useCallback, useEffect, useState } from 'react'
import { Button, Empty, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import { usuarios as apiUsuarios, type Rol, type UsuarioConsola } from '../api'
import { PROVEEDOR, ROLES, detalle } from './rolesUsuario'

function fecha(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' }) : '—'
}

/** Personas que han pedido acceso: el owner lo concede con un rol o lo deniega. */
export function SolicitudesAcceso({ onCambio }: { onCambio: () => void }) {
  const [filas, setFilas] = useState<UsuarioConsola[]>([])
  const [cargando, setCargando] = useState(true)
  const [roles, setRoles] = useState<Record<string, Rol>>({})

  const cargar = useCallback(async () => {
    try {
      const todos = await apiUsuarios.listar()
      setFilas(todos.filter((u) => u.estado_acceso === 'solicitado'))
    } catch (e) {
      message.error(detalle(e))
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => {
    void cargar()
  }, [cargar])

  const accion = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn()
      message.success(ok)
      await cargar()
      onCambio()
    } catch (e) {
      message.error(detalle(e))
    }
  }

  return (
    <section className="ops-users" aria-label="Solicitudes de acceso">
      <div className="ops-panel-heading">
        <div>
          <p className="ops-eyebrow">Acceso a la consola</p>
          <h2>Solicitudes</h2>
        </div>
      </div>
      <p className="ops-queue-note" style={{ marginTop: 8 }}>
        Personas que han entrado con Google o Microsoft y piden acceso. Al concederlo entran con el rol que elijas.
      </p>
      <Table<UsuarioConsola>
        rowKey="id"
        size="small"
        loading={cargando}
        dataSource={filas}
        pagination={false}
        style={{ marginTop: 16 }}
        locale={{ emptyText: <Empty description="No hay solicitudes pendientes" /> }}
        columns={[
          {
            title: 'Persona',
            render: (_, u) => (
              <span>
                {/* Sin correo verificado el nombre lo pone la propia cuenta: no prueba quién es */}
                <b>{u.email ? (u.nombre ?? u.email) : `Dice llamarse «${u.nombre ?? '—'}»`}</b>
                {!u.email && (
                  <Tag color="red" style={{ marginLeft: 8 }}>
                    identidad sin verificar
                  </Tag>
                )}
                <div>
                  <Typography.Text type="secondary">
                    {u.email ?? 'sin correo verificado'} · {PROVEEDOR[u.proveedor ?? ''] ?? u.proveedor}
                  </Typography.Text>
                </div>
              </span>
            ),
          },
          { title: 'Solicitado', dataIndex: 'solicitado_en', width: 190, render: fecha },
          {
            title: 'Rol al conceder',
            width: 190,
            render: (_, u) => (
              <Select<Rol>
                size="small"
                style={{ width: 170 }}
                value={roles[u.id] ?? 'calidad'}
                options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
                onChange={(rol) => setRoles((prev) => ({ ...prev, [u.id]: rol }))}
              />
            ),
          },
          {
            title: '',
            width: 220,
            render: (_, u) => (
              <Space>
                <Button
                  type="primary"
                  size="small"
                  className="ops-primary"
                  icon={<CheckOutlined />}
                  onClick={() =>
                    void accion(() => apiUsuarios.conceder(u.id, roles[u.id] ?? 'calidad'), `Acceso concedido a ${u.email ?? u.nombre_usuario}`)
                  }
                >
                  Conceder
                </Button>
                <Popconfirm
                  title="Denegar el acceso"
                  description="Verá que no tiene acceso; podrá volver a solicitarlo."
                  okText="Denegar"
                  okButtonProps={{ danger: true }}
                  cancelText="Cancelar"
                  onConfirm={() => void accion(() => apiUsuarios.denegar(u.id), 'Acceso denegado')}
                >
                  <Button size="small" danger icon={<CloseOutlined />}>
                    Denegar
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
