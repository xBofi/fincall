import type { ReactNode } from 'react'
import { Collapse, Tag } from 'antd'
import { CheckCircleFilled, ExclamationCircleFilled, InfoCircleFilled, LoadingOutlined } from '@ant-design/icons'
import type { VistaLlamada } from '../api'
import { canal, categoria, resultado, urgencia } from '../textos'
import { PanelEstado } from './PanelEstado'

type Estado = 'hecho' | 'ahora' | 'pendiente' | 'omitido'

interface Paso {
  titulo: string
  valor: ReactNode | null
  /** Nodos del grafo que preguntan por este dato: si es el actual, el bot lo está preguntando. */
  nodos: string[]
  nota?: string | null
}

function pasos(v: VistaLlamada): Paso[] {
  const d = v.datos
  const u = urgencia(d.urgencia)
  // Con ticket, el destino es el propio profesional: no se repite su nombre
  const destino = d.destino_aviso && d.destino_aviso !== d.especialista_nombre ? ` a ${d.destino_aviso}` : ''
  const aviso = d.canal_aviso ? `${d.especialista_nombre ?? 'Administrador'} · por ${canal(d.canal_aviso)}${destino}` : null
  return [
    { titulo: 'Finca', valor: d.finca_direccion, nodos: ['identificar_finca', 'finca_no_identificada'], nota: d.intentos_finca ? `${d.intentos_finca} intento(s)` : null },
    { titulo: 'Vivienda', valor: d.vivienda_etiqueta, nodos: ['identificar_vivienda', 'vivienda_no_identificada'], nota: d.intentos_vivienda ? `${d.intentos_vivienda} intento(s)` : null },
    // El nombre no se pregunta (basta la vivienda): solo aparece si el vecino lo dijo
    ...(d.nombre_declarado ? [{ titulo: 'Nombre del vecino', valor: d.nombre_declarado, nodos: [] }] : []),
    { titulo: 'Qué le ocurre', valor: d.solicitud, nodos: ['recoger_solicitud', 'pedir_detalle'] },
    { titulo: 'Tipo de avería', valor: d.categoria ? categoria(d.categoria) : null, nodos: ['clasificar_problema'] },
    {
      titulo: 'Urgencia',
      valor: u ? (
        <>
          <Tag color={u.color}>{u.texto}</Tag>
          {d.urgencia_declarada && d.urgencia_declarada !== d.urgencia && (
            <small className="ficha-nota">el vecino dijo «{urgencia(d.urgencia_declarada)?.texto}»</small>
          )}
        </>
      ) : null,
      nodos: ['determinar_urgencia'],
    },
    { titulo: 'Resumen confirmado', valor: d.confirmado ? 'Sí, el vecino lo confirmó' : null, nodos: ['confirmar_resumen'] },
    { titulo: 'Incidencia', valor: d.incidencia_id ? `Creada · ref. ${d.incidencia_id}` : null, nodos: ['validar_datos', 'crear_ticket'] },
    { titulo: 'Aviso (simulado)', valor: aviso, nodos: ['asignar_especialista', 'avisar_canal'] },
  ]
}

function estadoDe(p: Paso, v: VistaLlamada): Estado {
  if (p.valor) return 'hecho'
  if (v.finalizada) return 'omitido'
  return p.nodos.includes(v.nodo_actual) ? 'ahora' : 'pendiente'
}

const ICONO: Record<Estado, ReactNode> = {
  hecho: <CheckCircleFilled />,
  ahora: <LoadingOutlined />,
  pendiente: <span className="ficha-punto" />,
  omitido: <span className="ficha-punto" />,
}

export function FichaLlamada({ vista }: { vista: VistaLlamada | null }) {
  if (!vista) {
    return (
      <div className="ficha-vacia">
        <p>Aquí verás, en tiempo real, lo que el asistente va entendiendo:</p>
        <ol>
          <li>la finca y la vivienda desde la que llaman,</li>
          <li>quién llama y qué le ocurre,</li>
          <li>el tipo de avería y su urgencia,</li>
          <li>y a qué profesional se avisa.</li>
        </ol>
        <p>Cada dato se puede comprobar en «Fincas y profesionales».</p>
      </div>
    )
  }

  const lista = pasos(vista)
  const r = resultado(vista.datos.resultado)

  return (
    <div className="ficha">
      {r && vista.finalizada && (
        <div className={`ficha-resultado is-${r.tono}`}>
          {r.tono === 'bien' ? <CheckCircleFilled /> : r.tono === 'revisar' ? <ExclamationCircleFilled /> : <InfoCircleFilled />}
          <div>
            <b>{r.corto}</b>
            <p>{r.largo}</p>
          </div>
        </div>
      )}

      <ol className="ficha-pasos">
        {lista.map((p) => {
          const e = estadoDe(p, vista)
          return (
            <li key={p.titulo} className={`is-${e}`}>
              <span className="ficha-icono">{ICONO[e]}</span>
              <div>
                <span className="ficha-titulo">
                  {p.titulo}
                  {e === 'ahora' && <em>preguntando ahora</em>}
                </span>
                <span className="ficha-valor">{p.valor ?? (e === 'omitido' ? 'No se llegó a este paso' : 'Pendiente')}</span>
                {p.nota && <small className="ficha-nota">{p.nota}</small>}
              </div>
            </li>
          )
        })}
      </ol>

      {vista.datos.resumen && (
        <div className="ficha-resumen">
          <span>Resumen que se envía</span>
          <p>{vista.datos.resumen}</p>
        </div>
      )}

      <Collapse
        size="small"
        ghost
        className="ficha-tecnico"
        items={[
          {
            key: 'tecnico',
            label: 'Detalle técnico (para el equipo)',
            children: <PanelEstado vista={vista} />,
          },
        ]}
      />
    </div>
  )
}
