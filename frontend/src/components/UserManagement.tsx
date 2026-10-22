import { useCallback, useEffect, useState } from 'react'
import { Button, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import { UserDeleteOutlined } from '@ant-design/icons'
import { users as apiUsers, type UserAccessState, type Role, type ConsoleUser } from '../api'
import { PROVIDER, ROLES, detail } from './userRoles'

const ROLE_COLOR: Record<Role, string> = { owner: 'geekblue', administrator: 'cyan', quality: 'green' }

const STATE: Record<UserAccessState, { text: string; color: string }> = {
  granted: { text: 'con acceso', color: 'green' },
  requested: { text: 'solicita acceso', color: 'gold' },
  not_requested: { text: 'no ha solicitado', color: 'default' },
  denied: { text: 'denied', color: 'red' },
}

/** Quién puede entrar en la consola: rol, activación y baja. Solo owner. */
export function UserManagement({ me }: { me: ConsoleUser }) {
  const [rows, setRows] = useState<ConsoleUser[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      setRows(await apiUsers.list_all())
    } catch (e) {
      message.error(detail(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const action = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn()
      if (ok) message.success(ok)
      await load()
    } catch (e) {
      message.error(detail(e))
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

      <Table<ConsoleUser>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        style={{ marginTop: 16 }}
        columns={[
          {
            title: 'Persona',
            dataIndex: 'username',
            render: (v: string, u) => (
              <span>
                <b>{u.email || !u.provider ? (u.name ?? v) : `Dice llamarse «${u.name ?? '—'}»`}</b>
                {u.provider && !u.email && (
                  <Tag color="red" style={{ marginLeft: 8 }}>
                    identidad sin verificar
                  </Tag>
                )}
                {u.id === me.id && <Tag style={{ marginLeft: 8 }}>tú</Tag>}
                <div>
                  <Typography.Text type="secondary">
                    {u.email ?? v}
                    {u.provider ? ` · ${PROVIDER[u.provider] ?? u.provider}` : ' · cuenta antigua, no puede entrar'}
                  </Typography.Text>
                </div>
              </span>
            ),
          },
          {
            title: 'Access',
            dataIndex: 'access_state',
            width: 150,
            render: (state: UserAccessState) => <Tag color={STATE[state].color}>{STATE[state].text}</Tag>,
          },
          {
            title: 'Role',
            dataIndex: 'role',
            width: 190,
            render: (role: Role, u) =>
              u.id === me.id || u.access_state !== 'granted' ? (
                <Tag color={ROLE_COLOR[role]}>{ROLES.find((r) => r.value === role)?.label ?? role}</Tag>
              ) : (
                <Select<Role>
                  size="small"
                  value={role}
                  style={{ width: 170 }}
                  options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
                  onChange={(fresh) => void action(() => apiUsers.modify(u.id, { role: fresh }), 'Rol actualizado')}
                />
              ),
          },
          {
            title: 'Activo',
            dataIndex: 'active',
            width: 90,
            render: (active: boolean, u) => (
              <Switch
                size="small"
                checked={active}
                disabled={u.id === me.id}
                onChange={(v) => void action(() => apiUsers.modify(u.id, { active: v }), v ? 'Usuario activado' : 'Usuario desactivado')}
              />
            ),
          },
          {
            title: '',
            width: 130,
            render: (_, u) => (
              <Space>
                <Popconfirm
                  title={`Eliminar a ${u.name ?? u.username}`}
                  description="Si vuelve a entrar con su cuenta, aparecerá como una persona nueva."
                  okText="Eliminar"
                  okButtonProps={{ danger: true }}
                  cancelText="Cancelar"
                  disabled={u.id === me.id}
                  onConfirm={() => void action(() => apiUsers.remove(u.id), 'Usuario eliminado')}
                >
                  <Button size="small" danger icon={<UserDeleteOutlined />} disabled={u.id === me.id}>
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
