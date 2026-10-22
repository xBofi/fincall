import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Space, Spin, Tag, Tooltip, Typography, message } from 'antd'
import { ReloadOutlined, RobotOutlined, ThunderboltOutlined, UndoOutlined } from '@ant-design/icons'
import { ApiError, flujo } from '../api'
import type { GraphEdge, Graph, Template, Prompts } from '../api'
import { layOut } from './graphLayout'

const { Paragraph, Text } = Typography

const TYPE_COLOR: Record<string, string> = {
  decision: 'var(--flujo-decision)',
  question: 'var(--flujo-pregunta)',
  effect: 'var(--flujo-efecto)',
  terminal: 'var(--flujo-terminal)',
}

const TYPE_LABEL: Record<string, string> = {
  decision: 'Decide',
  question: 'Pregunta',
  effect: 'Efecto',
  terminal: 'Cierre',
}

function TemplateEditor({ template, onUpdate }: { template: Template; onUpdate: () => void }) {
  const [text, setText] = useState(template.text)
  const [problems, setProblems] = useState<string[]>([])
  const [saving, setSaving] = useState(false)

  const dirty = text.trim() !== template.text.trim()

  const save = async () => {
    setSaving(true)
    try {
      await flujo.publish(template.id, text)
      setProblems([])
      message.success('Publicado. El bot ya lo dice así.')
      onUpdate()
    } catch (e) {
      if (e instanceof ApiError) {
        // El backend devuelve la lista de problemas concretos, no un error genérico
        try {
          const d = JSON.parse(e.detail) as { problems?: string[] }
          setProblems(d.problems ?? [e.detail])
        } catch { setProblems([e.detail]) }
      }
    } finally { setSaving(false) }
  }

  const restore = async () => {
    setSaving(true)
    try { await flujo.restore(template.id); message.success('Restaurado el texto original'); onUpdate() }
    finally { setSaving(false) }
  }

  return (
    <div className="flujo-plantilla">
      <div className="flujo-plantilla-cab">
        <Text strong>{template.description}</Text>
        {template.overridden && <Tag color="gold">editada · v{template.version}</Tag>}
      </div>
      <Input.TextArea
        value={text}
        onChange={(e) => setText(e.target.value)}
        autoSize={{ minRows: 2, maxRows: 6 }}
        spellCheck
      />
      <div className="flujo-plantilla-pie">
        <Space size={4} wrap>
          {template.variables.map((v) => (
            <Tooltip key={v} title="Dato que rellena el nodo. Tiene que seguir apareciendo.">
              <Tag className="flujo-var">{'{' + v + '}'}</Tag>
            </Tooltip>
          ))}
          {template.question && (
            <Tooltip title="Sin interrogante, la redacción natural se descarta y el vecino oye el texto plano.">
              <Tag color="blue">debe preguntar</Tag>
            </Tooltip>
          )}
        </Space>
        <Space size={6}>
          {template.overridden && (
            <Button size="small" icon={<UndoOutlined />} onClick={() => void restore()} disabled={saving}>
              Original
            </Button>
          )}
          <Button size="small" type="primary" onClick={() => void save()} loading={saving} disabled={!dirty}>
            Publicar
          </Button>
        </Space>
      </div>
      {problems.length > 0 && (
        <Alert type="error" showIcon message={problems.map((p) => <div key={p}>{p}</div>)} />
      )}
    </div>
  )
}

export function FlowPanel({ isOwner }: { isOwner: boolean }) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [templates, setTemplates] = useState<Template[]>([])
  const [prompts, setPrompts] = useState<Prompts | null>(null)
  const [selection, setSelection] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [g, p, pr] = await Promise.all([flujo.graph(), flujo.templates(), flujo.prompts()])
      setGraph(g); setTemplates(p); setPrompts(pr); setError(null)
      setSelection((s) => s ?? g.utterance)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo cargar el flujo')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const layout = useMemo(() => (graph ? layOut(graph) : null), [graph])
  const node = useMemo(
    () => graph?.nodes.find((n) => n.id === selection) ?? null,
    [graph, selection],
  )
  const exits = useMemo<GraphEdge[]>(
    () => graph?.edges.filter((a) => a.origin === selection) ?? [],
    [graph, selection],
  )
  const suyas = useMemo(
    () => templates.filter((p) => p.node === selection),
    [templates, selection],
  )
  const maxTimes = useMemo(
    () => Math.max(1, ...(graph?.edges.map((a) => a.times) ?? [1])),
    [graph],
  )

  if (error) return <Alert type="error" showIcon message={error} />
  if (!graph || !layout) return <Spin tip="Leyendo el flujo…"><div style={{ minHeight: 200 }} /></Spin>

  const { boxes, strokes, end, finals, repeated, exitsLabel, width, high } = layout

  return (
    <div className="flujo">
      <div className="flujo-lienzo">
        <div className="ops-panel-heading">
          <div>
            <p className="ops-eyebrow">Flujo del asistente · {graph.nodes.length} bloques</p>
            <h2>Flujo de la llamada</h2>
          </div>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()} title="Volver a leer el flujo" />
        </div>

        {graph.violations.length > 0 && (
          <Alert
            type="warning"
            showIcon
            message="Hay transiciones que el manifiesto no declara"
            description={graph.violations.map((v) => <div key={v} className="ops-mono">{v}</div>)}
          />
        )}

        <div className="flujo-scroll">
          <svg viewBox={`0 0 ${width} ${high}`} style={{ width: `min(100%, ${Math.round(width * 1.3)}px)`, minWidth: width }} role="img" aria-label="Grafo del flujo de llamada">
            <defs>
              {(['normal', 'active'] as const).map((t) => (
                <marker key={t} id={`punta-${t}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
                  <path d="M0,1 L9,5 L0,9 z" fill={t === 'active' ? 'var(--color-accent)' : 'var(--flujo-arista)'} />
                </marker>
              ))}
            </defs>

            {exitsLabel && (
              <text className="flujo-rotulo" x={exitsLabel.x} y={exitsLabel.y}>
                Desde cualquier pregunta
              </text>
            )}

            {strokes.map((t) => {
              const active = selection !== null && (t.origin === selection || t.target === selection)
              if (t.activeOnly && !active) return null
              const punta = `url(#punta-${active ? 'active' : 'normal'})`
              return (
                <path
                  key={t.key}
                  d={t.d}
                  className={`flujo-arista ${active ? 'is-activa' : ''} ${t.times === 0 ? 'is-fria' : ''}`}
                  strokeWidth={1.25 + (t.times / maxTimes) * 2.25}
                  markerEnd={t.final ? punta : undefined}
                  markerStart={t.start ? punta : undefined}
                >
                  <title>{t.title}</title>
                </path>
              )
            })}

            {finals.map((f) => (
              <g key={`fin-${f.origin}`} className={`flujo-fin-marca ${f.origin === selection ? 'is-activa' : ''}`}>
                <path d={`M ${f.x - 16} ${f.y} L ${f.x - 5} ${f.y}`} />
                <circle cx={f.x} cy={f.y} r={4.5} />
                <title>Termina la llamada</title>
              </g>
            ))}

            {end && (
              <g className="flujo-fin" transform={`translate(${end.x - 66},${end.y - 11})`}>
                <rect width={132} height={24} rx={12} />
                <text x={66} y={16} textAnchor="middle">Fin de la llamada</text>
              </g>
            )}

            {[...boxes.values()].map((c) => {
              const n = c.node
              return (
                <g
                  key={n.id}
                  transform={`translate(${c.x},${c.y})`}
                  className={`flujo-nodo ${c.column === 'axis' ? 'is-eje' : ''} ${selection === n.id ? 'is-sel' : ''}`}
                  onClick={() => setSelection(n.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelection(n.id) }}
                >
                  <rect width={c.width} height={c.high} rx={9} style={{ stroke: TYPE_COLOR[n.kind] }} />
                  <text className="flujo-nodo-titulo" x={12} y={21}>{n.title}</text>
                  <text className="flujo-nodo-meta" x={12} y={38}>
                    {TYPE_LABEL[n.kind]}
                    {n.uses_llm ? ' · modelo' : ''}
                    {c.column === 'output' ? ` · desde ${c.origins} pasos` : ''}
                  </text>
                  {c.column === 'axis' && (
                    <text className="flujo-eje-texto" x={12} y={64}>
                      {['Reparte las preguntas:', 'salta a lo primero que', 'aún falte. Todos los', 'pasos vuelven aquí.'].map((l, i) => (
                        <tspan key={l} x={12} dy={i === 0 ? 0 : 16}>{l}</tspan>
                      ))}
                    </text>
                  )}
                  {repeated.has(n.id) && (
                    // En la línea de la meta, que siempre es corta: en la del título lo pisan los nombres largos.
                    <text className="flujo-repite" x={c.width - 14} y={39} textAnchor="middle">
                      ↻<title>Puede repetir la pregunta si no entiende la respuesta</title>
                    </text>
                  )}
                  {n.uses_llm && <circle cx={c.width - 12} cy={15} r={4} fill="var(--flujo-llm)" />}
                </g>
              )
            })}
          </svg>
        </div>

        <div className="flujo-leyenda">
          {Object.entries(TYPE_LABEL).map(([kind, label]) => (
            <span key={kind}><i style={{ background: TYPE_COLOR[kind] }} />{label}</span>
          ))}
          <span><i style={{ background: 'var(--flujo-llm)', borderRadius: '50%' }} />Puede llamar al modelo</span>
          <span>↻ Puede repetir la pregunta</span>
          <span><i className="flujo-leyenda-fin" />Termina la llamada</span>
          <span>Grosor = veces recorrida · punteado = aún no recorrida</span>
        </div>
      </div>

      <aside className="flujo-detalle">
        {node === null ? <Empty description="Elige un bloque" /> : (
          <>
            <div className="ops-panel-heading">
              <div>
                <p className="ops-eyebrow">{TYPE_LABEL[node.kind]} · etapa {node.stage}</p>
                <h2>{node.title}</h2>
              </div>
            </div>
            <Paragraph>{node.summary}</Paragraph>

            {node.deterministic_paths.length > 0 && (
              <Card size="small" title={<><ThunderboltOutlined /> Se resuelve sin modelo</>}>
                <Paragraph type="secondary" className="flujo-nota">
                  Estos casos no necesitan el modelo de lenguaje: el asistente responde al instante.
                  Editar un texto no cambia cómo se entienden.
                </Paragraph>
                <Space size={4} wrap>
                  {node.deterministic_paths.map((c) => <Tag key={c}>{c}</Tag>)}
                </Space>
              </Card>
            )}

            {node.effects.length > 0 && (
              <Card size="small" title="Efectos">
                <ul className="flujo-lista">{node.effects.map((e) => <li key={e}>{e}</li>)}</ul>
              </Card>
            )}

            <Card size="small" title="A dónde puede ir">
              <ul className="flujo-lista">
                {exits.map((a) => (
                  <li key={a.target}>
                    <button className="flujo-salto" onClick={() => boxes.has(a.target) && setSelection(a.target)}>
                      {graph.nodes.find((n) => n.id === a.target)?.title ?? 'Fin de la llamada'}
                    </button>
                    <span className="flujo-cond"> — {a.condition}</span>
                    {a.times > 0 && <Tag className="flujo-veces">{a.times}</Tag>}
                  </li>
                ))}
              </ul>
            </Card>

            <Card size="small" title={`Lo que dice (${suyas.length})`}>
              {suyas.length === 0
                ? <Text type="secondary">Este bloque no le dice nada al vecino: solo decide.</Text>
                : isOwner
                  ? suyas.map((p) => <TemplateEditor key={`${p.id}:${p.version}`} template={p} onUpdate={() => void load()} />)
                  : suyas.map((p) => (
                      <div key={p.id} className="flujo-plantilla">
                        <Text strong>{p.description}</Text>
                        <Paragraph className="flujo-texto-plano">{p.text}</Paragraph>
                      </div>
                    ))}
              {!isOwner && suyas.length > 0 && (
                <Text type="secondary">Solo el propietario puede editar los textos.</Text>
              )}
            </Card>

            {prompts && (
              <Card size="small" title={<><RobotOutlined /> Instrucciones del modelo de lenguaje</>}>
                <Alert type="info" showIcon message={prompts.reason} className="flujo-nota" />
                {prompts.prompts.map((p) => (
                  <details key={p.id} className="flujo-prompt">
                    <summary>{p.title} <Tag>{p.version}</Tag></summary>
                    <Paragraph type="secondary" className="flujo-nota">{p.when}</Paragraph>
                    <pre className="flujo-prompt-texto">{p.text}</pre>
                    <ul className="flujo-lista">{p.contract.map((c) => <li key={c}>{c}</li>)}</ul>
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
