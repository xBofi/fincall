import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Space, Spin, Tag, Tooltip, Typography, message } from 'antd'
import { ReloadOutlined, RobotOutlined, ThunderboltOutlined, UndoOutlined } from '@ant-design/icons'
import { ApiError, flujo } from '../api'
import type { AristaGrafo, Grafo, NodoGrafo, Plantilla, Prompts } from '../api'

const { Paragraph, Text } = Typography

// Disposición: una fila por etapa (la pista viene del manifiesto, no se reinventa aquí).
const ANCHO_NODO = 178
const ALTO_NODO = 54
const SEP_X = 26
const SEP_Y = 82
const MARGEN = 28

interface Caja { nodo: NodoGrafo; x: number; y: number }

function disponer(nodos: NodoGrafo[]): { cajas: Map<string, Caja>; ancho: number; alto: number } {
  const porEtapa = new Map<number, NodoGrafo[]>()
  for (const n of nodos) {
    const fila = porEtapa.get(n.etapa) ?? []
    fila.push(n)
    porEtapa.set(n.etapa, fila)
  }
  const etapas = [...porEtapa.keys()].sort((a, b) => a - b)
  const maxPorFila = Math.max(...etapas.map((e) => porEtapa.get(e)!.length))
  const ancho = MARGEN * 2 + maxPorFila * ANCHO_NODO + (maxPorFila - 1) * SEP_X
  const cajas = new Map<string, Caja>()
  etapas.forEach((etapa, fila) => {
    const enFila = porEtapa.get(etapa)!
    const anchoFila = enFila.length * ANCHO_NODO + (enFila.length - 1) * SEP_X
    const inicio = (ancho - anchoFila) / 2
    enFila.forEach((nodo, i) => {
      cajas.set(nodo.id, {
        nodo,
        x: inicio + i * (ANCHO_NODO + SEP_X),
        y: MARGEN + fila * (ALTO_NODO + SEP_Y),
      })
    })
  })
  const alto = MARGEN * 2 + etapas.length * (ALTO_NODO + SEP_Y)
  return { cajas, ancho, alto }
}

const COLOR_TIPO: Record<string, string> = {
  decision: 'var(--flujo-decision)',
  pregunta: 'var(--flujo-pregunta)',
  efecto: 'var(--flujo-efecto)',
  terminal: 'var(--flujo-terminal)',
}

const ETIQUETA_TIPO: Record<string, string> = {
  decision: 'Decide',
  pregunta: 'Pregunta',
  efecto: 'Efecto',
  terminal: 'Cierre',
}

/** Curva entre dos cajas. Una arista a sí mismo se dibuja como lazo a la derecha. */
function trazo(a: Caja, b: Caja | null): string {
  if (b === null) {
    const x = a.x + ANCHO_NODO
    const y = a.y + ALTO_NODO / 2
    return `M ${x} ${y - 10} C ${x + 46} ${y - 26}, ${x + 46} ${y + 26}, ${x} ${y + 10}`
  }
  const x1 = a.x + ANCHO_NODO / 2
  const y1 = a.y + ALTO_NODO
  const x2 = b.x + ANCHO_NODO / 2
  const y2 = b.y
  if (y2 < y1) {
    // Vuelta atrás (casi siempre hacia `siguiente_paso`): se rodea por la izquierda
    const desvio = Math.min(a.x, b.x) - 34
    return `M ${a.x} ${a.y + ALTO_NODO / 2} C ${desvio} ${a.y + ALTO_NODO / 2}, ${desvio} ${b.y + ALTO_NODO / 2}, ${b.x} ${b.y + ALTO_NODO / 2}`
  }
  const medio = (y1 + y2) / 2
  return `M ${x1} ${y1} C ${x1} ${medio}, ${x2} ${medio}, ${x2} ${y2}`
}

function EditorPlantilla({ plantilla, onCambio }: { plantilla: Plantilla; onCambio: () => void }) {
  const [texto, setTexto] = useState(plantilla.texto)
  const [problemas, setProblemas] = useState<string[]>([])
  const [guardando, setGuardando] = useState(false)

  useEffect(() => { setTexto(plantilla.texto); setProblemas([]) }, [plantilla.id, plantilla.texto])

  const sucio = texto.trim() !== plantilla.texto.trim()

  const guardar = async () => {
    setGuardando(true)
    try {
      await flujo.publicar(plantilla.id, texto)
      setProblemas([])
      message.success('Publicado. El bot ya lo dice así.')
      onCambio()
    } catch (e) {
      if (e instanceof ApiError) {
        // El backend devuelve la lista de problemas concretos, no un error genérico
        try {
          const d = JSON.parse(e.detalle) as { problemas?: string[] }
          setProblemas(d.problemas ?? [e.detalle])
        } catch { setProblemas([e.detalle]) }
      }
    } finally { setGuardando(false) }
  }

  const restaurar = async () => {
    setGuardando(true)
    try { await flujo.restaurar(plantilla.id); message.success('Restaurado el texto original'); onCambio() }
    finally { setGuardando(false) }
  }

  return (
    <div className="flujo-plantilla">
      <div className="flujo-plantilla-cab">
        <Text strong>{plantilla.descripcion}</Text>
        {plantilla.sobrescrita && <Tag color="gold">editada · v{plantilla.version}</Tag>}
      </div>
      <Input.TextArea
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        autoSize={{ minRows: 2, maxRows: 6 }}
        spellCheck
      />
      <div className="flujo-plantilla-pie">
        <Space size={4} wrap>
          {plantilla.variables.map((v) => (
            <Tooltip key={v} title="Dato que rellena el nodo. Tiene que seguir apareciendo.">
              <Tag className="flujo-var">{'{' + v + '}'}</Tag>
            </Tooltip>
          ))}
          {plantilla.pregunta && (
            <Tooltip title="Sin interrogante, la redacción natural se descarta y el vecino oye el texto plano.">
              <Tag color="blue">debe preguntar</Tag>
            </Tooltip>
          )}
        </Space>
        <Space size={6}>
          {plantilla.sobrescrita && (
            <Button size="small" icon={<UndoOutlined />} onClick={() => void restaurar()} disabled={guardando}>
              Original
            </Button>
          )}
          <Button size="small" type="primary" onClick={() => void guardar()} loading={guardando} disabled={!sucio}>
            Publicar
          </Button>
        </Space>
      </div>
      {problemas.length > 0 && (
        <Alert type="error" showIcon message={problemas.map((p) => <div key={p}>{p}</div>)} />
      )}
    </div>
  )
}

export function PanelFlujo({ esOwner }: { esOwner: boolean }) {
  const [grafo, setGrafo] = useState<Grafo | null>(null)
  const [plantillas, setPlantillas] = useState<Plantilla[]>([])
  const [prompts, setPrompts] = useState<Prompts | null>(null)
  const [seleccion, setSeleccion] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const cargar = useCallback(async () => {
    try {
      const [g, p, pr] = await Promise.all([flujo.grafo(), flujo.plantillas(), flujo.prompts()])
      setGrafo(g); setPlantillas(p); setPrompts(pr); setError(null)
      setSeleccion((s) => s ?? g.entrada)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo cargar el flujo')
    }
  }, [])

  useEffect(() => { void cargar() }, [cargar])

  const disposicion = useMemo(() => (grafo ? disponer(grafo.nodos) : null), [grafo])
  const nodo = useMemo(
    () => grafo?.nodos.find((n) => n.id === seleccion) ?? null,
    [grafo, seleccion],
  )
  const salidas = useMemo<AristaGrafo[]>(
    () => grafo?.aristas.filter((a) => a.origen === seleccion) ?? [],
    [grafo, seleccion],
  )
  const suyas = useMemo(
    () => plantillas.filter((p) => p.nodo === seleccion),
    [plantillas, seleccion],
  )
  const maxVeces = useMemo(
    () => Math.max(1, ...(grafo?.aristas.map((a) => a.veces) ?? [1])),
    [grafo],
  )

  if (error) return <Alert type="error" showIcon message={error} />
  if (!grafo || !disposicion) return <Spin tip="Leyendo el flujo…"><div style={{ minHeight: 200 }} /></Spin>

  const { cajas, ancho, alto } = disposicion

  return (
    <div className="flujo">
      <div className="flujo-lienzo">
        <div className="ops-panel-heading">
          <div>
            <p className="ops-eyebrow">Grafo del asistente · {grafo.nodos.length} bloques</p>
            <h2>Flujo de la llamada</h2>
          </div>
          <Space>
            <span className="ops-mono">grosor = veces recorrida</span>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => void cargar()} />
          </Space>
        </div>

        {grafo.violaciones.length > 0 && (
          <Alert
            type="warning"
            showIcon
            message="Hay transiciones que el manifiesto no declara"
            description={grafo.violaciones.map((v) => <div key={v} className="ops-mono">{v}</div>)}
          />
        )}

        <div className="flujo-scroll">
          <svg width={ancho} height={alto} role="img" aria-label="Grafo del flujo de llamada">
            <defs>
              <marker id="punta" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                <path d="M0,0 L7,3.5 L0,7 z" fill="var(--flujo-arista)" />
              </marker>
            </defs>
            {grafo.aristas.map((a) => {
              const origen = cajas.get(a.origen)
              if (!origen) return null
              const destino = a.destino === a.origen ? null : (cajas.get(a.destino) ?? null)
              if (a.destino !== a.origen && destino === null) return null // hacia `fin`
              const activa = a.origen === seleccion || a.destino === seleccion
              return (
                <path
                  key={`${a.origen}->${a.destino}`}
                  d={trazo(origen, destino)}
                  className={`flujo-arista ${activa ? 'is-activa' : ''} ${a.veces === 0 ? 'is-fria' : ''}`}
                  strokeWidth={1 + (a.veces / maxVeces) * 2.5}
                  markerEnd="url(#punta)"
                >
                  <title>{`${a.origen} → ${a.destino}\n${a.condicion}\nRecorrida ${a.veces} ${a.veces === 1 ? 'vez' : 'veces'}`}</title>
                </path>
              )
            })}
            {[...cajas.values()].map(({ nodo: n, x, y }) => (
              <g
                key={n.id}
                transform={`translate(${x},${y})`}
                className={`flujo-nodo ${seleccion === n.id ? 'is-sel' : ''}`}
                onClick={() => setSeleccion(n.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSeleccion(n.id) }}
              >
                <rect width={ANCHO_NODO} height={ALTO_NODO} rx={9} style={{ stroke: COLOR_TIPO[n.tipo] }} />
                <text className="flujo-nodo-titulo" x={11} y={22}>{n.titulo}</text>
                <text className="flujo-nodo-meta" x={11} y={40}>
                  {ETIQUETA_TIPO[n.tipo]}{n.usa_llm ? ' · modelo' : ''}
                </text>
                {n.usa_llm && <circle cx={ANCHO_NODO - 14} cy={16} r={4} fill="var(--flujo-llm)" />}
              </g>
            ))}
          </svg>
        </div>

        <div className="flujo-leyenda">
          {Object.entries(ETIQUETA_TIPO).map(([tipo, etiqueta]) => (
            <span key={tipo}><i style={{ background: COLOR_TIPO[tipo] }} />{etiqueta}</span>
          ))}
          <span><i style={{ background: 'var(--flujo-llm)', borderRadius: '50%' }} />Puede llamar al modelo</span>
        </div>
      </div>

      <aside className="flujo-detalle">
        {nodo === null ? <Empty description="Elige un bloque" /> : (
          <>
            <div className="ops-panel-heading">
              <div>
                <p className="ops-eyebrow">{ETIQUETA_TIPO[nodo.tipo]} · etapa {nodo.etapa}</p>
                <h2>{nodo.titulo}</h2>
              </div>
            </div>
            <Paragraph>{nodo.resumen}</Paragraph>

            {nodo.caminos_deterministas.length > 0 && (
              <Card size="small" title={<><ThunderboltOutlined /> Se resuelve sin modelo</>}>
                <Paragraph type="secondary" className="flujo-nota">
                  Estos casos no pasan por el LLM: son los turnos de ~0 ms. Editar un texto no
                  cambia cómo se entienden.
                </Paragraph>
                <Space size={4} wrap>
                  {nodo.caminos_deterministas.map((c) => <Tag key={c}>{c}</Tag>)}
                </Space>
              </Card>
            )}

            {nodo.efectos.length > 0 && (
              <Card size="small" title="Efectos">
                <ul className="flujo-lista">{nodo.efectos.map((e) => <li key={e}>{e}</li>)}</ul>
              </Card>
            )}

            <Card size="small" title="A dónde puede ir">
              <ul className="flujo-lista">
                {salidas.map((a) => (
                  <li key={a.destino}>
                    <button className="flujo-salto" onClick={() => cajas.has(a.destino) && setSeleccion(a.destino)}>
                      {grafo.nodos.find((n) => n.id === a.destino)?.titulo ?? 'Fin de la llamada'}
                    </button>
                    <span className="flujo-cond"> — {a.condicion}</span>
                    {a.veces > 0 && <Tag className="flujo-veces">{a.veces}</Tag>}
                  </li>
                ))}
              </ul>
            </Card>

            <Card size="small" title={`Lo que dice (${suyas.length})`}>
              {suyas.length === 0
                ? <Text type="secondary">Este bloque no le dice nada al vecino: solo decide.</Text>
                : esOwner
                  ? suyas.map((p) => <EditorPlantilla key={p.id} plantilla={p} onCambio={() => void cargar()} />)
                  : suyas.map((p) => (
                      <div key={p.id} className="flujo-plantilla">
                        <Text strong>{p.descripcion}</Text>
                        <Paragraph className="flujo-texto-plano">{p.texto}</Paragraph>
                      </div>
                    ))}
              {!esOwner && suyas.length > 0 && (
                <Text type="secondary">Solo el owner puede editar los textos.</Text>
              )}
            </Card>

            {prompts && (
              <Card size="small" title={<><RobotOutlined /> Prompts del sistema</>}>
                <Alert type="info" showIcon message={prompts.motivo} className="flujo-nota" />
                {prompts.prompts.map((p) => (
                  <details key={p.id} className="flujo-prompt">
                    <summary>{p.titulo} <Tag>{p.version}</Tag></summary>
                    <Paragraph type="secondary" className="flujo-nota">{p.cuando}</Paragraph>
                    <pre className="flujo-prompt-texto">{p.texto}</pre>
                    <ul className="flujo-lista">{p.contrato.map((c) => <li key={c}>{c}</li>)}</ul>
                  </details>
                ))}
              </Card>
            )}
          </>
        )}
      </aside>
    </div>
  )
}
