import { useEffect, useRef, useState } from 'react'
import { Button, Input, Space, Tag, Typography } from 'antd'
import { AudioOutlined, LoadingOutlined, SendOutlined, SoundOutlined } from '@ant-design/icons'
import type { Turno } from '../api'

export type EstadoVoz = 'inactivo' | 'esperando_voz' | 'hablando' | 'procesando' | 'respondiendo'

const ETIQUETA: Record<EstadoVoz, { texto: string; color: string; icono?: React.ReactNode }> = {
  inactivo: { texto: 'En espera', color: 'default' },
  esperando_voz: { texto: 'Escuchando… hable cuando quiera', color: 'processing', icono: <AudioOutlined /> },
  hablando: { texto: 'Le oigo…', color: 'green', icono: <AudioOutlined /> },
  procesando: { texto: 'Pensando…', color: 'gold', icono: <LoadingOutlined /> },
  respondiendo: { texto: 'Asistente hablando (puede interrumpir)', color: 'blue', icono: <SoundOutlined /> },
}

interface Props {
  historial: Turno[]
  parcial: string | null
  estadoVoz: EstadoVoz
  ocupado: boolean
  finalizada: boolean
  manosLibres: boolean
  onEnviar: (texto: string) => void
  onEscuchar: () => void
}

export function Conversacion({ historial, parcial, estadoVoz, ocupado, finalizada, manosLibres, onEnviar, onEscuchar }: Props) {
  const [texto, setTexto] = useState('')
  const finRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    finRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [historial, parcial])

  const enviar = () => {
    const t = texto.trim()
    if (!t || ocupado || finalizada) return
    onEnviar(t)
    setTexto('')
  }
  const etiqueta = ETIQUETA[estadoVoz]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0' }}>
        <Tag color={etiqueta.color} icon={etiqueta.icono}>
          {etiqueta.texto}
        </Tag>
        {manosLibres && !finalizada && <Tag>manos libres</Tag>}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: 12 }}>
        {historial.map((t, i) => (
          <div key={i} className={`burbuja ${t.rol}`}>
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
              {t.rol === 'bot' ? 'Asistente' : 'Vecino'}
            </Typography.Text>
            {t.texto}
          </div>
        ))}
        {parcial && (
          <div className="burbuja vecino" style={{ opacity: 0.6 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
              Vecino (transcribiendo)
            </Typography.Text>
            {parcial}
          </div>
        )}
        <div ref={finRef} />
      </div>
      <div style={{ padding: 12, borderTop: '1px solid #f0f0f0' }}>
        <Space.Compact style={{ width: '100%' }}>
          <Button
            type={estadoVoz === 'esperando_voz' || estadoVoz === 'hablando' ? 'primary' : 'default'}
            icon={<AudioOutlined />}
            onClick={onEscuchar}
            disabled={ocupado || finalizada || estadoVoz !== 'inactivo'}
            title="Escuchar un turno"
          />
          <Input
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            onPressEnter={enviar}
            placeholder={finalizada ? 'Llamada finalizada' : 'O escriba lo que diría el vecino…'}
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
