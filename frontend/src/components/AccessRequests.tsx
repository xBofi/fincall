import { useCallback, useEffect, useState } from 'react'
import { Button, Empty, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import { users as apiUsers, type Role, type ConsoleUser } from '../api'
import { PROVIDER, ROLES, detail } from './userRoles'

function date(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' }) : '—'
}

/** Personas que han pedido acceso: el owner lo concede con un rol o lo deniega. */
export function AccessRequests({ onUpdate }: { onUpdate: () => void }) {
  const [rows, setRows] = useState<ConsoleUser[]>([])
  const [loading, setLoading] = useState(true)
  const [roles, setRoles] = useState<Record<string, Role>>({})

  const load = useCallback(async () => {
    try {
      const todos = await apiUsers.list_all()
      setRows(todos.filter((u) => u.access_state === 'requested'))
    } catch (e) {
      message.error(detail(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const action = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn()
      message.success(ok)
      await load()
      onUpdate()
    } catch (e) {
      message.error(detail(e))
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
      <Table<ConsoleUser>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        style={{ marginTop: 16 }}
        locale={{ emptyText: <Empty description="No hay solicitudes pendientes" /> }}
        columns={[
          {
            title: 'Persona',
            render: (_, u) => (
              <span>
                {/* Sin correo verificado el nombre lo pone la propia cuenta: no prueba quién es */}
                <b>{u.email ? (u.name ?? u.email) : `Dice llamarse «${u.name ?? '—'}»`}</b>
                {!u.email && (
                  <Tag color="red" style={{ marginLeft: 8 }}>
                    identidad sin verificar
                  </Tag>
                )}
                <div>
                  <Typography.Text type="secondary">
                    {u.email ?? 'sin correo verificado'} · {PROVIDER[u.provider ?? ''] ?? u.provider}
                  </Typography.Text>
                </div>
              </span>
            ),
          },
          { title: 'Solicitado', dataIndex: 'requested_at', width: 190, render: date },
          {
            title: 'Rol al conceder',
            width: 190,
            render: (_, u) => (
              <Select<Role>
                size="small"
                style={{ width: 170 }}
                value={roles[u.id] ?? 'quality'}
                options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
                onChange={(role) => setRoles((prev) => ({ ...prev, [u.id]: role }))}
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
                    void action(() => apiUsers.grant(u.id, roles[u.id] ?? 'quality'), `Acceso concedido a ${u.email ?? u.username}`)
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
                  onConfirm={() => void action(() => apiUsers.deny(u.id), 'Acceso denegado')}
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
