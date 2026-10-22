import { useMemo, useState } from 'react'
import { Button, Collapse, Empty, Popconfirm, Segmented, Tag, message } from 'antd'
import { ApiError, operaciones } from '../api'
import type { CasoBanco, DetalleExpediente, Expediente, ResumenOperaciones } from '../api'
import type { Catalogo } from '../catalogo'
import { calificar, capitalizar, resultado } from '../textos'
import { MarcarCaso } from './MarcarCaso'

/** En la consola web el bot habla por el navegador: su pista la reconstruye el TTS del servidor. */
const NOMBRE_PISTA: Record<string, string> = {
  mezcla: 'Conversación completa',
  cliente: 'Solo el vecino',
  asistente: 'Solo el asistente',
}

const COLOR_TONO = { bien: 'green', revisar: 'orange', neutro: 'default' } as const

type Filtro = 'todas' | 'revisar' | 'resueltas'

function EtiquetaResultado({ e }: { e: Pick<Expediente, 'resultado' | 'estado'> }) {
  const r = resultado(e.resultado)
  if (!r) return <Tag color="processing">{e.estado === 'activa' ? 'En curso' : 'Sin resultado'}</Tag>
  return <Tag color={COLOR_TONO[r.tono]}>{r.corto}</Tag>
}

function valorar(ms: number | null): string {
  if (ms == null) return 'aún sin llamadas por voz'
  return `${calificar(ms).nombre} · objetivo ≤ 1200 ms`
}

const hora = (iso: string) =>
  new Date(iso).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

interface Props {
  resumen: ResumenOperaciones | null
  expedientes: Expediente[]
  seleccionado: DetalleExpediente | null
  onAbrir: (id: string) => void
  catalogo: Catalogo
}

export function Historial({ resumen, expedientes, seleccionado, onAbrir, catalogo }: Props) {
  const [filtro, setFiltro] = useState<Filtro>('todas')
  const lista = useMemo(
    () =>
      expedientes.filter((e) =>
        filtro === 'revisar' ? e.requiere_revision : filtro === 'resueltas' ? e.resultado === 'incidencia_creada' : true,
      ),
    [expedientes, filtro],
  )
  const p50 = resumen?.latencia_global.p50 ?? null

  return (
    <>
      <section className="ops-metrics" aria-label="Indicadores">
        <article>
          <p>Llamadas</p>
          <strong>{resumen?.llamadas ?? 0}</strong>
          <span>{resumen ? `${resumen.activas} en curso` : '—'}</span>
        </article>
        <article>
          <p>Con incidencia creada</p>
          <strong>{resumen ? `${resumen.resueltas}/${resumen.llamadas}` : '—'}</strong>
          <span>resueltas por el asistente sin intervención</span>
        </article>
        <article>
          <p>A revisar</p>
          <strong className={resumen?.requieren_revision ? 'is-review' : ''}>{resumen?.requieren_revision ?? 0}</strong>
          <span>necesitan que una persona las mire</span>
        </article>
        <article>
          <p>Tiempo de respuesta (mediana)</p>
          <strong>{p50 == null ? '—' : `${p50} ms`}</strong>
          <span>{valorar(p50)}</span>
        </article>
      </section>

      <section className="hist">
        <aside className="hist-lista" aria-label="Llamadas">
          <Segmented<Filtro>
            block
            size="small"
            value={filtro}
            onChange={setFiltro}
            options={[
              { label: 'Todas', value: 'todas' },
              { label: 'A revisar', value: 'revisar' },
              { label: 'Resueltas', value: 'resueltas' },
            ]}
          />
          {lista.length === 0 && <Empty className="hist-vacio" description={expedientes.length ? 'Ninguna llamada con este filtro' : 'Todavía no hay llamadas. Simula una desde «Simular llamada».'} />}
          {lista.map((e) => (
            <button
              key={e.id}
              type="button"
              className={`hist-fila ${seleccionado?.llamada.id === e.id ? 'is-selected' : ''}`}
              onClick={() => onAbrir(e.id)}
            >
              <span className="hist-fila-cab">
                <b>{e.nombre_declarado ?? 'Vecino sin identificar'}</b>
                <time>{hora(e.iniciada_en)}</time>
              </span>
              <small>{catalogo.direccionFinca(e.finca_id) ?? 'Finca sin identificar'}</small>
              <EtiquetaResultado e={e} />
            </button>
          ))}
        </aside>

        <section className="hist-detalle" aria-label="Detalle de la llamada">
          {seleccionado ? (
            <Detalle d={seleccionado} catalogo={catalogo} onRecargar={() => onAbrir(seleccionado.llamada.id)} />
          ) : (
            <Empty description="Elige una llamada para ver la conversación y lo que hizo el asistente" />
          )}
        </section>
      </section>
    </>
  )
}

function Detalle({ d, catalogo, onRecargar }: { d: DetalleExpediente; catalogo: Catalogo; onRecargar: () => void }) {
  const ll = d.llamada
  const r = resultado(ll.resultado)
  // Turno que se está marcando como caso del banco de pruebas (y el caso previo, si se corrige)
  const [marcando, setMarcando] = useState<{ turno: number; previo?: CasoBanco } | null>(null)
  const casosDe = (turno: number) => (d.casos ?? []).filter((c) => c.turno === turno)
  const preguntaAntes = (turno: number) => d.turnos.find((t) => t.turno === turno - 1)?.asistente.at(-1) ?? null
  const turnoMarcado = marcando ? d.turnos.find((t) => t.turno === marcando.turno) : undefined

  async function quitar(caso: CasoBanco) {
    try {
      await operaciones.desmarcarCaso(caso.id)
      message.success('Caso quitado del banco de pruebas')
      onRecargar()
    } catch (e) {
      message.error(e instanceof ApiError ? e.detalle : 'No se ha podido quitar el caso.')
    }
  }
  return (
    <div className="hist-dossier">
      <header className="hist-cab">
        <div>
          <p className="ops-eyebrow">Llamada del {new Date(ll.iniciada_en).toLocaleString('es-ES')}</p>
          <h2>{ll.nombre_declarado ?? 'Vecino sin identificar'}</h2>
        </div>
        <EtiquetaResultado e={ll} />
      </header>

      {r && <p className={`hist-explicacion is-${r.tono}`}>{r.largo}</p>}

      <dl className="ops-facts">
        <div>
          <dt>Finca</dt>
          <dd>{catalogo.direccionFinca(ll.finca_id) ?? 'No identificada'}</dd>
        </div>
        <div>
          <dt>Vivienda</dt>
          <dd>{catalogo.etiquetaVivienda(ll.vivienda_id) ?? 'No identificada'}</dd>
        </div>
        <div>
          <dt>Teléfono del vecino</dt>
          <dd>{ll.telefono_llamante ?? 'No disponible (simulación)'}</dd>
        </div>
        <div>
          <dt>Motivo de revisión</dt>
          <dd>{ll.motivo_revision ? capitalizar(ll.motivo_revision) : '—'}</dd>
        </div>
      </dl>

      <div className="ops-summary">
        <p className="ops-eyebrow">Resumen</p>
        <p>{ll.resumen ?? '—'}</p>
      </div>

      {d.pistas.length > 0 && (
        <div className="hist-bloque">
          <h3>Grabación</h3>
          <small className="hist-nota">Enlace temporal (5 min). Se borra a los 14 días.</small>
          {d.pistas.map((p) => (
            <div key={p.id} className="hist-pista">
              <small>{NOMBRE_PISTA[p.canal] ?? p.canal}</small>
              {p.url ? <audio controls preload="none" src={p.url} /> : <small> · eliminada por retención</small>}
            </div>
          ))}
        </div>
      )}

      <div className="hist-bloque hist-conv">
        <h3>Conversación</h3>
        <small className="hist-nota">
          ¿El asistente no entendió bien al vecino? Marca ese turno como caso para el banco de pruebas: así no vuelve a fallar.
        </small>
        <div className="hist-chat">
          {d.turnos.map((t) => (
            <div key={t.turno} className="hist-turno">
              {t.vecino && <div className="burbuja vecino">{t.vecino}</div>}
              {t.vecino && (
                <div className="hist-caso">
                  {casosDe(t.turno).map((c) => (
                    <span key={c.id} className="hist-caso-marca">
                      <Tag color="purple">{c.banco === 'clasificacion' ? 'Caso de clasificación' : 'Caso del banco'}</Tag>
                      <Button size="small" type="link" onClick={() => setMarcando({ turno: t.turno, previo: c })}>
                        Corregir
                      </Button>
                      <Popconfirm title="¿Quitar este caso del banco de pruebas?" okText="Quitar" cancelText="No" onConfirm={() => void quitar(c)}>
                        <Button size="small" type="link" danger>
                          Quitar
                        </Button>
                      </Popconfirm>
                    </span>
                  ))}
                  <Button size="small" type="link" onClick={() => setMarcando({ turno: t.turno })}>
                    Marcar como caso para el banco
                  </Button>
                </div>
              )}
              {t.asistente.length > 0 && <div className="burbuja bot">{t.asistente.join(' ')}</div>}
            </div>
          ))}
        </div>
      </div>

      {marcando && turnoMarcado && (
        <MarcarCaso
          key={`${marcando.turno}-${marcando.previo?.id ?? 'nuevo'}`}
          llamadaId={ll.id}
          turno={turnoMarcado}
          pregunta={preguntaAntes(marcando.turno)}
          previo={marcando.previo}
          onCerrar={() => setMarcando(null)}
          onGuardado={() => {
            setMarcando(null)
            onRecargar()
          }}
        />
      )}

      <Collapse
        size="small"
        ghost
        items={[
          {
            key: 'decisiones',
            label: 'Decisiones internas del asistente (para el equipo)',
            children: (
              <ol className="hist-decisiones">
                {d.turnos.map((t) => (
                  <li key={t.turno}>
                    <b>
                      Turno {t.turno}: {t.nodo_inicial} → {t.nodo_final}
                    </b>
                    <small>
                      {t.tiempos?.servidor_ms != null ? `${t.tiempos.servidor_ms} ms servidor` : ''}
                      {t.tiempos?.origen_interpretacion ? ` · ${t.tiempos.origen_interpretacion}` : ''}
                    </small>
                    {d.decisiones
                      .filter((x) => x.turno === t.turno)
                      .map((x, i) => (
                        <small key={i}>
                          ↳ {x.nodo}: {x.accion}
                          {x.detalle ? ` · ${x.detalle}` : ''}
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
