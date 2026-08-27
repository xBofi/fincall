import { useEffect, useRef, useState } from 'react'
import { Button, Input, Space, Tag, Typography } from 'antd'
import { AudioOutlined, AudioMutedOutlined, SendOutlined } from '@ant-design/icons'
import type { Turno } from '../api'
import { escuchar, sttDisponible } from '../voz'

interface Props {
  historial: Turno[]
  ocupado: boolean
  finalizada: boolean
  onEnviar: (texto: string) => void
}

export function Conversacion({ historial, ocupado, finalizada, onEnviar }: Props) {
  const [texto, setTexto] = useState('')
  const [escuchando, setEscuchando] = useState(false)
  const [errorVoz, setErrorVoz] = useState<string | null>(null)
  const pararRef = useRef<(() => void) | null>(null)
  const finRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    finRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [historial])

  const enviar = () => {
    const t = texto.trim()
    if (!t || ocupado || finalizada) return
    onEnviar(t)
    setTexto('')
  }

  const alternarMicro = () => {
    if (escuchando) {
      pararRef.current?.()
      return
    }
    setErrorVoz(null)
    const parar = escuchar(
      (t) => {
        setTexto('')
        onEnviar(t)
      },
      (m) => setErrorVoz(m),
      () => setEscuchando(false),
    )
    if (parar) {
      pararRef.current = parar
      setEscuchando(true)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          padding: 12,
        }}
      >
        {historial.map((t, i) => (
          <div key={i} className={`burbuja ${t.rol}`}>
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
              {t.rol === 'bot' ? 'Asistente' : 'Vecino'}
            </Typography.Text>
            {t.texto}
          </div>
        ))}
        <div ref={finRef} />
      </div>
      <div style={{ padding: 12, borderTop: '1px solid #f0f0f0' }}>
        {errorVoz && (
          <Tag color="warning" style={{ marginBottom: 8 }}>
            Micrófono: {errorVoz}
          </Tag>
        )}
        <Space.Compact style={{ width: '100%' }}>
          <Button
            type={escuchando ? 'primary' : 'default'}
            danger={escuchando}
            icon={escuchando ? <AudioMutedOutlined /> : <AudioOutlined />}
            onClick={alternarMicro}
            disabled={!sttDisponible() || ocupado || finalizada}
            title={sttDisponible() ? 'Hablar' : 'Reconocimiento de voz no disponible en este navegador'}
          />
          <Input
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            onPressEnter={enviar}
            placeholder={finalizada ? 'Llamada finalizada' : 'Escriba lo que diría el vecino…'}
            disabled={ocupado || finalizada}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={enviar} loading={ocupado} disabled={finalizada}>
            Enviar
          </Button>
        </Space.Compact>
      </div>
    </div>
  )
}
