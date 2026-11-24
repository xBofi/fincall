import { useEffect, useRef, useState } from 'react'
import { Button, Input, Space, Tag, Typography } from 'antd'
import { AudioOutlined, LoadingOutlined, SendOutlined, SoundOutlined } from '@ant-design/icons'
import type { Turn } from '../api'
import { t } from '../i18n'
import type { MessageKey } from '../i18n/messages/es'

export type VoiceState = 'idle' | 'awaiting_voice' | 'speaking' | 'processing' | 'respondiendo'

const LABEL: Record<VoiceState, { key: MessageKey; color: string; icon?: React.ReactNode }> = {
  idle: { key: 'conv.idle', color: 'default' },
  awaiting_voice: { key: 'conv.awaiting', color: 'processing', icon: <AudioOutlined /> },
  speaking: { key: 'conv.speaking', color: 'green', icon: <AudioOutlined /> },
  processing: { key: 'conv.processing', color: 'gold', icon: <LoadingOutlined /> },
  respondiendo: { key: 'conv.responding', color: 'blue', icon: <SoundOutlined /> },
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
    const typed = text.trim()
    if (!typed || busy || finished) return
    onSend(typed)
    setText('')
  }
  const label = LABEL[voiceState]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--color-rule)' }}>
        <Tag color={label.color} icon={label.icon}>
          {t(label.key)}
        </Tag>
        {handsFree && !finished && <Tag>{t('conv.handsFree')}</Tag>}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: 12 }}>
        {history.map((turn, i) => (
          <div key={i} className={`bubble ${turn.role}`} dir="auto">
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
              {turn.role === 'bot' ? t('conv.bot') : t('conv.caller')}
            </Typography.Text>
            {turn.text}
          </div>
        ))}
        {partial && (
          <div className="bubble caller" style={{ opacity: 0.6 }} dir="auto">
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
              {t('conv.transcribing')}
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
            title={t('conv.listen')}
          />
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onPressEnter={send}
            placeholder={finished ? t('conv.finished') : t('conv.placeholder')}
            dir="auto"
            disabled={busy || finished}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={send} loading={busy} disabled={finished}>
            {t('conv.send')}
          </Button>
        </Space.Compact>
      </div>
    </div>
  )
}
