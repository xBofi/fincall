import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Empty, Input, Popconfirm, Segmented, Select, Spin, Tag, Timeline, message } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import {
  inbox,
  type IncidentDetail,
  type HistoryEntry,
  type ManualStatus,
  type InboxRow,
  type InboxFilters,
} from '../api'
import type { Catalog } from '../catalog'
import { work_type, category, incidentStatus, errorMessage, reviewReason, urgency } from '../texts'

type View = 'administrator' | 'open_items' | 'todas'

const CLOSED = new Set(['resolved', 'closed'])
const MANUAL_STATUSES: { value: ManualStatus; text: string }[] = [
  { value: 'in_progress', text: 'En curso' },
  { value: 'awaiting_information', text: 'Esperando información' },
  { value: 'resolved', text: 'Resuelta' },
  { value: 'closed', text: 'Cerrada' },
]

const time = (iso: string) =>
  new Date(iso).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

function UrgencyTag({ u }: { u: string }) {
  const t = urgency(u)
  return t ? <Tag color={t.color}>{t.text}</Tag> : null
}

function StatusTag({ e }: { e: string }) {
  const t = incidentStatus(e)
  return <Tag color={t.color}>{t.text}</Tag>
}

interface Props {
  catalog: Catalog
  /** Owner y administrador actúan; calidad solo mira */
  canAct: boolean
  /** Para el contador de la pestaña */
  onPending?: (n: number) => void
}

/** Incidencias que esperan una decisión del administrador y lo que se hace con ellas. */
export function Inbox({ catalog, canAct, onPending }: Props) {
  const [view, setView] = useState<View>('administrator')
  const [estateId, setEstateId] = useState<string | undefined>()
  const [level, setLevel] = useState<string | undefined>()
  const [rows, setRows] = useState<InboxRow[]>([])
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<IncidentDetail | null>(null)

  const load = useCallback(async () => {
    const filters: InboxFilters = {
      pending_administrator_only: view === 'administrator',
      estate_id: estateId,
      urgency: level,
    }
    try {
      const [items, pending] = await Promise.all([
        inbox.list_all(filters),
        inbox.list_all({ pending_administrator_only: true }),
      ])
      setRows(view === 'open_items' ? items.filter((f) => !CLOSED.has(f.state)) : items)
      onPending?.(pending.length)
    } catch (e) {
      message.error(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [view, estateId, level, onPending])

  useEffect(() => {
    void load()
    const id = window.setInterval(() => {
      if (!document.hidden) void load()
    }, 20_000)
    return () => window.clearInterval(id)
  }, [load])

  const open = useCallback(async (id: string) => {
    try {
      setDetail(await inbox.detail(id))
    } catch (e) {
      message.error(errorMessage(e))
    }
  }, [])

  const tras = useCallback(
    async (id: string) => {
      await Promise.all([load(), open(id)])
    },
    [load, open],
  )

  return (
    <>
      <section className="ops-single band-cab">
        <div className="ops-panel-heading">
          <div>
            <p className="ops-eyebrow">Incidencias</p>
            <h2>Bandeja del administrador</h2>
          </div>
        </div>
        <p className="ops-queue-note">
          Lo que el asistente no avisa por su cuenta: siniestros, reposiciones, convivencia, zonas privativas, averías sin
          profesional o vecinos que discrepan. Decide a quién va y sigue su estado.
        </p>
      </section>

      <section className="hist band">
        <aside className="hist-lista" aria-label="Incidencias">
          <Segmented<View>
            block
            size="small"
            value={view}
            onChange={setView}
            options={[
              { label: 'Para decidir', value: 'administrator' },
              { label: 'Abiertas', value: 'open_items' },
              { label: 'Todas', value: 'todas' },
            ]}
          />
          <div className="band-filtros">
            <Select
              allowClear
              size="small"
              placeholder="Todas las fincas"
              value={estateId}
              onChange={setEstateId}
              options={catalog.estates.map((f) => ({ value: f.id, label: f.address }))}
            />
            <Select
              allowClear
              size="small"
              placeholder="Cualquier urgencia"
              value={level}
              onChange={setLevel}
              options={['emergency', 'high', 'medium', 'low'].map((u) => ({ value: u, label: urgency(u)?.text }))}
            />
          </div>
          {loading && <Spin className="hist-vacio" />}
          {!loading && rows.length === 0 && (
            <Empty
              className="hist-vacio"
              description={view === 'administrator' ? 'Nada pendiente de decidir' : 'Ninguna incidencia con este filtro'}
            />
          )}
          {rows.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`hist-fila ${detail?.incident.id === f.id ? 'is-selected' : ''} ${f.pending_administrator ? 'is-pendiente' : ''}`}
              onClick={() => void open(f.id)}
            >
              <span className="hist-fila-cab">
                <b>{category(f.category)}</b>
                <time>{time(f.created_at)}</time>
              </span>
              <small className="band-desc">{f.description || 'Sin descripción'}</small>
              <small>
                {f.estate_address ?? 'Finca desconocida'}
                {f.home ? ` · ${f.home}` : ''}
              </small>
              <span className="band-tags">
                <UrgencyTag u={f.urgency} />
                <StatusTag e={f.state} />
                {f.pending_administrator ? (
                  <Tag color="orange">Para decidir</Tag>
                ) : (
                  f.specialist_name && <Tag>{f.specialist_name}</Tag>
                )}
              </span>
            </button>
          ))}
        </aside>

        <section className="hist-detalle" aria-label="Detalle de la incidencia">
          {detail ? (
            <Detail key={detail.incident.id} d={detail} canAct={canAct} onUpdate={tras} />
          ) : (
            <Empty description="Elige una incidencia para ver la sugerencia, la llamada y decidir qué hacer" />
          )}
        </section>
      </section>
    </>
  )
}

function Detail({
  d,
  canAct,
  onUpdate,
}: {
  d: IncidentDetail
  canAct: boolean
  onUpdate: (id: string) => Promise<void>
}) {
  const inc = d.incident
  const call = d.call
  const reason = reviewReason(call?.review_reason)
  const closed = inc.state === 'closed'
  return (
    <div className="hist-dossier band-dossier">
      <header className="hist-cab">
        <div>
          <p className="ops-eyebrow">
            {category(inc.category)} · {work_type(inc.work_type)} · {time(inc.created_at)}
          </p>
          <h2>{inc.description || 'Sin descripción'}</h2>
        </div>
        <span className="band-tags">
          <UrgencyTag u={inc.urgency} />
          <StatusTag e={inc.state} />
        </span>
      </header>

      {(inc.suggestion || reason) && (
        <div className={`band-sugerencia ${d.pending_administrator ? 'is-pendiente' : ''}`}>
          <p className="ops-eyebrow">
            <BulbOutlined /> {d.pending_administrator ? 'Te toca decidir' : 'Sugerencia del asistente'}
          </p>
          {reason && <p className="band-motivo">{reason}</p>}
          {inc.suggestion && <p>{inc.suggestion}</p>}
        </div>
      )}

      <dl className="ops-facts">
        <div>
          <dt>Finca</dt>
          <dd>{d.estate?.address ?? 'Desconocida'}</dd>
        </div>
        <div>
          <dt>Vivienda</dt>
          <dd>{d.home ?? 'Desconocida'}</dd>
        </div>
        <div>
          <dt>Equipo afectado</dt>
          <dd>
            {d.equipment ? (
              <>
                {d.equipment.description}
                {d.equipment.contract ? ` · contrato ${d.equipment.contract}` : ''}
                {d.equipment.warranty_until ? ` · garantía hasta ${d.equipment.warranty_until}` : ''}
              </>
            ) : (
              '—'
            )}
          </dd>
        </div>
        <div>
          <dt>Mantenedor del equipo</dt>
          <dd>{d.equipment?.maintainer ? `${d.equipment.maintainer.name} (${d.equipment.maintainer.target})` : '—'}</dd>
        </div>
        <div>
          <dt>Asignada a</dt>
          <dd>{d.specialist ? `${d.specialist.name} (${d.specialist.target})` : 'Nadie todavía'}</dd>
        </div>
        <div>
          <dt>Vecino</dt>
          <dd>
            {call?.declared_name ?? 'Sin nombre'}
            {call?.caller_phone ? ` · ${call.caller_phone}` : ''}
          </dd>
        </div>
      </dl>

      {canAct && !closed && <Actions d={d} onUpdate={onUpdate} />}

      {call?.summary && (
        <div className="ops-summary">
          <p className="ops-eyebrow">Resumen de la llamada</p>
          <p>{call.summary}</p>
        </div>
      )}

      <div className="hist-bloque">
        <h3>Historial</h3>
        {d.history.length === 0 ? (
          <small className="hist-nota">Nadie ha actuado todavía sobre esta incidencia.</small>
        ) : (
          <Timeline
            className="band-historial"
            items={d.history.map((h) => ({ key: h.id, children: <HistoryEntryLine h={h} /> }))}
          />
        )}
      </div>

      {call && call.transcript.length > 0 && (
        <div className="hist-bloque hist-conv">
          <h3>Conversación</h3>
          <div className="hist-chat">
            {call.transcript.map((t, i) => (
              <div key={i} className={`burbuja ${t.role === 'bot' ? 'bot' : 'caller'}`}>
                {t.text}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function HistoryEntryLine({ h }: { h: HistoryEntry }) {
  const who = h.user_name ?? 'Alguien'
  let what: string
  if (h.action === 'assign') what = `asignó a ${String(h.detail.specialist_name ?? h.detail.specialist_id ?? '—')}`
  else if (h.action === 'note') what = 'añadió una nota'
  else what = `cambió el estado a «${incidentStatus(h.new_state).text.toLowerCase()}»`
  return (
    <div className="band-entrada">
      <span>
        <b>{who}</b> {what}
        <time> · {time(h.changed_at)}</time>
      </span>
      {h.detail.notice === 'pending_send' && <Tag color="gold">Aviso pendiente de envío</Tag>}
      {h.note && <p>{h.note}</p>}
    </div>
  )
}

function Actions({ d, onUpdate }: { d: IncidentDetail; onUpdate: (id: string) => Promise<void> }) {
  const inc = d.incident
  const suggested = d.equipment?.maintainer?.id ?? inc.specialist_id ?? undefined
  const [specialist, setSpecialist] = useState<string | undefined>(suggested)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const assignable = !CLOSED.has(inc.state)

  const options = useMemo(
    () =>
      d.specialists.map((e) => ({
        value: e.id,
        label: `${e.name} · ${category(e.main_category)}${e.id === d.equipment?.maintainer?.id ? ' · mantenedor' : ''}`,
      })),
    [d],
  )

  const run = async (fn: () => Promise<{ notice?: string }>, ok: string) => {
    setBusy(true)
    try {
      const r = await fn()
      message.success(r.notice === 'pending_send' ? `${ok}. El aviso queda pendiente de envío.` : ok)
      setNote('')
      await onUpdate(inc.id)
    } catch (e) {
      message.error(errorMessage(e))
      // Si otro usuario actuó mientras tanto (409), se ve ya su decisión
      await onUpdate(inc.id)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="hist-bloque band-acciones">
      <h3>Decidir</h3>
      {assignable && (
        <div className="band-accion">
          <label htmlFor="band-especialista">Asignar a un profesional</label>
          <div className="band-fila">
            <Select
              id="band-especialista"
              showSearch
              optionFilterProp="label"
              placeholder="Elige un profesional"
              value={specialist}
              onChange={setSpecialist}
              options={options}
            />
            <Button
              type="primary"
              disabled={!specialist || busy}
              loading={busy}
              onClick={() => void run(() => inbox.assign(inc.id, specialist!, note.trim()), 'Incidencia asignada')}
            >
              Asignar
            </Button>
          </div>
          <small className="hist-nota">Todavía no se envía ningún aviso real: queda anotado como pendiente de envío.</small>
        </div>
      )}

      <div className="band-accion">
        <label>Cambiar estado</label>
        <div className="band-estados">
          {MANUAL_STATUSES.filter((e) => e.value !== inc.state).map((e) =>
            e.value === 'closed' ? (
              <Popconfirm
                key={e.value}
                title="¿Cerrar la incidencia?"
                description="Una incidencia cerrada ya no se puede modificar."
                okText="Cerrar"
                cancelText="Cancelar"
                onConfirm={() => void run(() => inbox.state(inc.id, e.value, note.trim()), 'Incidencia cerrada')}
              >
                <Button size="small" disabled={busy}>
                  {e.text}
                </Button>
              </Popconfirm>
            ) : (
              <Button
                key={e.value}
                size="small"
                disabled={busy}
                onClick={() => void run(() => inbox.state(inc.id, e.value, note.trim()), `Estado: ${e.text.toLowerCase()}`)}
              >
                {e.text}
              </Button>
            ),
          )}
        </div>
      </div>

      <div className="band-accion">
        <label htmlFor="band-nota">Nota</label>
        <Input.TextArea
          id="band-nota"
          rows={2}
          maxLength={2000}
          value={note}
          onChange={(ev) => setNote(ev.target.value)}
          placeholder="Opcional al asignar o cambiar el estado; también se puede guardar sola"
        />
        <Button
          size="small"
          disabled={!note.trim() || busy}
          onClick={() => void run(() => inbox.note(inc.id, note.trim()), 'Nota guardada')}
        >
          Guardar solo la nota
        </Button>
      </div>
    </div>
  )
}
