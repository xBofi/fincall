import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Empty, Input, Modal, Segmented, Select, Spin, Tag, message } from 'antd'
import {
  CheckCircleFilled,
  DownOutlined,
  EditOutlined,
  MailOutlined,
  PhoneOutlined,
  PlusOutlined,
  ReloadOutlined,
  RightOutlined,
  RobotOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { ApiError, catalogos, type EspecialistaCatalogo, type FincaDetalle, type ViviendaCatalogo } from '../api'
import { asignacion, categoriasDe, reglasDe, type Catalogo } from '../catalogo'
import { canal, capitalizar, categoria, destinoRegla, urgencia } from '../textos'
import { EdicionCatalogo, type Edicion } from './EdicionCatalogo'
import { detalle } from './rolesUsuario'

/** Lo que el asistente ha identificado en la llamada en curso: se resalta en la base de datos. */
export interface Resaltado {
  fincaId?: string | null
  viviendaId?: string | null
  profesionalId?: string | null
  categoria?: string | null
}

type Pestana = 'fincas' | 'profesionales'

interface Props {
  catalogo: Catalogo
  resaltado?: Resaltado
  /** `panel`: columna lateral de la simulación. `pagina`: pantalla completa. */
  modo: 'panel' | 'pagina'
  /** Owner y administrador pueden dar de alta, editar y borrar (solo en `pagina`). */
  editable?: boolean
}

/** Acciones de edición que las tarjetas piden a la página. */
interface Editor {
  editar: (e: Edicion) => void
  asignar: (finca: FincaDetalle, categoria: string, profesionalId: string | null) => void
}

/** "3 b", "3º B", "portal 2 1a" → piezas comparables con las viviendas. */
const normalizar = (t: string) =>
  t
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[º°ª.,]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

function textoFinca(f: FincaDetalle, nombres: Record<string, string>): string {
  const viviendas = f.viviendas.map((v) => `${v.portal ? `portal ${v.portal} ` : ''}${v.piso} ${v.puerta} ${v.piso}${v.puerta}`)
  const profesionales = Object.values(asignacion(f)).map((id) => nombres[id] ?? '')
  return normalizar([f.direccion, f.administrador, f.correo, f.telefono_oficial, ...viviendas, ...profesionales].join(' '))
}

function textoProfesional(p: EspecialistaCatalogo, fincas: FincaDetalle[]): string {
  return normalizar([p.nombre, categoria(p.categoria_principal), p.destino, p.administrador, ...fincas.map((f) => f.direccion)].join(' '))
}

const coincide = (texto: string, consulta: string) => normalizar(consulta).split(' ').every((pieza) => texto.includes(pieza))

/** Marca lo que el asistente consulta en las llamadas, para distinguirlo de los datos de ficha. */
function UsaAsistente({ que }: { que: string }) {
  return (
    <span className="bd-usa" title={`El asistente lo usa: ${que}`}>
      <RobotOutlined /> lo usa el asistente
    </span>
  )
}

function Contacto({ canal: c, destino }: { canal: string; destino: string }) {
  return (
    <span className="bd-contacto">
      {c === 'llamada' ? <PhoneOutlined /> : <MailOutlined />}
      <span>{destino}</span>
    </span>
  )
}

function Viviendas({
  viviendas,
  resaltada,
  onEditar,
}: {
  viviendas: ViviendaCatalogo[]
  resaltada?: string | null
  onEditar?: (v: ViviendaCatalogo) => void
}) {
  const portales = useMemo(() => {
    const grupos = new Map<string, ViviendaCatalogo[]>()
    for (const v of viviendas) {
      const clave = v.portal ?? ''
      grupos.set(clave, [...(grupos.get(clave) ?? []), v])
    }
    return [...grupos.entries()]
  }, [viviendas])

  return (
    <div className="bd-portales">
      {portales.map(([portal, lista]) => (
        <div key={portal} className="bd-portal">
          {portal && <span className="bd-portal-nombre">Portal {portal}</span>}
          <div className="bd-chips">
            {lista.map((v) =>
              onEditar ? (
                <button
                  type="button"
                  key={v.id}
                  className={`bd-chip is-editable ${v.id === resaltada ? 'is-match' : ''}`}
                  onClick={() => onEditar(v)}
                  title="Editar o borrar la vivienda"
                >
                  {v.piso}º {v.puerta}
                </button>
              ) : (
                <span key={v.id} className={`bd-chip ${v.id === resaltada ? 'is-match' : ''}`}>
                  {v.piso}º {v.puerta}
                </span>
              ),
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function TarjetaFinca({
  finca,
  catalogo,
  abierta,
  onAlternar,
  resaltado,
  editor,
  marcar,
}: {
  finca: FincaDetalle
  catalogo: Catalogo
  abierta: boolean
  onAlternar: () => void
  resaltado?: Resaltado
  editor?: Editor
  /** Señala qué datos consulta el asistente (pantalla completa). */
  marcar?: boolean
}) {
  const ref = useRef<HTMLElement>(null)
  const identificada = resaltado?.fincaId === finca.id
  const cfg = finca.configuracion_efectiva
  const porCategoria = asignacion(finca)
  const profesionalPorId = useMemo(
    () => Object.fromEntries(catalogo.profesionales.map((p) => [p.id, p])),
    [catalogo.profesionales],
  )
  const opcionesProfesional = useMemo(
    () =>
      catalogo.profesionales
        .filter((p) => p.administrador_id === finca.administrador_id)
        .map((p) => ({ value: p.id, label: `${p.nombre} · ${categoria(p.categoria_principal)}` })),
    [catalogo.profesionales, finca.administrador_id],
  )

  useEffect(() => {
    if (identificada) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [identificada])

  return (
    <article ref={ref} className={`bd-tarjeta ${identificada ? 'is-match' : ''}`}>
      <button type="button" className="bd-cabecera" onClick={onAlternar} aria-expanded={abierta}>
        <span className="bd-chevron">{abierta ? <DownOutlined /> : <RightOutlined />}</span>
        <span className="bd-titulo">
          <b>{finca.direccion}</b>
          <small>
            {finca.administrador} · {finca.viviendas.length} viviendas
          </small>
        </span>
        {identificada && (
          <Tag color="blue" icon={<CheckCircleFilled />} className="bd-badge">
            En la llamada
          </Tag>
        )}
      </button>

      {abierta && (
        <div className="bd-cuerpo">
          {(marcar || editor) && (
            <div className="bd-acciones">
              {marcar && <UsaAsistente que="reconoce la finca por la dirección que dice el vecino" />}
              {editor && (
                <Button size="small" icon={<EditOutlined />} onClick={() => editor.editar({ tipo: 'finca', finca })}>
                  Editar finca
                </Button>
              )}
            </div>
          )}
          <dl className="bd-datos">
            <div>
              <dt>Teléfono</dt>
              <dd>{finca.telefono_oficial}</dd>
            </div>
            <div>
              <dt>
                Correo{' '}
                {marcar && (
                  <span className="bd-usa-icono" title="El asistente deja aquí lo que tiene que revisar el administrador">
                    <RobotOutlined />
                  </span>
                )}
              </dt>
              <dd>{finca.correo}</dd>
            </div>
            <div>
              <dt>Administrador</dt>
              <dd>{finca.administrador}</dd>
            </div>
          </dl>

          <section>
            <h4 className="bd-seccion">
              <span>Viviendas</span>
              {marcar && <UsaAsistente que="solo acepta viviendas de esta lista" />}
              {editor && (
                <Button size="small" type="link" icon={<PlusOutlined />} onClick={() => editor.editar({ tipo: 'vivienda', finca })}>
                  Añadir
                </Button>
              )}
            </h4>
            {finca.viviendas.length ? (
              <Viviendas
                viviendas={finca.viviendas}
                resaltada={identificada ? resaltado?.viviendaId : null}
                onEditar={editor ? (vivienda) => editor.editar({ tipo: 'vivienda', finca, vivienda }) : undefined}
              />
            ) : (
              <span className="is-muted">Sin viviendas: el asistente no podrá registrar incidencias aquí</span>
            )}
          </section>

          {(finca.equipos.length > 0 || editor) && (
            <section>
              <h4 className="bd-seccion">
                <span>Equipos y quién los mantiene</span>
                {marcar && <UsaAsistente que="avisa al mantenedor del equipo antes que al profesional de la categoría" />}
                {editor && (
                  <Button size="small" type="link" icon={<PlusOutlined />} onClick={() => editor.editar({ tipo: 'equipo', finca })}>
                    Añadir
                  </Button>
                )}
              </h4>
              {!finca.equipos.length && <span className="is-muted">Sin equipos registrados</span>}
              <ul className="bd-filas">
                {finca.equipos.map((e) => {
                  const m = e.mantenedor_id ? profesionalPorId[e.mantenedor_id] : undefined
                  const condiciones = [e.contrato && `contrato ${e.contrato}`, e.garantia_hasta && `garantía hasta ${e.garantia_hasta}`]
                    .filter(Boolean)
                    .join(' · ')
                  return (
                    <li key={e.id}>
                      <span className="bd-fila-clave">
                        {editor ? (
                          <button
                            type="button"
                            className="bd-enlace"
                            onClick={() => editor.editar({ tipo: 'equipo', finca, equipo: e })}
                            title="Editar o borrar el equipo"
                          >
                            {e.descripcion} <EditOutlined />
                          </button>
                        ) : (
                          e.descripcion
                        )}
                      </span>
                      {m ? (
                        <span className="bd-fila-valor">
                          <b>{m.nombre}</b>
                          <Contacto canal={m.canal_contacto} destino={m.destino} />
                          {condiciones && <small className="is-muted"> · {condiciones}</small>}
                        </span>
                      ) : (
                        <span className="bd-fila-valor is-muted">Sin mantenedor: se avisa al profesional de la categoría</span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </section>
          )}

          <section>
            <h4 className="bd-seccion">
              <span>Quién atiende cada avería</span>
              {marcar && <UsaAsistente que="avisa a este profesional cuando es una reparación" />}
            </h4>
            {editor &&
              (finca.asignacion_propia ? (
                <p className="bd-ayuda">
                  Esta finca fija sus propios profesionales en su configuración: lo que se asigne a{' '}
                  {finca.administrador} no le llega.
                </p>
              ) : (
                <p className="bd-ayuda">
                  Se guarda para todas las fincas de {finca.administrador}, como una versión nueva de su configuración.
                </p>
              ))}
            <ul className="bd-filas">
              {categoriasDe(finca).map((c) => {
                const p = porCategoria[c] ? profesionalPorId[porCategoria[c]] : undefined
                const activa = identificada && resaltado?.categoria === c
                return (
                  <li key={c} className={activa ? 'is-match' : ''}>
                    <span className="bd-fila-clave">{categoria(c)}</span>
                    {editor && !finca.asignacion_propia ? (
                      <span className="bd-fila-valor">
                        <Select
                          size="small"
                          allowClear
                          className="bd-asignar"
                          value={porCategoria[c] ?? null}
                          placeholder="Sin profesional: para el administrador"
                          options={opcionesProfesional}
                          aria-label={`Profesional de ${categoria(c)}`}
                          onChange={(v: string | null | undefined) => editor.asignar(finca, c, v ?? null)}
                        />
                      </span>
                    ) : p ? (
                      <span className="bd-fila-valor">
                        <b>{p.nombre}</b>
                        <Contacto canal={p.canal_contacto} destino={p.destino} />
                      </span>
                    ) : (
                      <span className="bd-fila-valor is-muted">Sin profesional: queda pendiente para el administrador</span>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>

          <section>
            <h4>Cómo se avisa según la urgencia</h4>
            <ul className="bd-filas">
              {reglasDe(finca).map((r) => {
                const u = urgencia(r.urgencia)
                return (
                  <li key={r.urgencia}>
                    <span className="bd-fila-clave">{u && <Tag color={u.color}>{u.texto}</Tag>}</span>
                    <span className="bd-fila-valor">
                      {capitalizar(canal(r.canal))} {destinoRegla(r.destino, catalogo.nombreProfesional)}
                    </span>
                  </li>
                )
              })}
              {!cfg.emergencias_activas && (
                <li>
                  <span className="bd-fila-clave">
                    <Tag color="red">Emergencia</Tag>
                  </span>
                  <span className="bd-fila-valor is-muted">Se trata como urgencia alta</span>
                </li>
              )}
            </ul>
          </section>

          <section>
            <h4>Comportamiento del asistente en esta finca</h4>
            <ul className="bd-filas">
              <li>
                <span className="bd-fila-clave">Intentos para encontrar la finca</span>
                <span className="bd-fila-valor">{String(cfg.intentos_finca)}</span>
              </li>
              <li>
                <span className="bd-fila-clave">Intentos para la vivienda</span>
                <span className="bd-fila-valor">{String(cfg.intentos_vivienda)}</span>
              </li>
              <li>
                <span className="bd-fila-clave">Pide un detalle extra del problema</span>
                <span className="bd-fila-valor">{cfg.pedir_detalle ? 'Sí' : 'No'}</span>
              </li>
            </ul>
          </section>
        </div>
      )}
    </article>
  )
}

function TarjetaProfesional({
  p,
  fincas,
  resaltado,
  editor,
  marcar,
}: {
  p: EspecialistaCatalogo
  fincas: FincaDetalle[]
  resaltado?: Resaltado
  editor?: Editor
  marcar?: boolean
}) {
  const ref = useRef<HTMLElement>(null)
  const asignado = resaltado?.profesionalId === p.id
  useEffect(() => {
    if (asignado) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [asignado])

  return (
    <article ref={ref} className={`bd-tarjeta bd-profesional ${asignado ? 'is-match' : ''}`}>
      <div className="bd-cabecera is-estatica">
        <span className="bd-titulo">
          <b>{p.nombre}</b>
          <small>{p.administrador}</small>
        </span>
        {asignado ? (
          <Tag color="blue" icon={<CheckCircleFilled />} className="bd-badge">
            Asignado
          </Tag>
        ) : (
          <Tag className="bd-badge">{categoria(p.categoria_principal)}</Tag>
        )}
        {editor && (
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            aria-label={`Editar ${p.nombre}`}
            title="Editar o borrar"
            onClick={() => editor.editar({ tipo: 'profesional', profesional: p })}
          />
        )}
      </div>
      <div className="bd-cuerpo">
        <ul className="bd-filas">
          {asignado && (
            <li>
              <span className="bd-fila-clave">Oficio</span>
              <span className="bd-fila-valor">{categoria(p.categoria_principal)}</span>
            </li>
          )}
          <li>
            <span className="bd-fila-clave">
              Se le avisa por{' '}
              {marcar && (
                <span className="bd-usa-icono" title="El asistente le avisa por este canal">
                  <RobotOutlined />
                </span>
              )}
            </span>
            <span className="bd-fila-valor">
              <Contacto canal={p.canal_contacto} destino={`${capitalizar(canal(p.canal_contacto))} · ${p.destino}`} />
            </span>
          </li>
          <li>
            <span className="bd-fila-clave">Atiende en</span>
            <span className="bd-fila-valor">{fincas.length ? fincas.map((f) => f.direccion).join(' · ') : <span className="is-muted">Ninguna finca lo tiene asignado</span>}</span>
          </li>
        </ul>
      </div>
    </article>
  )
}

export function BaseDatos({ catalogo, resaltado, modo, editable = false }: Props) {
  const [pestana, setPestana] = useState<Pestana>('fincas')
  const [consulta, setConsulta] = useState('')
  // Lo que el usuario abre o cierra a mano manda sobre lo que se abre solo
  const [manual, setManual] = useState<Record<string, boolean>>({})
  const [edicion, setEdicion] = useState<Edicion | null>(null)
  // Cada apertura monta el formulario de cero: sin restos de la edición anterior
  const [aperturas, setAperturas] = useState(0)
  const abrir = (e: Edicion) => {
    setAperturas((n) => n + 1)
    setEdicion(e)
  }
  const [modal, contextoModal] = Modal.useModal()
  const marcar = modo === 'pagina'

  const pocas = catalogo.fincas.length === 1 || (modo === 'pagina' && catalogo.fincas.length <= 4)
  const estaAbierta = (id: string, visibles: number) =>
    manual[id] ?? (id === resaltado?.fincaId || pocas || (consulta.trim() !== '' && visibles <= 3))

  const fincas = useMemo(() => {
    const q = consulta.trim()
    return q ? catalogo.fincas.filter((f) => coincide(textoFinca(f, catalogo.nombreProfesional), q)) : catalogo.fincas
  }, [catalogo.fincas, catalogo.nombreProfesional, consulta])

  const profesionales = useMemo(() => {
    const q = consulta.trim()
    const lista = q
      ? catalogo.profesionales.filter((p) => coincide(textoProfesional(p, catalogo.fincasDeProfesional[p.id] ?? []), q))
      : catalogo.profesionales
    return [...lista].sort((a, b) => categoria(a.categoria_principal).localeCompare(categoria(b.categoria_principal)))
  }, [catalogo.profesionales, catalogo.fincasDeProfesional, consulta])

  const alternar = (id: string) => setManual((prev) => ({ ...prev, [id]: !estaAbierta(id, fincas.length) }))

  const hecho = (aviso: string) => {
    setEdicion(null)
    void message.success(aviso)
    catalogo.recargar()
  }

  const borrarProfesional = async (p: EspecialistaCatalogo) => {
    try {
      await catalogos.borrarEspecialista(p.id)
      hecho('Profesional borrado')
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409 && e.detalle.includes('el asistente lo usa'))) {
        void message.error(detalle(e))
        return
      }
      // El asistente lo usa: se dice qué se queda sin él antes de borrarlo
      modal.confirm({
        title: `¿Borrar a ${p.nombre} igualmente?`,
        content: e.detalle,
        okText: 'Desasignar y borrar',
        okButtonProps: { danger: true },
        cancelText: 'Cancelar',
        onOk: async () => {
          try {
            const r = await catalogos.borrarEspecialista(p.id, true)
            const partes = [
              r.categorias_desasignadas.length > 0 && `${r.categorias_desasignadas.map(categoria).join(', ')} sin profesional`,
              r.equipos_sin_mantenedor.length > 0 && `${r.equipos_sin_mantenedor.length} equipo(s) sin mantenedor`,
            ].filter(Boolean)
            hecho(`Profesional borrado${partes.length ? ` · ${partes.join(' · ')}` : ''}`)
          } catch (err) {
            void message.error(detalle(err))
          }
        },
      })
    }
  }

  const editor: Editor | undefined =
    editable && modo === 'pagina'
      ? {
          editar: abrir,
          asignar: (finca, c, profesionalId) => {
            catalogos
              .asignarCategoria(finca.administrador_id, c, profesionalId)
              .then(() => {
                void message.success(
                  profesionalId
                    ? `${categoria(c)}: ${catalogo.nombreProfesional[profesionalId] ?? profesionalId}`
                    : `${categoria(c)}: queda para el administrador`,
                )
                catalogo.recargar()
              })
              .catch((e: unknown) => void message.error(detalle(e)))
          },
        }
      : undefined

  if (catalogo.error) {
    return (
      <Alert
        type="error"
        showIcon
        message="No se pudo leer la base de datos"
        description={catalogo.error}
        action={<Button size="small" onClick={catalogo.recargar}>Reintentar</Button>}
      />
    )
  }

  const hayBusqueda = consulta.trim() !== ''

  return (
    <div className={`bd bd-${modo}`}>
      <div className="bd-herramientas">
        <Segmented<Pestana>
          value={pestana}
          onChange={setPestana}
          options={[
            { label: `Fincas (${catalogo.fincas.length})`, value: 'fincas' },
            { label: `Profesionales (${catalogo.profesionales.length})`, value: 'profesionales' },
          ]}
        />
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder={pestana === 'fincas' ? 'Buscar calle, vivienda (3º B)…' : 'Buscar nombre, oficio, finca…'}
          value={consulta}
          onChange={(e) => setConsulta(e.target.value)}
          className="bd-buscador"
        />
        {editor && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => editor.editar(pestana === 'fincas' ? { tipo: 'finca' } : { tipo: 'profesional' })}
          >
            {pestana === 'fincas' ? 'Nueva finca' : 'Nuevo profesional'}
          </Button>
        )}
        {modo === 'pagina' && (
          <Button icon={<ReloadOutlined />} onClick={catalogo.recargar} title="Volver a leer la base de datos" />
        )}
      </div>

      {marcar && (
        <p className="bd-leyenda">
          <RobotOutlined /> marca lo que el asistente consulta en cada llamada
          {editor ? ': lo que cambies se aplica desde la siguiente llamada.' : '.'}
        </p>
      )}

      {catalogo.cargando && !catalogo.fincas.length ? (
        <Spin tip="Leyendo la base de datos…">
          <div style={{ minHeight: 160 }} />
        </Spin>
      ) : pestana === 'fincas' ? (
        fincas.length ? (
          <div className="bd-lista">
            {fincas.map((f) => (
              <TarjetaFinca
                key={f.id}
                finca={f}
                catalogo={catalogo}
                abierta={estaAbierta(f.id, fincas.length)}
                onAlternar={() => alternar(f.id)}
                resaltado={resaltado}
                editor={editor}
                marcar={marcar}
              />
            ))}
          </div>
        ) : (
          <Empty description={hayBusqueda ? 'Ninguna finca coincide con la búsqueda' : 'No hay fincas dadas de alta'} />
        )
      ) : profesionales.length ? (
        <div className="bd-lista">
          {profesionales.map((p) => (
            <TarjetaProfesional
              key={p.id}
              p={p}
              fincas={catalogo.fincasDeProfesional[p.id] ?? []}
              resaltado={resaltado}
              editor={editor}
              marcar={marcar}
            />
          ))}
        </div>
      ) : (
        <Empty description={hayBusqueda ? 'Ningún profesional coincide con la búsqueda' : 'No hay profesionales dados de alta'} />
      )}

      {contextoModal}
      {editor && edicion && (
        <EdicionCatalogo
          key={aperturas}
          edicion={edicion}
          catalogo={catalogo}
          onCerrar={() => setEdicion(null)}
          onHecho={hecho}
          onBorrarProfesional={(p) => {
            setEdicion(null)
            void borrarProfesional(p)
          }}
        />
      )}
    </div>
  )
}
