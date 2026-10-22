import { useState } from 'react'
import { Alert, Button, Form, Input, Modal, Popconfirm, Select, Space } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import {
  catalogs,
  type EquipmentData,
  type SpecialistData,
  type EstateData,
  type HomeData,
  type CatalogEquipment,
  type CatalogSpecialist,
  type EstateDetail,
  type CatalogHome,
} from '../api'
import type { Catalog } from '../catalog'
import { category } from '../texts'
import { detail } from './userRoles'

/** Qué se está creando o editando. Sin `finca`/`vivienda`/`equipo`/`profesional`: es nuevo. */
export type Editing =
  | { kind: 'estate'; estate?: EstateDetail }
  | { kind: 'home'; estate: EstateDetail; home?: CatalogHome }
  | { kind: 'equipment'; estate: EstateDetail; equipment?: CatalogEquipment }
  | { kind: 'professional'; professional?: CatalogSpecialist }

interface Props {
  editing: Editing
  catalog: Catalog
  onClose: () => void
  /** Tras guardar o borrar: recarga y avisa. */
  onDone: (notice: string) => void
  /** Borrar un profesional pasa por la confirmación de lo que se desasigna. */
  onDeleteProfessional: (p: CatalogSpecialist) => void
}

const TITLES: Record<Editing['kind'], [string, string]> = {
  estate: ['Nueva finca', 'Editar finca'],
  home: ['Nueva vivienda', 'Editar vivienda'],
  equipment: ['Nuevo equipo', 'Editar equipo'],
  professional: ['Nuevo profesional', 'Editar profesional'],
}

/** Campos vacíos de un formulario → null, como espera la API. */
const nulls = <T extends object>(values: T): T =>
  Object.fromEntries(
    Object.entries(values).map(([k, v]) => [k, typeof v === 'string' && v.trim() === '' ? null : v ?? null]),
  ) as T

function initialValues(e: Editing): Record<string, unknown> {
  switch (e.kind) {
    case 'estate':
      return e.estate
        ? { address: e.estate.address, email: e.estate.email, official_phone: e.estate.official_phone }
        : {}
    case 'home':
      return e.home ? { entrance: e.home.entrance, floor: e.home.floor, door: e.home.door } : {}
    case 'equipment': {
      if (!e.equipment) return {}
      const { id: _id, description: _d, ...remainder } = e.equipment
      return remainder
    }
    case 'professional':
      return e.professional
        ? {
            administrator_id: e.professional.administrator_id,
            name: e.professional.name,
            main_category: e.professional.main_category,
            contact_channel: e.professional.contact_channel,
            target: e.professional.target,
          }
        : { contact_channel: 'email' }
  }
}

/** Formularios de alta y edición del catálogo. Todo lo que se guarda aquí lo usa el asistente. */
export function CatalogEditing({ editing, catalog, onClose, onDone, onDeleteProfessional }: Props) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Se monta de nuevo en cada edición (key en BaseDatos): los valores iniciales bastan
  const [initial] = useState(() => {
    const v = initialValues(editing)
    if (!v.administrator_id && catalog.administrators.length === 1) v.administrator_id = catalog.administrators[0].id
    return v
  })
  const channel = Form.useWatch('contact_channel', form) as string | undefined
  const chosenAdmin = Form.useWatch('administrator_id', form) as string | undefined

  const existing =
    (editing.kind === 'estate' && editing.estate) ||
    (editing.kind === 'home' && editing.home) ||
    (editing.kind === 'equipment' && editing.equipment) ||
    (editing.kind === 'professional' && editing.professional) ||
    null
  const title = TITLES[editing.kind][existing ? 1 : 0]

  const run = async (fn: () => Promise<unknown>, notice: string) => {
    setSaving(true)
    setError(null)
    try {
      await fn()
      onDone(notice)
    } catch (e) {
      setError(detail(e))
    } finally {
      setSaving(false)
    }
  }

  const save = async () => {
    let v: Record<string, unknown>
    try {
      v = nulls(await form.validateFields())
    } catch {
      return // el formulario ya marca qué falta
    }
    const e = editing
    switch (e.kind) {
      case 'estate': {
        const data = v as unknown as EstateData & { administrator_id: string }
        return run(
          () => (e.estate ? catalogs.editEstate(e.estate.id, data) : catalogs.createEstate(data)),
          e.estate ? 'Finca actualizada' : 'Finca creada',
        )
      }
      case 'home': {
        const data = v as unknown as HomeData
        return run(
          () =>
            e.home
              ? catalogs.editHome(e.estate.id, e.home.id, data)
              : catalogs.createHome(e.estate.id, data),
          e.home ? 'Vivienda actualizada' : 'Vivienda añadida',
        )
      }
      case 'equipment': {
        const data = v as unknown as EquipmentData
        return run(
          () =>
            e.equipment ? catalogs.editEquipment(e.estate.id, e.equipment.id, data) : catalogs.createEquipment(e.estate.id, data),
          e.equipment ? 'Equipo actualizado' : 'Equipo añadido',
        )
      }
      case 'professional': {
        const data = v as unknown as SpecialistData & { administrator_id: string }
        return run(
          () =>
            e.professional ? catalogs.editSpecialist(e.professional.id, data) : catalogs.createSpecialist(data),
          e.professional ? 'Profesional actualizado' : 'Profesional creado',
        )
      }
    }
  }

  const remove = () => {
    const e = editing
    switch (e.kind) {
      case 'estate':
        return e.estate && run(() => catalogs.deleteEstate(e.estate!.id), 'Finca borrada')
      case 'home':
        return e.home && run(() => catalogs.deleteHome(e.estate.id, e.home!.id), 'Vivienda borrada')
      case 'equipment':
        return e.equipment && run(() => catalogs.deleteEquipment(e.estate.id, e.equipment!.id), 'Equipo borrado')
      case 'professional':
        return e.professional && onDeleteProfessional(e.professional)
    }
  }

  const deletionNotice: Record<Editing['kind'], string> = {
    estate: 'Se borran también sus viviendas y equipos. Si ya tiene llamadas o incidencias no se podrá: su historial la necesita.',
    home: 'El asistente dejará de aceptarla. Si ya tiene llamadas o incidencias no se podrá borrar.',
    equipment: 'Las averías de este equipo irán al profesional de la categoría. Si tiene incidencias no se podrá borrar.',
    professional: 'Si el asistente lo usa, te diré antes qué categorías y equipos se quedan sin él.',
  }

  // Administrador del que dependen las opciones (catálogo de categorías y mantenedores)
  const adminId =
    editing.kind === 'home' || editing.kind === 'equipment'
      ? editing.estate.administrator_id
      : editing.kind === 'professional'
        ? editing.professional?.administrator_id ?? chosenAdmin
        : undefined
  const admin = catalog.administrators.find((a) => a.id === adminId)
  const categories = admin?.category_catalog ?? []
  const maintainers = catalog.professionals.filter((p) => p.administrator_id === adminId)
  const adminOptions = catalog.administrators.map((a) => ({ value: a.id, label: a.name }))

  return (
    <Modal
      open
      title={
        editing.kind === 'home' || editing.kind === 'equipment' ? `${title} · ${editing.estate.address}` : title
      }
      onCancel={onClose}
      destroyOnHidden
      footer={
        <div className="db-modal-footer">
          {existing ? (
            <Popconfirm
              title={`¿Borrar ${editing.kind === 'professional' ? 'este profesional' : `esta ${editing.kind === 'equipment' ? 'instalación' : editing.kind}`}?`}
              description={<div style={{ maxWidth: 320 }}>{deletionNotice[editing.kind]}</div>}
              okText="Borrar"
              okButtonProps={{ danger: true }}
              cancelText="Cancelar"
              onConfirm={() => void remove()}
            >
              <Button danger icon={<DeleteOutlined />} disabled={saving}>
                Borrar
              </Button>
            </Popconfirm>
          ) : (
            <span />
          )}
          <Space>
            <Button onClick={onClose}>Cancelar</Button>
            <Button type="primary" loading={saving} onClick={() => void save()}>
              Guardar
            </Button>
          </Space>
        </div>
      }
    >
      <Form form={form} layout="vertical" requiredMark="optional" disabled={saving} initialValues={initial}>
        {editing.kind === 'estate' && (
          <>
            {!editing.estate && (
              <Form.Item name="administrator_id" label="Administrador" rules={[{ required: true, message: 'Elige el administrador' }]}>
                <Select options={adminOptions} placeholder="Quién administra la finca" />
              </Form.Item>
            )}
            <Form.Item
              name="address"
              label="Dirección"
              rules={[{ required: true, min: 3, message: 'Escribe la dirección' }]}
              extra="El asistente reconoce la finca por la dirección que dice el vecino: escríbela como la diría él."
            >
              <Input placeholder="Calle Mayor 12" />
            </Form.Item>
            <Form.Item
              name="email"
              label="Correo de la finca"
              rules={[{ required: true, type: 'email', message: 'Correo no válido' }]}
              extra="Las incidencias que tiene que revisar el administrador se dejan a este correo."
            >
              <Input placeholder="finca@administracion.es" />
            </Form.Item>
            <Form.Item
              name="official_phone"
              label="Teléfono oficial"
              rules={[{ required: true, message: 'Escribe el teléfono' }]}
              extra="Dato de la ficha: el asistente todavía no lo usa en las llamadas."
            >
              <Input placeholder="+34910000000" />
            </Form.Item>
          </>
        )}

        {editing.kind === 'home' && (
          <>
            <Form.Item name="entrance" label="Portal" extra="Solo si la finca tiene varios portales.">
              <Input placeholder="1" />
            </Form.Item>
            <Space.Compact block>
              <Form.Item name="floor" label="Piso" rules={[{ required: true, message: 'Falta el piso' }]} style={{ flex: 1 }}>
                <Input placeholder="3" />
              </Form.Item>
              <Form.Item name="door" label="Puerta" rules={[{ required: true, message: 'Falta la puerta' }]} style={{ flex: 1 }}>
                <Input placeholder="B" />
              </Form.Item>
            </Space.Compact>
            <p className="db-hint">El asistente solo acepta viviendas de esta lista cuando el vecino dice «3º B».</p>
          </>
        )}

        {editing.kind === 'equipment' && (
          <>
            <Form.Item
              name="kind"
              label="Tipo de equipo"
              rules={[{ required: true, min: 2, message: 'Escribe qué es' }]}
              extra="Con estas palabras reconoce el asistente que la avería es de este equipo."
            >
              <Input placeholder="portero automático, ascensor, puerta de garaje…" />
            </Form.Item>
            <Space.Compact block>
              <Form.Item name="brand" label="Marca" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
              <Form.Item name="model" label="Modelo" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
            </Space.Compact>
            <Form.Item name="location" label="Ubicación">
              <Input placeholder="portal, garaje, cubierta…" />
            </Form.Item>
            <Form.Item
              name="maintainer_id"
              label="Quién lo mantiene"
              extra="Las averías de este equipo se avisan a su mantenedor antes que al profesional de la categoría."
            >
              <Select
                allowClear
                placeholder="Sin mantenedor"
                options={maintainers.map((p) => ({ value: p.id, label: `${p.name} · ${category(p.main_category)}` }))}
              />
            </Form.Item>
            <Form.Item name="installer" label="Instalador">
              <Input />
            </Form.Item>
            <Space.Compact block>
              <Form.Item name="contract" label="Contrato de mantenimiento" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
              <Form.Item name="warranty_until" label="Garantía hasta" style={{ flex: 1 }}>
                <Input type="date" />
              </Form.Item>
            </Space.Compact>
          </>
        )}

        {editing.kind === 'professional' && (
          <>
            {!editing.professional && (
              <Form.Item name="administrator_id" label="Administrador" rules={[{ required: true, message: 'Elige el administrador' }]}>
                <Select options={adminOptions} placeholder="Para qué administrador trabaja" />
              </Form.Item>
            )}
            <Form.Item name="name" label="Nombre" rules={[{ required: true, min: 2, message: 'Escribe el nombre' }]}>
              <Input placeholder="Fontanería Pérez" />
            </Form.Item>
            <Form.Item
              name="main_category"
              label="Oficio"
              rules={[{ required: true, message: 'Elige el oficio' }]}
              extra="Informativo. Quién atiende cada avería se elige en «Quién atiende cada avería» de la ficha de la finca, y vale para todas las fincas del administrador."
            >
              <Select
                disabled={!categories.length}
                placeholder={categories.length ? 'Fontanería, electricidad…' : 'Elige antes el administrador'}
                options={categories.map((c) => ({ value: c, label: category(c) }))}
              />
            </Form.Item>
            <Form.Item name="contact_channel" label="Se le avisa por" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'email', label: 'Correo' },
                  { value: 'call', label: 'Llamada' },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="target"
              label={channel === 'call' ? 'Teléfono' : 'Correo'}
              rules={[
                { required: true, message: 'Escribe dónde se le avisa' },
                channel === 'call'
                  ? { pattern: /^\+?[\d\s.-]{6,20}$/, message: 'Teléfono no válido' }
                  : { type: 'email', message: 'Correo no válido' },
              ]}
              extra="Aquí le llegan los avisos de las averías que le asigna el asistente."
            >
              <Input placeholder={channel === 'call' ? '+34600000000' : 'avisos@empresa.es'} />
            </Form.Item>
          </>
        )}
      </Form>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} />}
    </Modal>
  )
}
