import { useEffect, useRef, useState } from 'react'
import { Button, Input, Space, Tag, Typography } from 'antd'
import { AudioOutlined, LoadingOutlined, SendOutlined, SoundOutlined } from '@ant-design/icons'
import type { Turn } from '../api'

export type VoiceState = 'idle' | 'awaiting_voice' | 'speaking' | 'processing' | 'respondiendo'

const LABEL: Record<VoiceState, { text: string; color: string; icon?: React.ReactNode }> = {
  idle: { text: 'En espera', color: 'default' },
  awaiting_voice: { text: 'Escuchando… habla cuando quieras', color: 'processing', icon: <AudioOutlined /> },
  speaking: { text: 'Le oigo…', color: 'green', icon: <AudioOutlined /> },
  processing: { text: 'Pensando…', color: 'gold', icon: <LoadingOutlined /> },
  respondiendo: { text: 'Asistente hablando (puede interrumpir)', color: 'blue', icon: <SoundOutlined /> },
}

interface Props {
  history: Turn[]
  partial: string | null
  voiceState: VoiceState
  busy: boolean
  finished: boolean
  handsFree: boolean
  onSend: (text: string) => void
  onListen: () => void
}

export function Conversation({ history, partial, voiceState, busy, finished, handsFree, onSend, onListen }: Props) {
  const [text, setText] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, partial])

  const send = () => {
    const t = text.trim()
    if (!t || busy || finished) return
    onSend(t)
    setText('')
  }
  const label = LABEL[voiceState]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--color-rule)' }}>
        <Tag color={label.color} icon={label.icon}>
          {label.text}
        </Tag>
        {handsFree && !finished && <Tag>manos libres</Tag>}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: 12 }}>
        {history.map((t, i) => (
          <div key={i} className={`bubble ${t.role}`}>
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
              {t.role === 'bot' ? 'Asistente' : 'Vecino'}
            </Typography.Text>
            {t.text}
          </div>
        ))}
        {partial && (
          <div className="bubble caller" style={{ opacity: 0.6 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
              Vecino (transcribiendo)
            </Typography.Text>
            {partial}
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div style={{ padding: 12, borderTop: '1px solid var(--color-rule)' }}>
        <Space.Compact style={{ width: '100%' }}>
          <Button
            type={voiceState === 'awaiting_voice' || voiceState === 'speaking' ? 'primary' : 'default'}
            icon={<AudioOutlined />}
            onClick={onListen}
            disabled={busy || finished || voiceState !== 'idle'}
            title="Escuchar un turno"
          />
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onPressEnter={send}
            placeholder={finished ? 'Llamada finalizada' : 'O escribe lo que diría el vecino…'}
            disabled={busy || finished}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={send} loading={busy} disabled={finished}>
            Enviar
          </Button>
        </Space.Compact>
      </div>
    </div>
  )
}
