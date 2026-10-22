import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Empty, Input, Segmented, Spin, Tag } from 'antd'
import {
  CheckCircleFilled,
  DownOutlined,
  MailOutlined,
  PhoneOutlined,
  ReloadOutlined,
  RightOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import type { EspecialistaCatalogo, FincaDetalle, ViviendaCatalogo } from '../api'
import { asignacion, categoriasDe, reglasDe, type Catalogo } from '../catalogo'
import { canal, capitalizar, categoria, destinoRegla, urgencia } from '../textos'

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

function Contacto({ canal: c, destino }: { canal: string; destino: string }) {
  return (
    <span className="bd-contacto">
      {c === 'llamada' ? <PhoneOutlined /> : <MailOutlined />}
      <span>{destino}</span>
    </span>
  )
}

function Viviendas({ viviendas, resaltada }: { viviendas: ViviendaCatalogo[]; resaltada?: string | null }) {
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
            {lista.map((v) => (
              <span key={v.id} className={`bd-chip ${v.id === resaltada ? 'is-match' : ''}`}>
                {v.piso}º {v.puerta}
              </span>
            ))}
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
}: {
  finca: FincaDetalle
  catalogo: Catalogo
  abierta: boolean
  onAlternar: () => void
  resaltado?: Resaltado
}) {
  const ref = useRef<HTMLElement>(null)
  const identificada = resaltado?.fincaId === finca.id
  const cfg = finca.configuracion_efectiva
  const porCategoria = asignacion(finca)
  const profesionalPorId = useMemo(
    () => Object.fromEntries(catalogo.profesionales.map((p) => [p.id, p])),
    [catalogo.profesionales],
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
          <dl className="bd-datos">
            <div>
              <dt>Teléfono</dt>
              <dd>{finca.telefono_oficial}</dd>
            </div>
            <div>
              <dt>Correo</dt>
              <dd>{finca.correo}</dd>
            </div>
            <div>
              <dt>Administrador</dt>
              <dd>{finca.administrador}</dd>
            </div>
          </dl>

          <section>
            <h4>Viviendas</h4>
            <Viviendas viviendas={finca.viviendas} resaltada={identificada ? resaltado?.viviendaId : null} />
          </section>

          {finca.equipos.length > 0 && (
            <section>
              <h4>Equipos y quién los mantiene</h4>
              <ul className="bd-filas">
                {finca.equipos.map((e) => {
                  const m = e.mantenedor_id ? profesionalPorId[e.mantenedor_id] : undefined
                  const condiciones = [e.contrato && `contrato ${e.contrato}`, e.garantia_hasta && `garantía hasta ${e.garantia_hasta}`]
                    .filter(Boolean)
                    .join(' · ')
                  return (
                    <li key={e.id}>
                      <span className="bd-fila-clave">{e.descripcion}</span>
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
            <h4>Quién atiende cada avería</h4>
            <ul className="bd-filas">
              {categoriasDe(finca).map((c) => {
                const p = porCategoria[c] ? profesionalPorId[porCategoria[c]] : undefined
                const activa = identificada && resaltado?.categoria === c
                return (
                  <li key={c} className={activa ? 'is-match' : ''}>
                    <span className="bd-fila-clave">{categoria(c)}</span>
                    {p ? (
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
              <li>
                <span className="bd-fila-clave">
                  <Tag color="red">Emergencia</Tag>
                </span>
                {cfg.emergencias_activas ? (
                  <span className="bd-fila-valor">
                    Indicación de seguridad y llamada al teléfono oficial de la finca (simulada por ahora)
                  </span>
                ) : (
                  <span className="bd-fila-valor is-muted">Se trata como urgencia alta</span>
                )}
              </li>
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
}: {
  p: EspecialistaCatalogo
  fincas: FincaDetalle[]
  resaltado?: Resaltado
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
            <span className="bd-fila-clave">Se le avisa por</span>
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

export function BaseDatos({ catalogo, resaltado, modo }: Props) {
  const [pestana, setPestana] = useState<Pestana>('fincas')
  const [consulta, setConsulta] = useState('')
  // Lo que el usuario abre o cierra a mano manda sobre lo que se abre solo
  const [manual, setManual] = useState<Record<string, boolean>>({})

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
        {modo === 'pagina' && (
          <Button icon={<ReloadOutlined />} onClick={catalogo.recargar} title="Volver a leer la base de datos" />
        )}
      </div>

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
              />
            ))}
          </div>
        ) : (
          <Empty description={hayBusqueda ? 'Ninguna finca coincide con la búsqueda' : 'No hay fincas dadas de alta'} />
        )
      ) : profesionales.length ? (
        <div className="bd-lista">
          {profesionales.map((p) => (
            <TarjetaProfesional key={p.id} p={p} fincas={catalogo.fincasDeProfesional[p.id] ?? []} resaltado={resaltado} />
          ))}
        </div>
      ) : (
        <Empty description={hayBusqueda ? 'Ningún profesional coincide con la búsqueda' : 'No hay profesionales dados de alta'} />
      )}
    </div>
  )
}
