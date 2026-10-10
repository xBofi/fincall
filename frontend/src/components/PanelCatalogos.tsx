import { useCallback, useEffect, useState } from 'react'
import { Alert, Card, Descriptions, Empty, Segmented, Spin, Table, Tag, Typography } from 'antd'
import { catalogos } from '../api'
import type { AdministradorCatalogo, EspecialistaCatalogo, FincaDetalle, FincaResumen } from '../api'

const { Paragraph, Text } = Typography

type Vista = 'fincas' | 'profesionales' | 'administradores'

function DetalleFinca({ finca }: { finca: FincaDetalle }) {
  const cfg = finca.configuracion_efectiva as Record<string, unknown>
  const categorias = (cfg.catalogo_categorias as string[] | undefined) ?? []
  const porCategoria = (cfg.especialista_por_categoria as Record<string, string> | undefined) ?? {}
  const reglas = (cfg.reglas_aviso as { urgencia: string; canal: string; destino: string }[] | undefined) ?? []

  return (
    <div className="cat-detalle">
      <Descriptions size="small" column={2} bordered
        items={[
          { key: 'admin', label: 'Administrador', children: finca.administrador },
          { key: 'correo', label: 'Correo', children: finca.correo },
          { key: 'tel', label: 'Teléfono oficial', children: finca.telefono_oficial },
          { key: 'viv', label: 'Viviendas', children: finca.viviendas.length },
        ]}
      />

      <Card size="small" title="Viviendas">
        <div className="cat-viviendas">
          {finca.viviendas.map((v) => (
            <Tag key={v.id}>{v.portal ? `${v.portal} · ` : ''}{v.piso}º {v.puerta}</Tag>
          ))}
        </div>
      </Card>

      <Card size="small" title="Configuración efectiva">
        <Paragraph type="secondary" className="cat-nota">
          Las tres capas ya resueltas (por defecto → administrador → finca). Es lo que el grafo
          usa en una llamada a esta finca. Se lee aquí; se cambia en la configuración, no desde
          los textos.
        </Paragraph>
        <Descriptions size="small" column={2}
          items={[
            { key: 'grafo', label: 'Grafo', children: <Text code>{String(cfg.grafo)}</Text> },
            { key: 'detalle', label: 'Pregunta de detalle', children: cfg.pedir_detalle ? 'Sí' : 'No' },
            { key: 'if', label: 'Intentos finca', children: String(cfg.intentos_finca) },
            { key: 'iv', label: 'Intentos vivienda', children: String(cfg.intentos_vivienda) },
            {
              key: 'emerg',
              label: 'Emergencias',
              children: cfg.emergencias_activas
                ? 'Activas'
                : 'Desactivadas (una emergencia se trata como alta)',
            },
          ]}
        />
        <div className="cat-bloque">
          <Text strong>Categorías del catálogo</Text>
          <div className="cat-viviendas">
            {categorias.map((c) => (
              <Tag key={c} color={porCategoria[c] ? 'blue' : undefined}>
                {c.replace(/_/g, ' ')}
                {porCategoria[c] ? ' ✓' : ''}
              </Tag>
            ))}
          </div>
          <Text type="secondary">En azul, las que tienen profesional asignado.</Text>
        </div>
        <div className="cat-bloque">
          <Text strong>Reglas de aviso</Text>
          <ul className="cat-lista">
            {reglas.map((r) => (
              <li key={r.urgencia}>
                <Tag>{r.urgencia}</Tag> por <Text code>{r.canal}</Text> a <Text code>{r.destino}</Text>
              </li>
            ))}
          </ul>
        </div>
      </Card>
    </div>
  )
}

export function PanelCatalogos() {
  const [vista, setVista] = useState<Vista>('fincas')
  const [fincas, setFincas] = useState<FincaResumen[]>([])
  const [profesionales, setProfesionales] = useState<EspecialistaCatalogo[]>([])
  const [admins, setAdmins] = useState<AdministradorCatalogo[]>([])
  const [detalle, setDetalle] = useState<FincaDetalle | null>(null)
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const cargar = useCallback(async () => {
    setCargando(true)
    try {
      const [f, e, a] = await Promise.all([
        catalogos.fincas(),
        catalogos.especialistas(),
        catalogos.administradores(),
      ])
      setFincas(f); setProfesionales(e); setAdmins(a); setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudieron cargar los catálogos')
    } finally { setCargando(false) }
  }, [])

  useEffect(() => { void cargar() }, [cargar])

  const abrir = async (id: string) => {
    setDetalle(null)
    try { setDetalle(await catalogos.finca(id)) }
    catch (e) { setError(e instanceof Error ? e.message : 'No se pudo abrir la finca') }
  }

  if (error) return <Alert type="error" showIcon message={error} />
  if (cargando) return <Spin tip="Leyendo los catálogos…"><div style={{ minHeight: 200 }} /></Spin>

  return (
    <div className="catalogos">
      <div className="ops-panel-heading">
        <div>
          <p className="ops-eyebrow">Datos que el asistente consulta en cada llamada</p>
          <h2>Catálogos</h2>
        </div>
        <Segmented<Vista>
          value={vista}
          onChange={(v) => { setVista(v); setDetalle(null) }}
          options={[
            { label: `Fincas (${fincas.length})`, value: 'fincas' },
            { label: `Profesionales (${profesionales.length})`, value: 'profesionales' },
            { label: `Administradores (${admins.length})`, value: 'administradores' },
          ]}
        />
      </div>

      {vista === 'fincas' && (
        <>
          <Table<FincaResumen>
            size="small"
            rowKey="id"
            dataSource={fincas}
            pagination={false}
            onRow={(f) => ({ onClick: () => void abrir(f.id), style: { cursor: 'pointer' } })}
            columns={[
              { title: 'Dirección', dataIndex: 'direccion' },
              { title: 'Administrador', dataIndex: 'administrador' },
              { title: 'Viviendas', dataIndex: 'viviendas', width: 100 },
              { title: 'Correo', dataIndex: 'correo' },
              { title: 'Teléfono', dataIndex: 'telefono_oficial', width: 140 },
            ]}
          />
          {detalle
            ? <DetalleFinca finca={detalle} />
            : <Empty description="Elige una finca para ver sus viviendas y su configuración" />}
        </>
      )}

      {vista === 'profesionales' && (
        <Table<EspecialistaCatalogo>
          size="small"
          rowKey="id"
          dataSource={profesionales}
          pagination={false}
          columns={[
            { title: 'Nombre', dataIndex: 'nombre' },
            {
              title: 'Oficio',
              dataIndex: 'categoria_principal',
              render: (c: string) => <Tag>{c.replace(/_/g, ' ')}</Tag>,
            },
            { title: 'Administrador', dataIndex: 'administrador' },
            { title: 'Canal', dataIndex: 'canal_contacto', width: 110 },
            { title: 'Destino', dataIndex: 'destino' },
          ]}
        />
      )}

      {vista === 'administradores' && (
        <div className="cat-admins">
          {admins.map((a) => (
            <Card key={a.id} size="small" title={a.nombre}>
              <div className="cat-bloque">
                <Text strong>Fincas</Text>
                <ul className="cat-lista">
                  {a.fincas.map((f) => <li key={f.id}>{f.direccion}</li>)}
                </ul>
              </div>
              <div className="cat-bloque">
                <Text strong>Profesionales</Text>
                <div className="cat-viviendas">
                  {a.especialistas.map((e) => (
                    <Tag key={e.id}>{e.nombre} · {e.categoria_principal.replace(/_/g, ' ')}</Tag>
                  ))}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
