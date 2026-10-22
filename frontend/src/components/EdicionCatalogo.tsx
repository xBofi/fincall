import { useState } from 'react'
import { Alert, Button, Form, Input, Modal, Popconfirm, Select, Space } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import {
  catalogos,
  type DatosEquipo,
  type DatosEspecialista,
  type DatosFinca,
  type DatosVivienda,
  type EquipoCatalogo,
  type EspecialistaCatalogo,
  type FincaDetalle,
  type ViviendaCatalogo,
} from '../api'
import type { Catalogo } from '../catalogo'
import { categoria } from '../textos'
import { detalle } from './rolesUsuario'

/** Qué se está creando o editando. Sin `finca`/`vivienda`/`equipo`/`profesional`: es nuevo. */
export type Edicion =
  | { tipo: 'finca'; finca?: FincaDetalle }
  | { tipo: 'vivienda'; finca: FincaDetalle; vivienda?: ViviendaCatalogo }
  | { tipo: 'equipo'; finca: FincaDetalle; equipo?: EquipoCatalogo }
  | { tipo: 'profesional'; profesional?: EspecialistaCatalogo }

interface Props {
  edicion: Edicion
  catalogo: Catalogo
  onCerrar: () => void
  /** Tras guardar o borrar: recarga y avisa. */
  onHecho: (aviso: string) => void
  /** Borrar un profesional pasa por la confirmación de lo que se desasigna. */
  onBorrarProfesional: (p: EspecialistaCatalogo) => void
}

const TITULOS: Record<Edicion['tipo'], [string, string]> = {
  finca: ['Nueva finca', 'Editar finca'],
  vivienda: ['Nueva vivienda', 'Editar vivienda'],
  equipo: ['Nuevo equipo', 'Editar equipo'],
  profesional: ['Nuevo profesional', 'Editar profesional'],
}

/** Campos vacíos de un formulario → null, como espera la API. */
const nulos = <T extends object>(valores: T): T =>
  Object.fromEntries(
    Object.entries(valores).map(([k, v]) => [k, typeof v === 'string' && v.trim() === '' ? null : v ?? null]),
  ) as T

function valoresIniciales(e: Edicion): Record<string, unknown> {
  switch (e.tipo) {
    case 'finca':
      return e.finca
        ? { direccion: e.finca.direccion, correo: e.finca.correo, telefono_oficial: e.finca.telefono_oficial }
        : {}
    case 'vivienda':
      return e.vivienda ? { portal: e.vivienda.portal, piso: e.vivienda.piso, puerta: e.vivienda.puerta } : {}
    case 'equipo': {
      if (!e.equipo) return {}
      const { id: _id, descripcion: _d, ...resto } = e.equipo
      return resto
    }
    case 'profesional':
      return e.profesional
        ? {
            administrador_id: e.profesional.administrador_id,
            nombre: e.profesional.nombre,
            categoria_principal: e.profesional.categoria_principal,
            canal_contacto: e.profesional.canal_contacto,
            destino: e.profesional.destino,
          }
        : { canal_contacto: 'correo' }
  }
}

/** Formularios de alta y edición del catálogo. Todo lo que se guarda aquí lo usa el asistente. */
export function EdicionCatalogo({ edicion, catalogo, onCerrar, onHecho, onBorrarProfesional }: Props) {
  const [form] = Form.useForm()
  const [guardando, setGuardando] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Se monta de nuevo en cada edición (key en BaseDatos): los valores iniciales bastan
  const [iniciales] = useState(() => {
    const v = valoresIniciales(edicion)
    if (!v.administrador_id && catalogo.administradores.length === 1) v.administrador_id = catalogo.administradores[0].id
    return v
  })
  const canal = Form.useWatch('canal_contacto', form) as string | undefined
  const adminElegido = Form.useWatch('administrador_id', form) as string | undefined

  const existente =
    (edicion.tipo === 'finca' && edicion.finca) ||
    (edicion.tipo === 'vivienda' && edicion.vivienda) ||
    (edicion.tipo === 'equipo' && edicion.equipo) ||
    (edicion.tipo === 'profesional' && edicion.profesional) ||
    null
  const titulo = TITULOS[edicion.tipo][existente ? 1 : 0]

  const ejecutar = async (fn: () => Promise<unknown>, aviso: string) => {
    setGuardando(true)
    setError(null)
    try {
      await fn()
      onHecho(aviso)
    } catch (e) {
      setError(detalle(e))
    } finally {
      setGuardando(false)
    }
  }

  const guardar = async () => {
    let v: Record<string, unknown>
    try {
      v = nulos(await form.validateFields())
    } catch {
      return // el formulario ya marca qué falta
    }
    const e = edicion
    switch (e.tipo) {
      case 'finca': {
        const datos = v as unknown as DatosFinca & { administrador_id: string }
        return ejecutar(
          () => (e.finca ? catalogos.editarFinca(e.finca.id, datos) : catalogos.crearFinca(datos)),
          e.finca ? 'Finca actualizada' : 'Finca creada',
        )
      }
      case 'vivienda': {
        const datos = v as unknown as DatosVivienda
        return ejecutar(
          () =>
            e.vivienda
              ? catalogos.editarVivienda(e.finca.id, e.vivienda.id, datos)
              : catalogos.crearVivienda(e.finca.id, datos),
          e.vivienda ? 'Vivienda actualizada' : 'Vivienda añadida',
        )
      }
      case 'equipo': {
        const datos = v as unknown as DatosEquipo
        return ejecutar(
          () =>
            e.equipo ? catalogos.editarEquipo(e.finca.id, e.equipo.id, datos) : catalogos.crearEquipo(e.finca.id, datos),
          e.equipo ? 'Equipo actualizado' : 'Equipo añadido',
        )
      }
      case 'profesional': {
        const datos = v as unknown as DatosEspecialista & { administrador_id: string }
        return ejecutar(
          () =>
            e.profesional ? catalogos.editarEspecialista(e.profesional.id, datos) : catalogos.crearEspecialista(datos),
          e.profesional ? 'Profesional actualizado' : 'Profesional creado',
        )
      }
    }
  }

  const borrar = () => {
    const e = edicion
    switch (e.tipo) {
      case 'finca':
        return e.finca && ejecutar(() => catalogos.borrarFinca(e.finca!.id), 'Finca borrada')
      case 'vivienda':
        return e.vivienda && ejecutar(() => catalogos.borrarVivienda(e.finca.id, e.vivienda!.id), 'Vivienda borrada')
      case 'equipo':
        return e.equipo && ejecutar(() => catalogos.borrarEquipo(e.finca.id, e.equipo!.id), 'Equipo borrado')
      case 'profesional':
        return e.profesional && onBorrarProfesional(e.profesional)
    }
  }

  const avisoBorrado: Record<Edicion['tipo'], string> = {
    finca: 'Se borran también sus viviendas y equipos. Si ya tiene llamadas o incidencias no se podrá: su historial la necesita.',
    vivienda: 'El asistente dejará de aceptarla. Si ya tiene llamadas o incidencias no se podrá borrar.',
    equipo: 'Las averías de este equipo irán al profesional de la categoría. Si tiene incidencias no se podrá borrar.',
    profesional: 'Si el asistente lo usa, te diré antes qué categorías y equipos se quedan sin él.',
  }

  // Administrador del que dependen las opciones (catálogo de categorías y mantenedores)
  const adminId =
    edicion.tipo === 'vivienda' || edicion.tipo === 'equipo'
      ? edicion.finca.administrador_id
      : edicion.tipo === 'profesional'
        ? edicion.profesional?.administrador_id ?? adminElegido
        : undefined
  const admin = catalogo.administradores.find((a) => a.id === adminId)
  const categorias = admin?.catalogo_categorias ?? []
  const mantenedores = catalogo.profesionales.filter((p) => p.administrador_id === adminId)
  const opcionesAdmin = catalogo.administradores.map((a) => ({ value: a.id, label: a.nombre }))

  return (
    <Modal
      open
      title={
        edicion.tipo === 'vivienda' || edicion.tipo === 'equipo' ? `${titulo} · ${edicion.finca.direccion}` : titulo
      }
      onCancel={onCerrar}
      destroyOnHidden
      footer={
        <div className="bd-modal-pie">
          {existente ? (
            <Popconfirm
              title={`¿Borrar ${edicion.tipo === 'profesional' ? 'este profesional' : `esta ${edicion.tipo === 'equipo' ? 'instalación' : edicion.tipo}`}?`}
              description={<div style={{ maxWidth: 320 }}>{avisoBorrado[edicion.tipo]}</div>}
              okText="Borrar"
              okButtonProps={{ danger: true }}
              cancelText="Cancelar"
              onConfirm={() => void borrar()}
            >
              <Button danger icon={<DeleteOutlined />} disabled={guardando}>
                Borrar
              </Button>
            </Popconfirm>
          ) : (
            <span />
          )}
          <Space>
            <Button onClick={onCerrar}>Cancelar</Button>
            <Button type="primary" loading={guardando} onClick={() => void guardar()}>
              Guardar
            </Button>
          </Space>
        </div>
      }
    >
      <Form form={form} layout="vertical" requiredMark="optional" disabled={guardando} initialValues={iniciales}>
        {edicion.tipo === 'finca' && (
          <>
            {!edicion.finca && (
              <Form.Item name="administrador_id" label="Administrador" rules={[{ required: true, message: 'Elige el administrador' }]}>
                <Select options={opcionesAdmin} placeholder="Quién administra la finca" />
              </Form.Item>
            )}
            <Form.Item
              name="direccion"
              label="Dirección"
              rules={[{ required: true, min: 3, message: 'Escribe la dirección' }]}
              extra="El asistente reconoce la finca por la dirección que dice el vecino: escríbela como la diría él."
            >
              <Input placeholder="Calle Mayor 12" />
            </Form.Item>
            <Form.Item
              name="correo"
              label="Correo de la finca"
              rules={[{ required: true, type: 'email', message: 'Correo no válido' }]}
              extra="Las incidencias que tiene que revisar el administrador se dejan a este correo."
            >
              <Input placeholder="finca@administracion.es" />
            </Form.Item>
            <Form.Item
              name="telefono_oficial"
              label="Teléfono oficial"
              rules={[{ required: true, message: 'Escribe el teléfono' }]}
              extra="Dato de la ficha: el asistente todavía no lo usa en las llamadas."
            >
              <Input placeholder="+34910000000" />
            </Form.Item>
          </>
        )}

        {edicion.tipo === 'vivienda' && (
          <>
            <Form.Item name="portal" label="Portal" extra="Solo si la finca tiene varios portales.">
              <Input placeholder="1" />
            </Form.Item>
            <Space.Compact block>
              <Form.Item name="piso" label="Piso" rules={[{ required: true, message: 'Falta el piso' }]} style={{ flex: 1 }}>
                <Input placeholder="3" />
              </Form.Item>
              <Form.Item name="puerta" label="Puerta" rules={[{ required: true, message: 'Falta la puerta' }]} style={{ flex: 1 }}>
                <Input placeholder="B" />
              </Form.Item>
            </Space.Compact>
            <p className="bd-ayuda">El asistente solo acepta viviendas de esta lista cuando el vecino dice «3º B».</p>
          </>
        )}

        {edicion.tipo === 'equipo' && (
          <>
            <Form.Item
              name="tipo"
              label="Tipo de equipo"
              rules={[{ required: true, min: 2, message: 'Escribe qué es' }]}
              extra="Con estas palabras reconoce el asistente que la avería es de este equipo."
            >
              <Input placeholder="portero automático, ascensor, puerta de garaje…" />
            </Form.Item>
            <Space.Compact block>
              <Form.Item name="marca" label="Marca" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
              <Form.Item name="modelo" label="Modelo" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
            </Space.Compact>
            <Form.Item name="ubicacion" label="Ubicación">
              <Input placeholder="portal, garaje, cubierta…" />
            </Form.Item>
            <Form.Item
              name="mantenedor_id"
              label="Quién lo mantiene"
              extra="Las averías de este equipo se avisan a su mantenedor antes que al profesional de la categoría."
            >
              <Select
                allowClear
                placeholder="Sin mantenedor"
                options={mantenedores.map((p) => ({ value: p.id, label: `${p.nombre} · ${categoria(p.categoria_principal)}` }))}
              />
            </Form.Item>
            <Form.Item name="instalador" label="Instalador">
              <Input />
            </Form.Item>
            <Space.Compact block>
              <Form.Item name="contrato" label="Contrato de mantenimiento" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
              <Form.Item name="garantia_hasta" label="Garantía hasta" style={{ flex: 1 }}>
                <Input type="date" />
              </Form.Item>
            </Space.Compact>
          </>
        )}

        {edicion.tipo === 'profesional' && (
          <>
            {!edicion.profesional && (
              <Form.Item name="administrador_id" label="Administrador" rules={[{ required: true, message: 'Elige el administrador' }]}>
                <Select options={opcionesAdmin} placeholder="Para qué administrador trabaja" />
              </Form.Item>
            )}
            <Form.Item name="nombre" label="Nombre" rules={[{ required: true, min: 2, message: 'Escribe el nombre' }]}>
              <Input placeholder="Fontanería Pérez" />
            </Form.Item>
            <Form.Item
              name="categoria_principal"
              label="Oficio"
              rules={[{ required: true, message: 'Elige el oficio' }]}
              extra="Informativo. Quién atiende cada avería se elige en «Quién atiende cada avería» de la ficha de la finca, y vale para todas las fincas del administrador."
            >
              <Select
                disabled={!categorias.length}
                placeholder={categorias.length ? 'Fontanería, electricidad…' : 'Elige antes el administrador'}
                options={categorias.map((c) => ({ value: c, label: categoria(c) }))}
              />
            </Form.Item>
            <Form.Item name="canal_contacto" label="Se le avisa por" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'correo', label: 'Correo' },
                  { value: 'llamada', label: 'Llamada' },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="destino"
              label={canal === 'llamada' ? 'Teléfono' : 'Correo'}
              rules={[
                { required: true, message: 'Escribe dónde se le avisa' },
                canal === 'llamada'
                  ? { pattern: /^\+?[\d\s.-]{6,20}$/, message: 'Teléfono no válido' }
                  : { type: 'email', message: 'Correo no válido' },
              ]}
              extra="Aquí le llegan los avisos de las averías que le asigna el asistente."
            >
              <Input placeholder={canal === 'llamada' ? '+34600000000' : 'avisos@empresa.es'} />
            </Form.Item>
          </>
        )}
      </Form>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} />}
    </Modal>
  )
}
