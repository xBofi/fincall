import { useCallback, useEffect, useMemo, useState } from 'react'
import { RoutingErrorBoundary } from './RoutingErrorBoundary'
import { Alert, Badge, Button, Empty, Segmented, Select, Skeleton, Tabs } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { operations, type CallRecord, type CallView } from '../../api'
import { result as resultLabel } from '../../texts'
import type { DemoRecording, GraphStructure, Role, TurnRecord } from './types'
import { emptyTrace, firstStepOfTurn, fromDecisions, fromTurns, viewAt, type TraceModel } from './trace'
import { RoutingGraph } from './RoutingGraph'
import { ROLE_ICON } from './roleIcons'
import { ConversationPanel, DecisionPanel, NodePanel, StatePanel } from './Panels'
import { Timeline } from './Timeline'
import { useDocumentAttrs, useLiveTrace, usePlayback } from './hooks'
import { t } from '../../i18n'
import { localizeStructure, modeKey, roleKey, visitCondition } from './labels'
import './routing.css'

export type Mode = 'structure' | 'live' | 'replay' | 'demo'
type PanelTab = 'decision' | 'state' | 'conversation' | 'node'

const ROLES: Role[] = ['entry', 'router', 'llm', 'tool', 'human', 'end']

export interface RoutingViewProps {
  /** null mientras carga. En la demo autónoma se usa la estructura grabada. */
  structure: GraphStructure | null
  error?: string | null
  onReload?: () => void
  live?: { callId: string | null; view: CallView | null }
  modes?: Mode[]
  initialMode?: Mode
  /** Contenido extra del panel de nodo (plantillas editables en la consola). */
  nodeExtra?: (nodeId: string) => React.ReactNode
  /** Solo el lienzo y el último salto: para la columna de «Simular llamada». */
  compact?: boolean
}

const loadDemo = () => import('./demo/demoCall.json').then((m) => m.default as unknown as DemoRecording)

/** La vista entera va dentro de un límite de errores: un fallo enseña un aviso con «Reintentar»
 * en lugar de dejar la consola en blanco. */
export function RoutingView(props: RoutingViewProps) {
  return (
    <RoutingErrorBoundary compact={props.compact}>
      <RoutingViewInner {...props} />
    </RoutingErrorBoundary>
  )
}

function RoutingViewInner({
  structure: fetched,
  error,
  onReload,
  live,
  modes = ['structure', 'live', 'replay', 'demo'],
  initialMode,
  nodeExtra,
  compact = false,
}: RoutingViewProps) {
  const { dark, rtl, lang } = useDocumentAttrs()
  const [mode, setMode] = useState<Mode>(initialMode ?? (live?.callId ? 'live' : modes[0]))
  const [demo, setDemo] = useState<DemoRecording | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null)
  const [tab, setTab] = useState<PanelTab>('decision')
  const [follow, setFollow] = useState(true)

  // ------------------------------------------------------------------ fuentes de la traza
  const liveTrace = useLiveTrace(mode === 'live' ? live?.callId ?? null : null, live?.view ?? null)

  const [calls, setCalls] = useState<CallRecord[] | null>(null)
  const [replayId, setReplayId] = useState<string | null>(null)
  const [replay, setReplay] = useState<{ model: TraceModel; legacy: boolean } | null>(null)
  const [replayError, setReplayError] = useState<string | null>(null)

  useEffect(() => {
    if (mode === 'demo' && !demo) void loadDemo().then(setDemo)
    if (mode === 'replay' && calls === null) {
      operations.calls().then(setCalls).catch((e: Error) => { setCalls([]); setReplayError(e.message) })
    }
  }, [mode, demo, calls])

  useEffect(() => {
    if (!replayId) return
    let stale = false
    setReplay(null)
    operations.detail(replayId).then((d) => {
      if (stale) return
      const turns = d.turns as unknown as TurnRecord[]
      const hasVisits = turns.some((x) => (x.visits ?? []).length > 0)
      setReplay(hasVisits
        ? { model: fromTurns(turns), legacy: false }
        : { model: fromDecisions(turns, d.decisions), legacy: true })
    }).catch((e: Error) => setReplayError(e.message))
    return () => { stale = true }
  }, [replayId])

  const demoModel = useMemo(() => (demo ? fromTurns(demo.turns) : null), [demo])
  const rawStructure = fetched ?? (mode === 'demo' ? demo?.structure ?? null : null)
  // Títulos y condiciones en el idioma de la consola (el backend los da en el del código)
  const structure = useMemo(() => (rawStructure ? localizeStructure(rawStructure, lang) : null), [rawStructure, lang])

  const model: TraceModel | null =
    mode === 'live' ? liveTrace.model
      : mode === 'replay' ? replay?.model ?? null
        : mode === 'demo' ? demoModel
          : null
  const playback = usePlayback(model ?? emptyTrace())
  const { setCursor, setPlaying } = playback

  // Al cambiar de llamada o de modo: la demo arranca sola, la repetición enseña la llamada entera
  useEffect(() => {
    setPlaying(false)
    if (mode === 'demo' && demoModel) { setCursor(-1); setPlaying(true) }
    else if (mode === 'replay' && replay) setCursor(replay.model.steps.length - 1)
    else setCursor(-1)
    setFollow(true)
    setSelected(null)
    setTab(mode === 'structure' ? 'node' : 'decision')
  }, [mode, demoModel, replay, setCursor, setPlaying])

  const liveFollowing = mode === 'live' && follow
  const cursor = liveFollowing ? (model?.steps.length ?? 0) : playback.cursor
  const at = useMemo(() => (model ? viewAt(model, cursor) : null), [model, cursor])
  const running = liveFollowing ? model?.running?.node ?? null : null
  const stepIndex = model ? Math.min(cursor, model.steps.length - 1) : -1
  const step = model && stepIndex >= 0 ? model.steps[stepIndex] : null
  const turnInfo = step ? model?.turns.find((x) => x.turn === step.turn) ?? null : null

  const moveCursor = useCallback((i: number) => {
    setFollow(false)
    setPlaying(false)
    setCursor(i)
    setSelected(null)
    setTab((tb) => (tb === 'node' ? 'decision' : tb))
  }, [setCursor, setPlaying])

  const play = (p: boolean) => {
    if (!model) return
    setFollow(false)
    if (p && playback.cursor >= model.steps.length - 1) setCursor(-1)
    setPlaying(p)
  }

  const onSelect = useCallback((id: string | null) => {
    setSelected(id)
    setHoveredEdge(null)
    if (id) setTab('node')
  }, [])

  const selectedNode = structure?.nodes.find((n) => n.id === (selected ?? (mode === 'structure' ? structure.hub : at?.node))) ?? null

  // ------------------------------------------------------------------------------ render
  if (error) {
    return (
      <div className="rv" data-lang={lang}>
        <Alert type="error" showIcon message={t('routing.error', { error })} action={onReload && <Button size="small" onClick={onReload}>{t('routing.retry')}</Button>} />
      </div>
    )
  }

  const loadingCanvas = !structure || (mode === 'demo' && !demoModel)
  const canvas = loadingCanvas ? (
    <div className={`rv-canvas rv-canvas-loading ${compact ? 'is-compact' : ''}`} aria-busy="true">
      <Skeleton active paragraph={{ rows: 6 }} title={false} />
      <span className="rv-muted">{t('routing.loading')}</span>
    </div>
  ) : mode === 'live' && !live?.callId ? (
    <div className={`rv-canvas rv-canvas-empty ${compact ? 'is-compact' : ''}`}>
      <Empty description={<><b>{t('routing.live.empty.title')}</b><br />{t('routing.live.empty.body')}</>}>
        {modes.includes('demo') && <Button type="primary" onClick={() => setMode('demo')}>{t('routing.mode.demo')}</Button>}
      </Empty>
    </div>
  ) : (
    <RoutingGraph
      structure={structure}
      view={mode === 'structure' ? null : at}
      running={running}
      selected={selected}
      onSelect={onSelect}
      compact={compact}
      rtl={rtl}
      dark={dark}
      hoveredEdge={hoveredEdge}
    />
  )

  if (compact) {
    const v = step?.visit
    const title = (id: string) => structure?.nodes.find((n) => n.id === (id === 'end' ? structure.end : id))?.title ?? id
    return (
      <div className="rv is-compact" data-lang={lang}>
        {canvas}
        <p className="rv-compact-line" aria-live="polite">
          {running ? t('routing.decision.running', { node: title(running) })
            : v ? (
              v.target === v.node && v.awaiting_user
                ? <><b>{title(v.node)}</b> · {t('routing.node.waiting')}</>
                : <><b>{title(v.node)} → {title(v.target)}</b>{structure && visitCondition(structure, v) && <> · {visitCondition(structure, v)}</>}{v.llm_calls > 0 && <> · LLM {Math.round(v.llm_ms)} ms</>}</>
            )
              : t('routing.live.empty.title')}
        </p>
      </div>
    )
  }

  const statusBadge = mode === 'live' && live?.callId && (
    <Badge
      status={liveTrace.status === 'live' ? 'processing' : liveTrace.status === 'ended' ? 'default' : 'warning'}
      text={liveTrace.status === 'ended' ? t('routing.live.ended') : liveTrace.status === 'live' ? t('routing.live.connected') : t('routing.live.reconnecting')}
    />
  )

  const tabs = [
    { key: 'decision', label: t('routing.panel.decision'), disabled: mode === 'structure', children: structure && <DecisionPanel step={step} turn={turnInfo} structure={structure} running={running} /> },
    { key: 'state', label: t('routing.panel.state'), disabled: mode === 'structure', children: model && <StatePanel model={model} cursor={stepIndex} /> },
    {
      key: 'conversation', label: t('routing.panel.conversation'), disabled: mode === 'structure',
      children: model && <ConversationPanel turns={model.turns} current={step?.turn ?? null} onJump={(turn) => moveCursor(firstStepOfTurn(model, turn))} />,
    },
    { key: 'node', label: t('routing.panel.node'), children: structure && <NodePanel node={selectedNode} structure={structure} onSelect={onSelect} onHoverRoute={setHoveredEdge} extra={selectedNode && nodeExtra?.(selectedNode.id)} /> },
  ]

  return (
    <div className="rv" data-lang={lang}>
      <header className="rv-header">
        <div>
          <p className="rv-eyebrow">
            {structure ? t('routing.eyebrow', { graph: structure.graph, nodes: structure.nodes.length - 2, edges: structure.edges.length }) : ' '}
          </p>
          <h2>{t('routing.title')}</h2>
        </div>
        <div className="rv-header-tools">
          {statusBadge}
          {mode === 'live' && !follow && live?.callId && <Button size="small" onClick={() => setFollow(true)}>{t('routing.live.follow')}</Button>}
          {mode === 'replay' && (
            <Select
              className="rv-call-picker"
              size="small"
              placeholder={t('routing.replay.pick')}
              aria-label={t('routing.replay.pick')}
              loading={calls === null}
              value={replayId ?? undefined}
              onChange={setReplayId}
              notFoundContent={t('routing.replay.empty')}
              options={(calls ?? []).map((c) => ({ value: c.id, label: `${new Date(c.started_at).toLocaleString()} · ${c.result ? resultLabel(c.result)?.short ?? c.result : t('routing.replay.active')}` }))}
            />
          )}
          {modes.length > 1 && <Segmented
            aria-label={t('routing.mode.label')}
            value={mode}
            onChange={(v) => setMode(v as Mode)}
            options={modes.map((m) => ({ value: m, label: t(modeKey(m)) }))}
          />}
          {onReload && <Button size="small" icon={<ReloadOutlined />} onClick={onReload} aria-label={t('routing.reload')} title={t('routing.reload')} />}
        </div>
      </header>

      {structure && structure.drift.length > 0 && (
        <Alert type="error" showIcon message={t('routing.drift.title')} description={structure.drift.map((d) => <div key={d} className="rv-mono">{d}</div>)} />
      )}
      {structure && (structure.violations ?? []).length > 0 && (
        <Alert type="warning" showIcon message={t('routing.violations.title')} description={structure.violations!.map((d) => <div key={d} className="rv-mono">{d}</div>)} />
      )}
      {mode === 'replay' && replay?.legacy && <Alert type="info" showIcon message={t('routing.replay.legacy')} />}
      {mode === 'replay' && replayError && <Alert type="error" showIcon message={replayError} />}
      {mode === 'demo' && demo && (
        <p className="rv-note">{t('routing.demo.note', { llm: demo.meta.llm, date: demo.meta.recorded_at.slice(0, 10) })}</p>
      )}

      <div className="rv-body">
        <div className="rv-main">
          {canvas}
          <ul className="rv-legend" aria-label={t('routing.legend')}>
            {ROLES.map((r) => (
              <li key={r}><span className={`rv-legend-swatch rv-role-${r}`} aria-hidden>{ROLE_ICON[r]}</span>{t(roleKey(r))}</li>
            ))}
            <li><span aria-hidden className="rv-legend-retry">↻</span>{t('routing.legend.retry')}</li>
            {mode !== 'structure' && <li><i className="rv-legend-line is-turn" aria-hidden />{t('routing.legend.taken')}</li>}
            {mode !== 'structure' && <li><i className="rv-legend-line is-current" aria-hidden />{t('routing.legend.current')}</li>}
            {mode === 'structure' && <li>{t('routing.legend.width')}</li>}
            <li className="rv-muted">{t('routing.legend.hidden')}</li>
          </ul>
          {mode !== 'structure' && model && structure && (
            <Timeline
              model={model}
              structure={structure}
              cursor={liveFollowing ? model.steps.length - 1 : playback.cursor}
              onCursor={moveCursor}
              playing={playback.playing}
              onPlay={play}
              speed={mode === 'demo' || mode === 'replay' ? playback.speed : undefined}
              onSpeed={mode === 'demo' || mode === 'replay' ? playback.setSpeed : undefined}
              rtl={rtl}
            />
          )}
          {mode === 'replay' && !replayId && <p className="rv-muted rv-small">{t('routing.replay.pick')}</p>}
          {structure && <p className="rv-source rv-small">{t('routing.source', { compiled: structure.compiled.conditional_edges })}</p>}
        </div>
        <aside className="rv-side">
          <Tabs size="small" activeKey={tab} onChange={(k) => setTab(k as PanelTab)} items={tabs} />
        </aside>
      </div>
    </div>
  )
}
