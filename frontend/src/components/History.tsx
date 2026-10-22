import { useMemo, useState } from 'react'
import { Button, Collapse, Empty, Popconfirm, Segmented, Tag, message } from 'antd'
import { ApiError, operations } from '../api'
import type { BankCase, CallRecordDetail, CallRecord, OperationsSummary } from '../api'
import type { Catalog } from '../catalog'
import { rate, capitalize, result } from '../texts'
import { useAuditedView } from '../auditedView'
import { MarkCase } from './MarkCase'

/** En la consola web el bot habla por el navegador: su pista la reconstruye el TTS del servidor. */
const TRACK_NAME: Record<string, string> = {
  mix: 'Conversación completa',
  client: 'Solo el vecino',
  assistant: 'Solo el asistente',
}

const TONE_COLOR = { good: 'green', review: 'orange', neutral: 'default' } as const

type Filter = 'todas' | 'review' | 'resolved'

function ResultTag({ e }: { e: Pick<CallRecord, 'result' | 'state'> }) {
  const r = result(e.result)
  if (!r) return <Tag color="processing">{e.state === 'active' ? 'En curso' : 'Sin resultado'}</Tag>
  return <Tag color={TONE_COLOR[r.tone]}>{r.short}</Tag>
}

function describeLatency(ms: number | null): string {
  if (ms == null) return 'aún sin llamadas por voz'
  return `${rate(ms).name} · objetivo ≤ 1200 ms`
}

const time = (iso: string) =>
  new Date(iso).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

interface Props {
  summary: OperationsSummary | null
  callRecords: CallRecord[]
  selected: CallRecordDetail | null
  onOpen: (id: string) => void
  catalog: Catalog
  /** Only the owner erases calls */
  canErase: boolean
  onErased: () => void
}

export function History({ summary, callRecords, selected, onOpen, catalog, canErase, onErased }: Props) {
  const [filter, setFilter] = useState<Filter>('todas')
  const items = useMemo(
    () =>
      callRecords.filter((e) =>
        filter === 'review' ? e.needs_review : filter === 'resolved' ? e.result === 'incident_created' : true,
      ),
    [callRecords, filter],
  )
  const p50 = summary?.global_latency.p50 ?? null

  return (
    <>
      <section className="ops-metrics" aria-label="Indicadores">
        <article>
          <p>Llamadas</p>
          <strong>{summary?.calls ?? 0}</strong>
          <span>{summary ? `${summary.active_calls} en curso` : '—'}</span>
        </article>
        <article>
          <p>Con incidencia creada</p>
          <strong>{summary ? `${summary.resolved}/${summary.calls}` : '—'}</strong>
          <span>resueltas por el asistente sin intervención</span>
        </article>
        <article>
          <p>A revisar</p>
          <strong className={summary?.need_review ? 'is-review' : ''}>{summary?.need_review ?? 0}</strong>
          <span>necesitan que una persona las mire</span>
        </article>
        <article>
          <p>Tiempo de respuesta (mediana)</p>
          <strong>{p50 == null ? '—' : `${p50} ms`}</strong>
          <span>{describeLatency(p50)}</span>
        </article>
      </section>

      <section className="history">
        <aside className="history-items" aria-label="Llamadas">
          <Segmented<Filter>
            block
            size="small"
            value={filter}
            onChange={setFilter}
            options={[
              { label: 'Todas', value: 'todas' },
              { label: 'A revisar', value: 'review' },
              { label: 'Resueltas', value: 'resolved' },
            ]}
          />
          {items.length === 0 && <Empty className="history-empty" description={callRecords.length ? 'Ninguna llamada con este filtro' : 'Todavía no hay llamadas. Simula una desde «Simular llamada».'} />}
          {items.map((e) => (
            <button
              key={e.id}
              type="button"
              className={`history-row ${selected?.call.id === e.id ? 'is-selected' : ''}`}
              onClick={() => onOpen(e.id)}
            >
              <span className="history-row-head">
                <b>{e.caller_phone ?? 'Número oculto'}</b>
                <time>{time(e.started_at)}</time>
              </span>
              <small>{catalog.estateAddress(e.estate_id) ?? 'Finca sin identificar'}</small>
              <ResultTag e={e} />
            </button>
          ))}
        </aside>

        <section className="history-detail" aria-label="Detalle de la llamada">
          {selected ? (
            <Detail
              d={selected}
              catalog={catalog}
              onReload={() => onOpen(selected.call.id)}
              canErase={canErase}
              onErased={onErased}
            />
          ) : (
            <Empty description="Elige una llamada para ver la conversación y lo que hizo el asistente" />
          )}
        </section>
      </section>
    </>
  )
}

function Detail({
  d,
  catalog,
  onReload,
  canErase,
  onErased,
}: {
  d: CallRecordDetail
  catalog: Catalog
  onReload: () => void
  canErase: boolean
  onErased: () => void
}) {
  const call = d.call
  // How long the call record stays on screen goes to the audit log
  useAuditedView('calls', call.id)
  const [erasing, setErasing] = useState(false)
  const r = result(call.result)
  // Turno que se está marcando como caso del banco de pruebas (y el caso previo, si se corrige)
  const [marking, setMarking] = useState<{ turn: number; previous?: BankCase } | null>(null)
  const casesOf = (turn: number) => (d.cases ?? []).filter((c) => c.turn === turn)
  const previousQuestion = (turn: number) => d.turns.find((t) => t.turn === turn - 1)?.assistant.at(-1) ?? null
  const markedTurn = marking ? d.turns.find((t) => t.turn === marking.turn) : undefined

  async function quitar(testCase: BankCase) {
    try {
      await operations.unmarkCase(testCase.id)
      message.success('Caso quitado del banco de pruebas')
      onReload()
    } catch (e) {
      message.error(e instanceof ApiError ? e.detail : 'No se ha podido quitar el caso.')
    }
  }

  async function erase() {
    setErasing(true)
    try {
      await operations.erase(call.id)
      message.success('Llamada eliminada')
      onErased()
    } catch (e) {
      message.error(e instanceof ApiError ? e.detail : 'No se ha podido eliminar la llamada.')
    } finally {
      setErasing(false)
    }
  }
  return (
    <div className="history-dossier">
      <header className="history-head">
        <div>
          <p className="ops-eyebrow">Llamada del {new Date(call.started_at).toLocaleString('es-ES')}</p>
          <h2>{call.caller_phone ?? 'Número oculto'}</h2>
        </div>
        <div className="history-head-actions">
          <ResultTag e={call} />
          {canErase && call.state === 'finished' && (
            <Popconfirm
              title="¿Eliminar esta llamada?"
              description="Se borran la conversación, el audio, el teléfono y los casos marcados. Sus incidencias se quedan, sin descripción. No se puede deshacer."
              okText="Eliminar"
              okButtonProps={{ danger: true }}
              cancelText="Cancelar"
              onConfirm={erase}
            >
              <Button danger size="small" loading={erasing}>
                Eliminar llamada
              </Button>
            </Popconfirm>
          )}
        </div>
      </header>

      {r && <p className={`history-explanation is-${r.tone}`}>{r.long}</p>}

      <dl className="ops-facts">
        <div>
          <dt>Finca</dt>
          <dd>{catalog.estateAddress(call.estate_id) ?? 'No identificada'}</dd>
        </div>
        <div>
          <dt>Vivienda</dt>
          <dd>{catalog.homeLabel(call.home_id) ?? 'No identificada'}</dd>
        </div>
        <div>
          <dt>Teléfono del vecino</dt>
          <dd>{call.caller_phone ?? 'No disponible (simulación)'}</dd>
        </div>
        <div>
          <dt>Motivo de revisión</dt>
          <dd>{call.review_reason ? capitalize(call.review_reason) : '—'}</dd>
        </div>
      </dl>

      <div className="ops-summary">
        <p className="ops-eyebrow">Resumen</p>
        <p>{call.summary ?? '—'}</p>
      </div>

      {d.tracks.length > 0 && (
        <div className="history-block">
          <h3>Grabación</h3>
          <small className="history-note">Enlace temporal (5 min). Se borra a los 14 días.</small>
          {d.tracks.map((p) => (
            <div key={p.id} className="history-track">
              <small>{TRACK_NAME[p.channel] ?? p.channel}</small>
              {p.url ? <audio controls preload="none" src={p.url} /> : <small> · eliminada por retención</small>}
            </div>
          ))}
        </div>
      )}

      <div className="history-block history-conv">
        <h3>Conversación</h3>
        <small className="history-note">
          ¿El asistente no entendió bien al vecino? Marca ese turno como caso para el banco de pruebas: así no vuelve a fallar.
        </small>
        <div className="history-chat">
          {d.turns.map((t) => (
            <div key={t.turn} className="history-turn">
              {t.caller && <div className="bubble caller">{t.caller}</div>}
              {t.caller && (
                <div className="history-case">
                  {casesOf(t.turn).map((c) => (
                    <span key={c.id} className="history-case-brand">
                      <Tag color="purple">{c.bank === 'classification' ? 'Caso de clasificación' : 'Caso del banco'}</Tag>
                      <Button size="small" type="link" onClick={() => setMarking({ turn: t.turn, previous: c })}>
                        Corregir
                      </Button>
                      <Popconfirm title="¿Quitar este caso del banco de pruebas?" okText="Quitar" cancelText="No" onConfirm={() => void quitar(c)}>
                        <Button size="small" type="link" danger>
                          Quitar
                        </Button>
                      </Popconfirm>
                    </span>
                  ))}
                  <Button size="small" type="link" onClick={() => setMarking({ turn: t.turn })}>
                    Marcar como caso para el banco
                  </Button>
                </div>
              )}
              {t.assistant.length > 0 && <div className="bubble bot">{t.assistant.join(' ')}</div>}
            </div>
          ))}
        </div>
      </div>

      {marking && markedTurn && (
        <MarkCase
          key={`${marking.turn}-${marking.previous?.id ?? 'fresh'}`}
          callId={call.id}
          turn={markedTurn}
          question={previousQuestion(marking.turn)}
          previous={marking.previous}
          onClose={() => setMarking(null)}
          onSaved={() => {
            setMarking(null)
            onReload()
          }}
        />
      )}

      <Collapse
        size="small"
        ghost
        items={[
          {
            key: 'decisions',
            label: 'Decisiones internas del asistente (para el equipo)',
            children: (
              <ol className="history-decisions">
                {d.turns.map((t) => (
                  <li key={t.turn}>
                    <b>
                      Turno {t.turn}: {t.initial_node} → {t.final_node}
                    </b>
                    <small>
                      {t.timings?.server_ms != null ? `${t.timings.server_ms} ms servidor` : ''}
                      {t.timings?.interpretation_origin ? ` · ${t.timings.interpretation_origin}` : ''}
                    </small>
                    {d.decisions
                      .filter((x) => x.turn === t.turn)
                      .map((x, i) => (
                        <small key={i}>
                          ↳ {x.node}: {x.action}
                          {x.detail ? ` · ${x.detail}` : ''}
                        </small>
                      ))}
                  </li>
                ))}
              </ol>
            ),
          },
        ]}
      />
    </div>
  )
}
