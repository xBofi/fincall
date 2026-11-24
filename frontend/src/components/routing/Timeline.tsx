import { Button, Segmented, Tooltip } from 'antd'
import {
  CaretRightOutlined,
  PauseOutlined,
  StepBackwardOutlined,
  StepForwardOutlined,
  VerticalLeftOutlined,
  VerticalRightOutlined,
} from '@ant-design/icons'
import type { GraphStructure } from './types'
import type { TraceModel } from './trace'
import { t } from '../../i18n'

export interface TimelineProps {
  model: TraceModel
  structure: GraphStructure
  cursor: number
  onCursor: (i: number) => void
  playing: boolean
  onPlay: (play: boolean) => void
  speed?: number
  onSpeed?: (s: number) => void
  rtl?: boolean
  extra?: React.ReactNode
}

/** Línea de tiempo por pasos, agrupada por turno. Se maneja con teclado (toolbar). */
export function Timeline({ model, structure, cursor, onCursor, playing, onPlay, speed, onSpeed, rtl, extra }: TimelineProps) {
  const n = model.steps.length
  const last = n - 1
  const at = Math.min(cursor, last)
  const role = (id: string) => structure.nodes.find((x) => x.id === id)?.role ?? 'router'
  const title = (id: string) => structure.nodes.find((x) => x.id === id)?.title ?? id

  const onKey = (e: React.KeyboardEvent) => {
    const back = rtl ? 'ArrowRight' : 'ArrowLeft'
    const fwd = rtl ? 'ArrowLeft' : 'ArrowRight'
    if (e.key === back) onCursor(Math.max(0, at - 1))
    else if (e.key === fwd) onCursor(Math.min(last, at + 1))
    else if (e.key === 'Home') onCursor(0)
    else if (e.key === 'End') onCursor(last)
    else if (e.key === ' ' && (e.target as HTMLElement).tagName !== 'BUTTON') onPlay(!playing)
    else return
    e.preventDefault()
  }

  const groups: { turn: number; steps: number[] }[] = []
  model.steps.forEach((s, i) => {
    const g = groups[groups.length - 1]
    if (g && g.turn === s.turn) g.steps.push(i)
    else groups.push({ turn: s.turn, steps: [i] })
  })

  return (
    <div className="rv-timeline" role="toolbar" aria-label={t('routing.timeline.label')} onKeyDown={onKey}>
      <div className="rv-timeline-controls">
        <Button size="small" type="text" icon={<VerticalRightOutlined className="rv-flip" />} onClick={() => onCursor(0)} disabled={n === 0 || at <= 0} aria-label={t('routing.timeline.first')} />
        <Button size="small" type="text" icon={<StepBackwardOutlined className="rv-flip" />} onClick={() => onCursor(Math.max(0, at - 1))} disabled={n === 0 || at <= 0} aria-label={t('routing.timeline.prev')} />
        <Button
          size="small"
          type="primary"
          shape="circle"
          icon={playing ? <PauseOutlined /> : <CaretRightOutlined className="rv-flip" />}
          onClick={() => onPlay(!playing)}
          disabled={n === 0}
          aria-label={playing ? t('routing.timeline.pause') : t('routing.timeline.play')}
        />
        <Button size="small" type="text" icon={<StepForwardOutlined className="rv-flip" />} onClick={() => onCursor(Math.min(last, at + 1))} disabled={n === 0 || at >= last} aria-label={t('routing.timeline.next')} />
        <Button size="small" type="text" icon={<VerticalLeftOutlined className="rv-flip" />} onClick={() => onCursor(last)} disabled={n === 0 || at >= last} aria-label={t('routing.timeline.last')} />
        <span className="rv-timeline-pos rv-mono" aria-live="polite">
          {n === 0 ? t('routing.timeline.empty') : t('routing.timeline.step', { i: Math.max(at, 0) + 1, n })}
        </span>
        {onSpeed && speed !== undefined && (
          <Segmented
            size="small"
            aria-label={t('routing.demo.speed')}
            value={speed}
            onChange={(v) => onSpeed(Number(v))}
            options={[{ label: '0.5×', value: 0.5 }, { label: '1×', value: 1 }, { label: '2×', value: 2 }]}
          />
        )}
        {extra}
      </div>
      <div className="rv-ticks">
        {groups.map((g) => (
          <div key={g.turn} className="rv-tick-group">
            <span className="rv-tick-turn rv-mono">{g.turn}</span>
            <div className="rv-tick-row">
              {g.steps.map((i) => {
                const s = model.steps[i]
                const label = `${t('routing.timeline.turn', { turn: s.turn })} · ${title(s.visit.node)}`
                return (
                  <Tooltip key={s.key} title={label} mouseEnterDelay={0.2}>
                    <button
                      type="button"
                      className={`rv-tick rv-role-${role(s.visit.node)} ${i === at ? 'is-current' : i < at ? 'is-past' : ''} ${s.visit.llm_calls > 0 ? 'is-llm' : ''}`}
                      aria-label={label}
                      aria-current={i === at ? 'step' : undefined}
                      tabIndex={i === at ? 0 : -1}
                      onClick={() => onCursor(i)}
                    />
                  </Tooltip>
                )
              })}
            </div>
          </div>
        ))}
      </div>
      <small className="rv-timeline-keys">{t('routing.timeline.keys')}</small>
    </div>
  )
}
