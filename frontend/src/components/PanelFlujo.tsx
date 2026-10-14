import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Space, Spin, Tag, Tooltip, Typography, message } from 'antd'
import { ReloadOutlined, RobotOutlined, ThunderboltOutlined, UndoOutlined } from '@ant-design/icons'
import { ApiError, flujo } from '../api'
import type { AristaGrafo, Grafo, Plantilla, Prompts } from '../api'
import { disponer } from './grafoDisposicion'

const { Paragraph, Text } = Typography

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

function EditorPlantilla({ plantilla, onCambio }: { plantilla: Plantilla; onCambio: () => void }) {
  const [texto, setTexto] = useState(plantilla.texto)
  const [problemas, setProblemas] = useState<string[]>([])
  const [guardando, setGuardando] = useState(false)

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

  const disposicion = useMemo(() => (grafo ? disponer(grafo) : null), [grafo])
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

  const { cajas, trazos, fin, finales, repiten, rotuloSalidas, ancho, alto } = disposicion

  return (
    <div className="flujo">
      <div className="flujo-lienzo">
        <div className="ops-panel-heading">
          <div>
            <p className="ops-eyebrow">Flujo del asistente · {grafo.nodos.length} bloques</p>
            <h2>Flujo de la llamada</h2>
          </div>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void cargar()} title="Volver a leer el flujo" />
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
          <svg viewBox={`0 0 ${ancho} ${alto}`} style={{ width: `min(100%, ${Math.round(ancho * 1.3)}px)`, minWidth: ancho }} role="img" aria-label="Grafo del flujo de llamada">
            <defs>
              {(['normal', 'activa'] as const).map((t) => (
                <marker key={t} id={`punta-${t}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
                  <path d="M0,1 L9,5 L0,9 z" fill={t === 'activa' ? 'var(--color-accent)' : 'var(--flujo-arista)'} />
                </marker>
              ))}
            </defs>

            {rotuloSalidas && (
              <text className="flujo-rotulo" x={rotuloSalidas.x} y={rotuloSalidas.y}>
                Desde cualquier pregunta
              </text>
            )}

            {trazos.map((t) => {
              const activa = seleccion !== null && (t.origen === seleccion || t.destino === seleccion)
              if (t.soloActiva && !activa) return null
              const punta = `url(#punta-${activa ? 'activa' : 'normal'})`
              return (
                <path
                  key={t.clave}
                  d={t.d}
                  className={`flujo-arista ${activa ? 'is-activa' : ''} ${t.veces === 0 ? 'is-fria' : ''}`}
                  strokeWidth={1.25 + (t.veces / maxVeces) * 2.25}
                  markerEnd={t.final ? punta : undefined}
                  markerStart={t.inicio ? punta : undefined}
                >
                  <title>{t.titulo}</title>
                </path>
              )
            })}

            {finales.map((f) => (
              <g key={`fin-${f.origen}`} className={`flujo-fin-marca ${f.origen === seleccion ? 'is-activa' : ''}`}>
                <path d={`M ${f.x - 16} ${f.y} L ${f.x - 5} ${f.y}`} />
                <circle cx={f.x} cy={f.y} r={4.5} />
                <title>Termina la llamada</title>
              </g>
            ))}

            {fin && (
              <g className="flujo-fin" transform={`translate(${fin.x - 66},${fin.y - 11})`}>
                <rect width={132} height={24} rx={12} />
                <text x={66} y={16} textAnchor="middle">Fin de la llamada</text>
              </g>
            )}

            {[...cajas.values()].map((c) => {
              const n = c.nodo
              return (
                <g
                  key={n.id}
                  transform={`translate(${c.x},${c.y})`}
                  className={`flujo-nodo ${c.columna === 'eje' ? 'is-eje' : ''} ${seleccion === n.id ? 'is-sel' : ''}`}
                  onClick={() => setSeleccion(n.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSeleccion(n.id) }}
                >
                  <rect width={c.ancho} height={c.alto} rx={9} style={{ stroke: COLOR_TIPO[n.tipo] }} />
                  <text className="flujo-nodo-titulo" x={12} y={21}>{n.titulo}</text>
                  <text className="flujo-nodo-meta" x={12} y={38}>
                    {ETIQUETA_TIPO[n.tipo]}
                    {n.usa_llm ? ' · modelo' : ''}
                    {c.columna === 'salida' ? ` · desde ${c.origenes} pasos` : ''}
                  </text>
                  {c.columna === 'eje' && (
                    <text className="flujo-eje-texto" x={12} y={64}>
                      {['Reparte las preguntas:', 'salta a lo primero que', 'aún falte. Todos los', 'pasos vuelven aquí.'].map((l, i) => (
                        <tspan key={l} x={12} dy={i === 0 ? 0 : 16}>{l}</tspan>
                      ))}
                    </text>
                  )}
                  {repiten.has(n.id) && (
                    // En la línea de la meta, que siempre es corta: en la del título lo pisan los nombres largos.
                    <text className="flujo-repite" x={c.ancho - 14} y={39} textAnchor="middle">
                      ↻<title>Puede repetir la pregunta si no entiende la respuesta</title>
                    </text>
                  )}
                  {n.usa_llm && <circle cx={c.ancho - 12} cy={15} r={4} fill="var(--flujo-llm)" />}
                </g>
              )
            })}
          </svg>
        </div>

        <div className="flujo-leyenda">
          {Object.entries(ETIQUETA_TIPO).map(([tipo, etiqueta]) => (
            <span key={tipo}><i style={{ background: COLOR_TIPO[tipo] }} />{etiqueta}</span>
          ))}
          <span><i style={{ background: 'var(--flujo-llm)', borderRadius: '50%' }} />Puede llamar al modelo</span>
          <span>↻ Puede repetir la pregunta</span>
          <span><i className="flujo-leyenda-fin" />Termina la llamada</span>
          <span>Grosor = veces recorrida · punteado = aún no recorrida</span>
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
                  Estos casos no necesitan el modelo de lenguaje: el asistente responde al instante.
                  Editar un texto no cambia cómo se entienden.
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
                  ? suyas.map((p) => <EditorPlantilla key={`${p.id}:${p.version}`} plantilla={p} onCambio={() => void cargar()} />)
                  : suyas.map((p) => (
                      <div key={p.id} className="flujo-plantilla">
                        <Text strong>{p.descripcion}</Text>
                        <Paragraph className="flujo-texto-plano">{p.texto}</Paragraph>
                      </div>
                    ))}
              {!esOwner && suyas.length > 0 && (
                <Text type="secondary">Solo el propietario puede editar los textos.</Text>
              )}
            </Card>

            {prompts && (
              <Card size="small" title={<><RobotOutlined /> Instrucciones del modelo de lenguaje</>}>
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
