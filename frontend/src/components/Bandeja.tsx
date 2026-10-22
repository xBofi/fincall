import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Empty, Input, Popconfirm, Segmented, Select, Spin, Tag, Timeline, message } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import {
  bandeja,
  type DetalleIncidencia,
  type EntradaHistorial,
  type EstadoManual,
  type FilaBandeja,
  type FiltrosBandeja,
} from '../api'
import type { Catalogo } from '../catalogo'
import { actuacion, categoria, estadoIncidencia, mensajeError, motivoRevision, urgencia } from '../textos'

type Vista = 'administrador' | 'abiertas' | 'todas'

const CERRADAS = new Set(['resuelta', 'cerrada'])
const ESTADOS_MANUALES: { valor: EstadoManual; texto: string }[] = [
  { valor: 'en_curso', texto: 'En curso' },
  { valor: 'esperando_informacion', texto: 'Esperando información' },
  { valor: 'resuelta', texto: 'Resuelta' },
  { valor: 'cerrada', texto: 'Cerrada' },
]

const hora = (iso: string) =>
  new Date(iso).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

function EtiquetaUrgencia({ u }: { u: string }) {
  const t = urgencia(u)
  return t ? <Tag color={t.color}>{t.texto}</Tag> : null
}

function EtiquetaEstado({ e }: { e: string }) {
  const t = estadoIncidencia(e)
  return <Tag color={t.color}>{t.texto}</Tag>
}

interface Props {
  catalogo: Catalogo
  /** Owner y administrador actúan; calidad solo mira */
  puedeActuar: boolean
  /** Para el contador de la pestaña */
  onPendientes?: (n: number) => void
}

/** Incidencias que esperan una decisión del administrador y lo que se hace con ellas. */
export function Bandeja({ catalogo, puedeActuar, onPendientes }: Props) {
  const [vista, setVista] = useState<Vista>('administrador')
  const [fincaId, setFincaId] = useState<string | undefined>()
  const [nivel, setNivel] = useState<string | undefined>()
  const [filas, setFilas] = useState<FilaBandeja[]>([])
  const [cargando, setCargando] = useState(true)
  const [detalle, setDetalle] = useState<DetalleIncidencia | null>(null)

  const cargar = useCallback(async () => {
    const filtros: FiltrosBandeja = {
      pendientes_administrador: vista === 'administrador',
      finca_id: fincaId,
      urgencia: nivel,
    }
    try {
      const [lista, pendientes] = await Promise.all([
        bandeja.listar(filtros),
        bandeja.listar({ pendientes_administrador: true }),
      ])
      setFilas(vista === 'abiertas' ? lista.filter((f) => !CERRADAS.has(f.estado)) : lista)
      onPendientes?.(pendientes.length)
    } catch (e) {
      message.error(mensajeError(e))
    } finally {
      setCargando(false)
    }
  }, [vista, fincaId, nivel, onPendientes])

  useEffect(() => {
    void cargar()
    const id = window.setInterval(() => {
      if (!document.hidden) void cargar()
    }, 20_000)
    return () => window.clearInterval(id)
  }, [cargar])

  const abrir = useCallback(async (id: string) => {
    try {
      setDetalle(await bandeja.detalle(id))
    } catch (e) {
      message.error(mensajeError(e))
    }
  }, [])

  const tras = useCallback(
    async (id: string) => {
      await Promise.all([cargar(), abrir(id)])
    },
    [cargar, abrir],
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
          <Segmented<Vista>
            block
            size="small"
            value={vista}
            onChange={setVista}
            options={[
              { label: 'Para decidir', value: 'administrador' },
              { label: 'Abiertas', value: 'abiertas' },
              { label: 'Todas', value: 'todas' },
            ]}
          />
          <div className="band-filtros">
            <Select
              allowClear
              size="small"
              placeholder="Todas las fincas"
              value={fincaId}
              onChange={setFincaId}
              options={catalogo.fincas.map((f) => ({ value: f.id, label: f.direccion }))}
            />
            <Select
              allowClear
              size="small"
              placeholder="Cualquier urgencia"
              value={nivel}
              onChange={setNivel}
              options={['emergencia', 'alta', 'media', 'baja'].map((u) => ({ value: u, label: urgencia(u)?.texto }))}
            />
          </div>
          {cargando && <Spin className="hist-vacio" />}
          {!cargando && filas.length === 0 && (
            <Empty
              className="hist-vacio"
              description={vista === 'administrador' ? 'Nada pendiente de decidir' : 'Ninguna incidencia con este filtro'}
            />
          )}
          {filas.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`hist-fila ${detalle?.incidencia.id === f.id ? 'is-selected' : ''} ${f.pendiente_administrador ? 'is-pendiente' : ''}`}
              onClick={() => void abrir(f.id)}
            >
              <span className="hist-fila-cab">
                <b>{categoria(f.categoria)}</b>
                <time>{hora(f.creada_en)}</time>
              </span>
              <small className="band-desc">{f.descripcion || 'Sin descripción'}</small>
              <small>
                {f.finca_direccion ?? 'Finca desconocida'}
                {f.vivienda ? ` · ${f.vivienda}` : ''}
              </small>
              <span className="band-tags">
                <EtiquetaUrgencia u={f.urgencia} />
                <EtiquetaEstado e={f.estado} />
                {f.pendiente_administrador ? (
                  <Tag color="orange">Para decidir</Tag>
                ) : (
                  f.especialista_nombre && <Tag>{f.especialista_nombre}</Tag>
                )}
              </span>
            </button>
          ))}
        </aside>

        <section className="hist-detalle" aria-label="Detalle de la incidencia">
          {detalle ? (
            <Detalle key={detalle.incidencia.id} d={detalle} puedeActuar={puedeActuar} onCambio={tras} />
          ) : (
            <Empty description="Elige una incidencia para ver la sugerencia, la llamada y decidir qué hacer" />
          )}
        </section>
      </section>
    </>
  )
}

function Detalle({
  d,
  puedeActuar,
  onCambio,
}: {
  d: DetalleIncidencia
  puedeActuar: boolean
  onCambio: (id: string) => Promise<void>
}) {
  const inc = d.incidencia
  const ll = d.llamada
  const motivo = motivoRevision(ll?.motivo_revision)
  const cerrada = inc.estado === 'cerrada'
  return (
    <div className="hist-dossier band-dossier">
      <header className="hist-cab">
        <div>
          <p className="ops-eyebrow">
            {categoria(inc.categoria)} · {actuacion(inc.actuacion)} · {hora(inc.creada_en)}
          </p>
          <h2>{inc.descripcion || 'Sin descripción'}</h2>
        </div>
        <span className="band-tags">
          <EtiquetaUrgencia u={inc.urgencia} />
          <EtiquetaEstado e={inc.estado} />
        </span>
      </header>

      {(inc.sugerencia || motivo) && (
        <div className={`band-sugerencia ${d.pendiente_administrador ? 'is-pendiente' : ''}`}>
          <p className="ops-eyebrow">
            <BulbOutlined /> {d.pendiente_administrador ? 'Te toca decidir' : 'Sugerencia del asistente'}
          </p>
          {motivo && <p className="band-motivo">{motivo}</p>}
          {inc.sugerencia && <p>{inc.sugerencia}</p>}
        </div>
      )}

      <dl className="ops-facts">
        <div>
          <dt>Finca</dt>
          <dd>{d.finca?.direccion ?? 'Desconocida'}</dd>
        </div>
        <div>
          <dt>Vivienda</dt>
          <dd>{d.vivienda ?? 'Desconocida'}</dd>
        </div>
        <div>
          <dt>Equipo afectado</dt>
          <dd>
            {d.equipo ? (
              <>
                {d.equipo.descripcion}
                {d.equipo.contrato ? ` · contrato ${d.equipo.contrato}` : ''}
                {d.equipo.garantia_hasta ? ` · garantía hasta ${d.equipo.garantia_hasta}` : ''}
              </>
            ) : (
              '—'
            )}
          </dd>
        </div>
        <div>
          <dt>Mantenedor del equipo</dt>
          <dd>{d.equipo?.mantenedor ? `${d.equipo.mantenedor.nombre} (${d.equipo.mantenedor.destino})` : '—'}</dd>
        </div>
        <div>
          <dt>Asignada a</dt>
          <dd>{d.especialista ? `${d.especialista.nombre} (${d.especialista.destino})` : 'Nadie todavía'}</dd>
        </div>
        <div>
          <dt>Vecino</dt>
          <dd>
            {ll?.nombre_declarado ?? 'Sin nombre'}
            {ll?.telefono_llamante ? ` · ${ll.telefono_llamante}` : ''}
          </dd>
        </div>
      </dl>

      {puedeActuar && !cerrada && <Acciones d={d} onCambio={onCambio} />}

      {ll?.resumen && (
        <div className="ops-summary">
          <p className="ops-eyebrow">Resumen de la llamada</p>
          <p>{ll.resumen}</p>
        </div>
      )}

      <div className="hist-bloque">
        <h3>Historial</h3>
        {d.historial.length === 0 ? (
          <small className="hist-nota">Nadie ha actuado todavía sobre esta incidencia.</small>
        ) : (
          <Timeline
            className="band-historial"
            items={d.historial.map((h) => ({ key: h.id, children: <EntradaLinea h={h} /> }))}
          />
        )}
      </div>

      {ll && ll.transcripcion.length > 0 && (
        <div className="hist-bloque hist-conv">
          <h3>Conversación</h3>
          <div className="hist-chat">
            {ll.transcripcion.map((t, i) => (
              <div key={i} className={`burbuja ${t.rol === 'bot' ? 'bot' : 'vecino'}`}>
                {t.texto}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function EntradaLinea({ h }: { h: EntradaHistorial }) {
  const quien = h.usuario_nombre ?? 'Alguien'
  let que: string
  if (h.accion === 'asignar') que = `asignó a ${String(h.detalle.especialista_nombre ?? h.detalle.especialista_id ?? '—')}`
  else if (h.accion === 'nota') que = 'añadió una nota'
  else que = `cambió el estado a «${estadoIncidencia(h.estado_nuevo).texto.toLowerCase()}»`
  return (
    <div className="band-entrada">
      <span>
        <b>{quien}</b> {que}
        <time> · {hora(h.cambiado_en)}</time>
      </span>
      {h.detalle.aviso === 'pendiente_envio' && <Tag color="gold">Aviso pendiente de envío</Tag>}
      {h.nota && <p>{h.nota}</p>}
    </div>
  )
}

function Acciones({ d, onCambio }: { d: DetalleIncidencia; onCambio: (id: string) => Promise<void> }) {
  const inc = d.incidencia
  const sugerido = d.equipo?.mantenedor?.id ?? inc.especialista_id ?? undefined
  const [especialista, setEspecialista] = useState<string | undefined>(sugerido)
  const [nota, setNota] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const asignable = !CERRADAS.has(inc.estado)

  const opciones = useMemo(
    () =>
      d.especialistas.map((e) => ({
        value: e.id,
        label: `${e.nombre} · ${categoria(e.categoria_principal)}${e.id === d.equipo?.mantenedor?.id ? ' · mantenedor' : ''}`,
      })),
    [d],
  )

  const ejecutar = async (fn: () => Promise<{ aviso?: string }>, ok: string) => {
    setOcupado(true)
    try {
      const r = await fn()
      message.success(r.aviso === 'pendiente_envio' ? `${ok}. El aviso queda pendiente de envío.` : ok)
      setNota('')
      await onCambio(inc.id)
    } catch (e) {
      message.error(mensajeError(e))
      // Si otro usuario actuó mientras tanto (409), se ve ya su decisión
      await onCambio(inc.id)
    } finally {
      setOcupado(false)
    }
  }

  return (
    <div className="hist-bloque band-acciones">
      <h3>Decidir</h3>
      {asignable && (
        <div className="band-accion">
          <label htmlFor="band-especialista">Asignar a un profesional</label>
          <div className="band-fila">
            <Select
              id="band-especialista"
              showSearch
              optionFilterProp="label"
              placeholder="Elige un profesional"
              value={especialista}
              onChange={setEspecialista}
              options={opciones}
            />
            <Button
              type="primary"
              disabled={!especialista || ocupado}
              loading={ocupado}
              onClick={() => void ejecutar(() => bandeja.asignar(inc.id, especialista!, nota.trim()), 'Incidencia asignada')}
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
          {ESTADOS_MANUALES.filter((e) => e.valor !== inc.estado).map((e) =>
            e.valor === 'cerrada' ? (
              <Popconfirm
                key={e.valor}
                title="¿Cerrar la incidencia?"
                description="Una incidencia cerrada ya no se puede modificar."
                okText="Cerrar"
                cancelText="Cancelar"
                onConfirm={() => void ejecutar(() => bandeja.estado(inc.id, e.valor, nota.trim()), 'Incidencia cerrada')}
              >
                <Button size="small" disabled={ocupado}>
                  {e.texto}
                </Button>
              </Popconfirm>
            ) : (
              <Button
                key={e.valor}
                size="small"
                disabled={ocupado}
                onClick={() => void ejecutar(() => bandeja.estado(inc.id, e.valor, nota.trim()), `Estado: ${e.texto.toLowerCase()}`)}
              >
                {e.texto}
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
          value={nota}
          onChange={(ev) => setNota(ev.target.value)}
          placeholder="Opcional al asignar o cambiar el estado; también se puede guardar sola"
        />
        <Button
          size="small"
          disabled={!nota.trim() || ocupado}
          onClick={() => void ejecutar(() => bandeja.nota(inc.id, nota.trim()), 'Nota guardada')}
        >
          Guardar solo la nota
        </Button>
      </div>
    </div>
  )
}
