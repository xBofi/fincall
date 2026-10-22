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
import { ApiError, catalogs, type CatalogSpecialist, type EstateDetail, type CatalogHome } from '../api'
import { assignment, categoriesOf, rulesOf, type Catalog } from '../catalog'
import { channel, capitalize, category, ruleTarget, urgency } from '../texts'
import { CatalogEditing, type Editing } from './CatalogEditing'
import { detail } from './userRoles'

/** Lo que el asistente ha identificado en la llamada en curso: se resalta en la base de datos. */
export interface Highlight {
  estateId?: string | null
  homeId?: string | null
  professionalId?: string | null
  category?: string | null
}

type Tab = 'estates' | 'professionals'

interface Props {
  catalog: Catalog
  highlight?: Highlight
  /** `panel`: columna lateral de la simulación. `pagina`: pantalla completa. */
  mode: 'panel' | 'page'
  /** Owner y administrador pueden dar de alta, editar y borrar (solo en `pagina`). */
  editable?: boolean
}

/** Acciones de edición que las tarjetas piden a la página. */
interface Editor {
  edit: (e: Editing) => void
  assign: (estate: EstateDetail, category: string, professionalId: string | null) => void
}

/** "3 b", "3º B", "portal 2 1a" → piezas comparables con las viviendas. */
const normalizeText = (t: string) =>
  t
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[º°ª.,]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

function estateText(f: EstateDetail, names: Record<string, string>): string {
  const homes = f.homes.map((v) => `${v.entrance ? `portal ${v.entrance} ` : ''}${v.floor} ${v.door} ${v.floor}${v.door}`)
  const professionals = Object.values(assignment(f)).map((id) => names[id] ?? '')
  return normalizeText([f.address, f.administrator, f.email, f.official_phone, ...homes, ...professionals].join(' '))
}

function professionalText(p: CatalogSpecialist, estates: EstateDetail[]): string {
  return normalizeText([p.name, category(p.main_category), p.target, p.administrator, ...estates.map((f) => f.address)].join(' '))
}

const matches = (text: string, query: string) => normalizeText(query).split(' ').every((piece) => text.includes(piece))

/** Marca lo que el asistente consulta en las llamadas, para distinguirlo de los datos de ficha. */
function UsesAssistant({ what }: { what: string }) {
  return (
    <span className="db-uses" title={`El asistente lo usa: ${what}`}>
      <RobotOutlined /> lo usa el asistente
    </span>
  )
}

function Contact({ channel: c, target }: { channel: string; target: string }) {
  return (
    <span className="db-contact">
      {c === 'call' ? <PhoneOutlined /> : <MailOutlined />}
      <span>{target}</span>
    </span>
  )
}

function Homes({
  homes,
  highlighted,
  onEdit,
}: {
  homes: CatalogHome[]
  highlighted?: string | null
  onEdit?: (v: CatalogHome) => void
}) {
  const entrances = useMemo(() => {
    const groups = new Map<string, CatalogHome[]>()
    for (const v of homes) {
      const key = v.entrance ?? ''
      groups.set(key, [...(groups.get(key) ?? []), v])
    }
    return [...groups.entries()]
  }, [homes])

  return (
    <div className="db-entrances">
      {entrances.map(([entrance, items]) => (
        <div key={entrance} className="db-entrance">
          {entrance && <span className="db-entrance-name">Portal {entrance}</span>}
          <div className="db-chips">
            {items.map((v) =>
              onEdit ? (
                <button
                  type="button"
                  key={v.id}
                  className={`db-chip is-editable ${v.id === highlighted ? 'is-match' : ''}`}
                  onClick={() => onEdit(v)}
                  title="Editar o borrar la vivienda"
                >
                  {v.floor}º {v.door}
                </button>
              ) : (
                <span key={v.id} className={`db-chip ${v.id === highlighted ? 'is-match' : ''}`}>
                  {v.floor}º {v.door}
                </span>
              ),
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function EstateCard({
  estate,
  catalog,
  opened,
  onToggle,
  highlight,
  editor,
  mark,
}: {
  estate: EstateDetail
  catalog: Catalog
  opened: boolean
  onToggle: () => void
  highlight?: Highlight
  editor?: Editor
  /** Señala qué datos consulta el asistente (pantalla completa). */
  mark?: boolean
}) {
  const ref = useRef<HTMLElement>(null)
  const identified = highlight?.estateId === estate.id
  const cfg = estate.effective_config
  const byCategory = assignment(estate)
  const professionalById = useMemo(
    () => Object.fromEntries(catalog.professionals.map((p) => [p.id, p])),
    [catalog.professionals],
  )
  const professionalOptions = useMemo(
    () =>
      catalog.professionals
        .filter((p) => p.administrator_id === estate.administrator_id)
        .map((p) => ({ value: p.id, label: `${p.name} · ${category(p.main_category)}` })),
    [catalog.professionals, estate.administrator_id],
  )

  useEffect(() => {
    if (identified) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [identified])

  return (
    <article ref={ref} className={`db-tile ${identified ? 'is-match' : ''}`}>
      <button type="button" className="db-header" onClick={onToggle} aria-expanded={opened}>
        <span className="db-chevron">{opened ? <DownOutlined /> : <RightOutlined />}</span>
        <span className="db-title">
          <b>{estate.address}</b>
          <small>
            {estate.administrator} · {estate.homes.length} viviendas
          </small>
        </span>
        {identified && (
          <Tag color="blue" icon={<CheckCircleFilled />} className="db-badge">
            En la llamada
          </Tag>
        )}
      </button>

      {opened && (
        <div className="db-body">
          {(mark || editor) && (
            <div className="db-actions">
              {mark && <UsesAssistant what="reconoce la finca por la dirección que dice el vecino" />}
              {editor && (
                <Button size="small" icon={<EditOutlined />} onClick={() => editor.edit({ kind: 'estate', estate })}>
                  Editar finca
                </Button>
              )}
            </div>
          )}
          <dl className="db-data">
            <div>
              <dt>Teléfono</dt>
              <dd>{estate.official_phone}</dd>
            </div>
            <div>
              <dt>
                Correo{' '}
                {mark && (
                  <span className="db-uses-icon" title="El asistente deja aquí lo que tiene que revisar el administrador">
                    <RobotOutlined />
                  </span>
                )}
              </dt>
              <dd>{estate.email}</dd>
            </div>
            <div>
              <dt>Administrador</dt>
              <dd>{estate.administrator}</dd>
            </div>
          </dl>

          <section>
            <h4 className="db-section">
              <span>Viviendas</span>
              {mark && <UsesAssistant what="solo acepta viviendas de esta lista" />}
              {editor && (
                <Button size="small" type="link" icon={<PlusOutlined />} onClick={() => editor.edit({ kind: 'home', estate })}>
                  Añadir
                </Button>
              )}
            </h4>
            {estate.homes.length ? (
              <Homes
                homes={estate.homes}
                highlighted={identified ? highlight?.homeId : null}
                onEdit={editor ? (home) => editor.edit({ kind: 'home', estate, home }) : undefined}
              />
            ) : (
              <span className="is-muted">Sin viviendas: el asistente no podrá registrar incidencias aquí</span>
            )}
          </section>

          {(estate.equipment_list.length > 0 || editor) && (
            <section>
              <h4 className="db-section">
                <span>Equipos y quién los mantiene</span>
                {mark && <UsesAssistant what="avisa al mantenedor del equipo antes que al profesional de la categoría" />}
                {editor && (
                  <Button size="small" type="link" icon={<PlusOutlined />} onClick={() => editor.edit({ kind: 'equipment', estate })}>
                    Añadir
                  </Button>
                )}
              </h4>
              {!estate.equipment_list.length && <span className="is-muted">Sin equipos registrados</span>}
              <ul className="db-rows">
                {estate.equipment_list.map((e) => {
                  const m = e.maintainer_id ? professionalById[e.maintainer_id] : undefined
                  const conditions = [e.contract && `contrato ${e.contract}`, e.warranty_until && `garantía hasta ${e.warranty_until}`]
                    .filter(Boolean)
                    .join(' · ')
                  return (
                    <li key={e.id}>
                      <span className="db-row-key">
                        {editor ? (
                          <button
                            type="button"
                            className="db-link"
                            onClick={() => editor.edit({ kind: 'equipment', estate, equipment: e })}
                            title="Editar o borrar el equipo"
                          >
                            {e.description} <EditOutlined />
                          </button>
                        ) : (
                          e.description
                        )}
                      </span>
                      {m ? (
                        <span className="db-row-value">
                          <b>{m.name}</b>
                          <Contact channel={m.contact_channel} target={m.target} />
                          {conditions && <small className="is-muted"> · {conditions}</small>}
                        </span>
                      ) : (
                        <span className="db-row-value is-muted">Sin mantenedor: se avisa al profesional de la categoría</span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </section>
          )}

          <section>
            <h4 className="db-section">
              <span>Quién atiende cada avería</span>
              {mark && <UsesAssistant what="avisa a este profesional cuando es una reparación" />}
            </h4>
            {editor &&
              (estate.own_assignment ? (
                <p className="db-hint">
                  Esta finca fija sus propios profesionales en su configuración: lo que se asigne a{' '}
                  {estate.administrator} no le llega.
                </p>
              ) : (
                <p className="db-hint">
                  Se guarda para todas las fincas de {estate.administrator}, como una versión nueva de su configuración.
                </p>
              ))}
            <ul className="db-rows">
              {categoriesOf(estate).map((c) => {
                const p = byCategory[c] ? professionalById[byCategory[c]] : undefined
                const active = identified && highlight?.category === c
                return (
                  <li key={c} className={active ? 'is-match' : ''}>
                    <span className="db-row-key">{category(c)}</span>
                    {editor && !estate.own_assignment ? (
                      <span className="db-row-value">
                        <Select
                          size="small"
                          allowClear
                          className="db-assign"
                          value={byCategory[c] ?? null}
                          placeholder="Sin profesional: para el administrador"
                          options={professionalOptions}
                          aria-label={`Profesional de ${category(c)}`}
                          onChange={(v: string | null | undefined) => editor.assign(estate, c, v ?? null)}
                        />
                      </span>
                    ) : p ? (
                      <span className="db-row-value">
                        <b>{p.name}</b>
                        <Contact channel={p.contact_channel} target={p.target} />
                      </span>
                    ) : (
                      <span className="db-row-value is-muted">Sin profesional: queda pendiente para el administrador</span>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>

          <section>
            <h4>Cómo se avisa según la urgencia</h4>
            <ul className="db-rows">
              {rulesOf(estate).map((r) => {
                const u = urgency(r.urgency)
                return (
                  <li key={r.urgency}>
                    <span className="db-row-key">{u && <Tag color={u.color}>{u.text}</Tag>}</span>
                    <span className="db-row-value">
                      {capitalize(channel(r.channel))} {ruleTarget(r.target, catalog.professionalName)}
                    </span>
                  </li>
                )
              })}
              <li>
                <span className="db-row-key">
                  <Tag color="red">Emergencia</Tag>
                </span>
                {cfg.emergencies_enabled ? (
                  <span className="db-row-value">
                    Indicación de seguridad y llamada al teléfono oficial de la finca (simulada por ahora)
                  </span>
                ) : (
                  <span className="db-row-value is-muted">Se trata como urgencia alta</span>
                )}
              </li>
            </ul>
          </section>

          <section>
            <h4>Comportamiento del asistente en esta finca</h4>
            <ul className="db-rows">
              <li>
                <span className="db-row-key">Intentos para encontrar la finca</span>
                <span className="db-row-value">{String(cfg.estate_attempts)}</span>
              </li>
              <li>
                <span className="db-row-key">Intentos para la vivienda</span>
                <span className="db-row-value">{String(cfg.home_attempts)}</span>
              </li>
              <li>
                <span className="db-row-key">Pide un detalle extra del problema</span>
                <span className="db-row-value">{cfg.ask_detail ? 'Sí' : 'No'}</span>
              </li>
            </ul>
          </section>
        </div>
      )}
    </article>
  )
}

function ProfessionalCard({
  p,
  estates,
  highlight,
  editor,
  mark,
}: {
  p: CatalogSpecialist
  estates: EstateDetail[]
  highlight?: Highlight
  editor?: Editor
  mark?: boolean
}) {
  const ref = useRef<HTMLElement>(null)
  const assigned = highlight?.professionalId === p.id
  useEffect(() => {
    if (assigned) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [assigned])

  return (
    <article ref={ref} className={`db-tile db-professional ${assigned ? 'is-match' : ''}`}>
      <div className="db-header is-static">
        <span className="db-title">
          <b>{p.name}</b>
          <small>{p.administrator}</small>
        </span>
        {assigned ? (
          <Tag color="blue" icon={<CheckCircleFilled />} className="db-badge">
            Asignado
          </Tag>
        ) : (
          <Tag className="db-badge">{category(p.main_category)}</Tag>
        )}
        {editor && (
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            aria-label={`Editar ${p.name}`}
            title="Editar o borrar"
            onClick={() => editor.edit({ kind: 'professional', professional: p })}
          />
        )}
      </div>
      <div className="db-body">
        <ul className="db-rows">
          {assigned && (
            <li>
              <span className="db-row-key">Oficio</span>
              <span className="db-row-value">{category(p.main_category)}</span>
            </li>
          )}
          <li>
            <span className="db-row-key">
              Se le avisa por{' '}
              {mark && (
                <span className="db-uses-icon" title="El asistente le avisa por este canal">
                  <RobotOutlined />
                </span>
              )}
            </span>
            <span className="db-row-value">
              <Contact channel={p.contact_channel} target={`${capitalize(channel(p.contact_channel))} · ${p.target}`} />
            </span>
          </li>
          <li>
            <span className="db-row-key">Atiende en</span>
            <span className="db-row-value">{estates.length ? estates.map((f) => f.address).join(' · ') : <span className="is-muted">Ninguna finca lo tiene asignado</span>}</span>
          </li>
        </ul>
      </div>
    </article>
  )
}

export function Database({ catalog, highlight, mode, editable = false }: Props) {
  const [tab, setTab] = useState<Tab>('estates')
  const [query, setQuery] = useState('')
  // Lo que el usuario abre o cierra a mano manda sobre lo que se abre solo
  const [manual, setManual] = useState<Record<string, boolean>>({})
  const [editing, setEditing] = useState<Editing | null>(null)
  // Cada apertura monta el formulario de cero: sin restos de la edición anterior
  const [openings, setOpenings] = useState(0)
  const open = (e: Editing) => {
    setOpenings((n) => n + 1)
    setEditing(e)
  }
  const [modal, modalContext] = Modal.useModal()
  const mark = mode === 'page'

  const few = catalog.estates.length === 1 || (mode === 'page' && catalog.estates.length <= 4)
  const isOpen = (id: string, visible: number) =>
    manual[id] ?? (id === highlight?.estateId || few || (query.trim() !== '' && visible <= 3))

  const estates = useMemo(() => {
    const q = query.trim()
    return q ? catalog.estates.filter((f) => matches(estateText(f, catalog.professionalName), q)) : catalog.estates
  }, [catalog.estates, catalog.professionalName, query])

  const professionals = useMemo(() => {
    const q = query.trim()
    const items = q
      ? catalog.professionals.filter((p) => matches(professionalText(p, catalog.estatesByProfessional[p.id] ?? []), q))
      : catalog.professionals
    return [...items].sort((a, b) => category(a.main_category).localeCompare(category(b.main_category)))
  }, [catalog.professionals, catalog.estatesByProfessional, query])

  const toggle = (id: string) => setManual((prev) => ({ ...prev, [id]: !isOpen(id, estates.length) }))

  const done = (notice: string) => {
    setEditing(null)
    void message.success(notice)
    catalog.reload()
  }

  const deleteProfessional = async (p: CatalogSpecialist) => {
    try {
      await catalogs.deleteSpecialist(p.id)
      done('Profesional borrado')
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409 && e.detail.includes('el asistente lo usa'))) {
        void message.error(detail(e))
        return
      }
      // El asistente lo usa: se dice qué se queda sin él antes de borrarlo
      modal.confirm({
        title: `¿Borrar a ${p.name} igualmente?`,
        content: e.detail,
        okText: 'Desasignar y borrar',
        okButtonProps: { danger: true },
        cancelText: 'Cancelar',
        onOk: async () => {
          try {
            const r = await catalogs.deleteSpecialist(p.id, true)
            const parts = [
              r.unassigned_categories.length > 0 && `${r.unassigned_categories.map(category).join(', ')} sin profesional`,
              r.equipment_without_maintainer.length > 0 && `${r.equipment_without_maintainer.length} equipo(s) sin mantenedor`,
            ].filter(Boolean)
            done(`Profesional borrado${parts.length ? ` · ${parts.join(' · ')}` : ''}`)
          } catch (err) {
            void message.error(detail(err))
          }
        },
      })
    }
  }

  const editor: Editor | undefined =
    editable && mode === 'page'
      ? {
          edit: open,
          assign: (estate, c, professionalId) => {
            catalogs
              .assignCategory(estate.administrator_id, c, professionalId)
              .then(() => {
                void message.success(
                  professionalId
                    ? `${category(c)}: ${catalog.professionalName[professionalId] ?? professionalId}`
                    : `${category(c)}: queda para el administrador`,
                )
                catalog.reload()
              })
              .catch((e: unknown) => void message.error(detail(e)))
          },
        }
      : undefined

  if (catalog.error) {
    return (
      <Alert
        type="error"
        showIcon
        message="No se pudo leer la base de datos"
        description={catalog.error}
        action={<Button size="small" onClick={catalog.reload}>Reintentar</Button>}
      />
    )
  }

  const hasSearch = query.trim() !== ''

  return (
    <div className={`db bd-${mode}`}>
      <div className="db-tools">
        <Segmented<Tab>
          value={tab}
          onChange={setTab}
          options={[
            { label: `Fincas (${catalog.estates.length})`, value: 'estates' },
            { label: `Profesionales (${catalog.professionals.length})`, value: 'professionals' },
          ]}
        />
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder={tab === 'estates' ? 'Buscar calle, vivienda (3º B)…' : 'Buscar nombre, oficio, finca…'}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="db-search"
        />
        {editor && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => editor.edit(tab === 'estates' ? { kind: 'estate' } : { kind: 'professional' })}
          >
            {tab === 'estates' ? 'Nueva finca' : 'Nuevo profesional'}
          </Button>
        )}
        {mode === 'page' && (
          <Button icon={<ReloadOutlined />} onClick={catalog.reload} title="Volver a leer la base de datos" />
        )}
      </div>

      {mark && (
        <p className="db-legend">
          <RobotOutlined /> marca lo que el asistente consulta en cada llamada
          {editor ? ': lo que cambies se aplica desde la siguiente llamada.' : '.'}
        </p>
      )}

      {catalog.loading && !catalog.estates.length ? (
        <Spin tip="Leyendo la base de datos…">
          <div style={{ minHeight: 160 }} />
        </Spin>
      ) : tab === 'estates' ? (
        estates.length ? (
          <div className="db-items">
            {estates.map((f) => (
              <EstateCard
                key={f.id}
                estate={f}
                catalog={catalog}
                opened={isOpen(f.id, estates.length)}
                onToggle={() => toggle(f.id)}
                highlight={highlight}
                editor={editor}
                mark={mark}
              />
            ))}
          </div>
        ) : (
          <Empty description={hasSearch ? 'Ninguna finca coincide con la búsqueda' : 'No hay fincas dadas de alta'} />
        )
      ) : professionals.length ? (
        <div className="db-items">
          {professionals.map((p) => (
            <ProfessionalCard
              key={p.id}
              p={p}
              estates={catalog.estatesByProfessional[p.id] ?? []}
              highlight={highlight}
              editor={editor}
              mark={mark}
            />
          ))}
        </div>
      ) : (
        <Empty description={hasSearch ? 'Ningún profesional coincide con la búsqueda' : 'No hay profesionales dados de alta'} />
      )}

      {modalContext}
      {editor && editing && (
        <CatalogEditing
          key={openings}
          editing={editing}
          catalog={catalog}
          onClose={() => setEditing(null)}
          onDone={done}
          onDeleteProfessional={(p) => {
            setEditing(null)
            void deleteProfessional(p)
          }}
        />
      )}
    </div>
  )
}
