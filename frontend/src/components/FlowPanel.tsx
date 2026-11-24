import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Input, Space, Tag, Tooltip, Typography, message } from 'antd'
import { RobotOutlined, UndoOutlined } from '@ant-design/icons'
import { ApiError, flujo, routing, type CallView, type Prompts, type Template } from '../api'
import type { GraphStructure } from './routing/types'
import { RoutingView, type Mode } from './routing/RoutingView'

const { Paragraph, Text } = Typography

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
    <div className="flow-template">
      <div className="flow-template-head">
        <Text strong>{template.description}</Text>
        {template.overridden && <Tag color="gold">editada · v{template.version}</Tag>}
      </div>
      <Input.TextArea
        value={text}
        onChange={(e) => setText(e.target.value)}
        autoSize={{ minRows: 2, maxRows: 6 }}
        spellCheck
      />
      <div className="flow-template-footer">
        <Space size={4} wrap>
          {template.variables.map((v) => (
            <Tooltip key={v} title="Dato que rellena el nodo. Tiene que seguir apareciendo.">
              <Tag className="flow-var">{'{' + v + '}'}</Tag>
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

/**
 * Pantalla «Flujo»: la vista de enrutado de LangGraph (estructura, en directo, repetición y demo)
 * y, en el panel de cada nodo, lo que el asistente dice (plantillas editables por el owner) y
 * las instrucciones del modelo (solo lectura).
 */
export function FlowPanel({
  isOwner,
  liveView,
  initialMode,
}: {
  isOwner: boolean
  liveView?: CallView | null
  initialMode?: Mode
}) {
  const [structure, setStructure] = useState<GraphStructure | null>(null)
  const [templates, setTemplates] = useState<Template[]>([])
  const [prompts, setPrompts] = useState<Prompts | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [g, p, pr] = await Promise.all([routing.structure(), flujo.templates(), flujo.prompts()])
      setStructure(g); setTemplates(p); setPrompts(pr)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo cargar el flujo')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const nodeExtra = (nodeId: string) => {
    const own = templates.filter((p) => p.node === nodeId)
    if (nodeId.startsWith('__')) return null
    return (
      <>
        <Card size="small" title={`Lo que dice (${own.length})`}>
          {own.length === 0
            ? <Text type="secondary">Este bloque no le dice nada al vecino: solo decide.</Text>
            : isOwner
              ? own.map((p) => <TemplateEditor key={`${p.id}:${p.version}`} template={p} onUpdate={() => void load()} />)
              : own.map((p) => (
                  <div key={p.id} className="flow-template">
                    <Text strong>{p.description}</Text>
                    <Paragraph className="flow-text-plain">{p.text}</Paragraph>
                  </div>
                ))}
          {!isOwner && own.length > 0 && <Text type="secondary">Solo el propietario puede editar los textos.</Text>}
        </Card>
        {prompts && structure?.nodes.find((n) => n.id === nodeId)?.uses_llm && (
          <Card size="small" title={<><RobotOutlined /> Instrucciones del modelo de lenguaje</>}>
            <Alert type="info" showIcon message={prompts.reason} className="flow-note" />
            {prompts.prompts.map((p) => (
              <details key={p.id} className="flow-prompt">
                <summary>{p.title} <Tag>{p.version}</Tag></summary>
                <Paragraph type="secondary" className="flow-note">{p.when}</Paragraph>
                <pre className="flow-prompt-text">{p.text}</pre>
                <ul className="flow-items">{p.contract.map((c) => <li key={c}>{c}</li>)}</ul>
              </details>
            ))}
          </Card>
        )}
      </>
    )
  }

  return (
    <RoutingView
      structure={structure}
      error={error}
      onReload={() => void load()}
      live={{ callId: liveView?.call_id ?? null, view: liveView ?? null }}
      initialMode={initialMode ?? (liveView && !liveView.finished ? 'live' : 'structure')}
      nodeExtra={nodeExtra}
    />
  )
}
