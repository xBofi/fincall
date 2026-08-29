import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Dropdown, Segmented, Slider, Spin, Switch, message } from 'antd'
import { KeyOutlined, LogoutOutlined, PhoneOutlined, ReloadOutlined, SoundOutlined, UserOutlined } from '@ant-design/icons'
import {
  api,
  auth,
  operaciones,
  telefonia,
  type DetalleExpediente,
  type EstadoTelefonia,
  type EstadoAcceso,
  type UsuarioConsola,
  type Expediente,
  type ResumenOperaciones,
  type Salud,
  type VistaLlamada,
} from './api'
import { CambioContrasena } from './components/CambioContrasena'
import { Conversacion, type EstadoVoz } from './components/Conversacion'
import { GestionUsuarios } from './components/GestionUsuarios'
import { Login } from './components/Login'
import { PanelEstado } from './components/PanelEstado'
import { PanelLatencia, type TurnoMedido } from './components/PanelLatencia'
import { abrirStreaming, type SesionStreaming } from './streaming'
import {
  VAD_POR_DEFECTO,
  SinVozError,
  capturarTurno,
  capturaDisponible,
  hablar,
  reconocerEnNavegador,
  silenciar,
  sttNavegadorDisponible,
  ttsDisponible,
} from './voz'

type ModoStt = 'navegador' | 'servidor' | 'streaming'
type Pantalla = 'operaciones' | 'latencia' | 'usuarios'

export default function App() {
  const [salud, setSalud] = useState<Salud | null>(null)
  const [vista, setVista] = useState<VistaLlamada | null>(null)
  const [ocupado, setOcupado] = useState(false)
  const [estadoVoz, setEstadoVoz] = useState<EstadoVoz>('inactivo')
  const [parcial, setParcial] = useState<string | null>(null)
  const [vozActiva, setVozActiva] = useState(ttsDisponible())
  const [manosLibres, setManosLibres] = useState(true)
  const [bargeIn] = useState(true)
  const [modoStt, setModoStt] = useState<ModoStt>('navegador')
  const [silencioMs, setSilencioMs] = useState(VAD_POR_DEFECTO.silencioMs)
  const [turnos, setTurnos] = useState<TurnoMedido[]>([])
  const [error, setError] = useState<string | null>(null)
  const [streamingInfo, setStreamingInfo] = useState<string | null>(null)
  // Consola operativa: acceso, indicadores, cola de expedientes y dossier seleccionado
  const [acceso, setAcceso] = useState<EstadoAcceso | null>(null)
  const [resumenOps, setResumenOps] = useState<ResumenOperaciones | null>(null)
  const [expedientes, setExpedientes] = useState<Expediente[]>([])
  const [seleccionado, setSeleccionado] = useState<DetalleExpediente | null>(null)
  const [pantalla, setPantalla] = useState<Pantalla>('operaciones')
  const [tel, setTel] = useState<EstadoTelefonia | null>(null)
  const [cambioAbierto, setCambioAbierto] = useState(false)

  const refrescarOperaciones = useCallback(async () => {
    try {
      const estado = await auth.estado()
      setAcceso(estado)
      if (!estado.autenticado || estado.usuario?.debe_cambiar_contrasena) {
        setResumenOps(null)
        setExpedientes([])
        return
      }
      const [resumen, lista] = await Promise.all([operaciones.resumen(), operaciones.llamadas()])
      setResumenOps(resumen)
      setExpedientes(lista)
      telefonia.estado().then(setTel).catch(() => undefined)
    } catch {
      // Backend caído: se mantiene el último estado conocido
    }
  }, [])

  const sesionAbierta = useCallback(
    (usuario: UsuarioConsola) => {
      setAcceso({ modo: 'local', autenticado: true, usuario })
      setPantalla('operaciones')
      void refrescarOperaciones()
    },
    [refrescarOperaciones],
  )

  const salir = useCallback(async () => {
    await auth.logout().catch(() => undefined)
    setAcceso({ modo: 'local', autenticado: false, usuario: null })
    setResumenOps(null)
    setExpedientes([])
    setSeleccionado(null)
  }, [])

  const abrirExpediente = useCallback(async (id: string) => {
    try {
      setSeleccionado(await operaciones.detalle(id))
    } catch (e) {
      message.error(String(e))
    }
  }, [])

  // Referencias para el bucle de voz (evitan closures obsoletas)
  const vistaRef = useRef<VistaLlamada | null>(null)
  const cancelarEscuchaRef = useRef<() => void>(() => undefined)
  const cancelarHablaRef = useRef<() => void>(() => undefined)
  const streamingRef = useRef<SesionStreaming | null>(null)
  const bucleActivoRef = useRef(false)
  const opcionesRef = useRef({ vozActiva, manosLibres, bargeIn, modoStt, silencioMs })
  opcionesRef.current = { vozActiva, manosLibres, bargeIn, modoStt, silencioMs }

  useEffect(() => {
    api
      .salud()
      .then((s) => {
        setSalud(s)
        if (s.stt_streaming) setModoStt('streaming')
        else if (s.stt_disponible && !sttNavegadorDisponible()) setModoStt('servidor')
      })
      .catch((e) => setError(String(e)))
    void refrescarOperaciones()
  }, [refrescarOperaciones])

  const registrarTurno = useCallback((t: TurnoMedido) => {
    setTurnos((prev) => [...prev.filter((x) => x.turno !== t.turno), t])
  }, [])

  /** Reproduce la respuesta y, si procede, vuelve a escuchar. */
  const responder = useCallback(
    async (v: VistaLlamada, finVozAt: number | null, medicion: Partial<TurnoMedido>): Promise<void> => {
      vistaRef.current = v
      setVista(v)
      const texto = v.mensajes_bot.join(' ')
      let ttsInicioAt: number | null = null
      let interrumpida = false
      const recibidoAt = performance.now()
      const modo = opcionesRef.current.modoStt
      if (opcionesRef.current.vozActiva && texto) {
        setEstadoVoz('respondiendo')
        streamingRef.current?.pausar()
        // En streaming el barge-in lo detecta el propio micrófono de la sesión (onBargeIn);
        // en los otros modos, la vigilancia interna de `hablar`
        const rep = hablar(texto, opcionesRef.current.bargeIn && !v.finalizada && !streamingRef.current)
        cancelarHablaRef.current = rep.cancelar
        ttsInicioAt = await rep.inicio
        const fin = await rep.fin
        interrumpida = fin.interrumpida
        streamingRef.current?.reanudar()
        // Ledger de salida: el servidor sabe qué parte del mensaje oyó el vecino
        if (v.turno >= 0) {
          if (streamingRef.current) streamingRef.current.informarEntrega(v.turno, fin.estado, fin.ratio, fin.textoEntregado)
          else api.entrega(v.llamada_id, v.turno, fin.estado, fin.ratio, fin.textoEntregado).catch(() => undefined)
        }
      }
      const t: TurnoMedido = {
        turno: v.turno,
        modo: finVozAt === null ? 'texto' : 'voz',
        stt: finVozAt === null ? null : modo === 'streaming' ? 'servidor' : modo,
        servidor_ms: v.tiempos.servidor_ms,
        interpretacion_ms: v.tiempos.interpretacion_ms,
        llm_llamadas: v.tiempos.llm_llamadas,
        stt_ms: medicion.stt_ms ?? v.tiempos.stt_ms,
        vad_silencio_ms: medicion.vad_silencio_ms,
        red_ms: medicion.red_ms,
        tts_inicio_ms: ttsInicioAt !== null ? Math.round(ttsInicioAt - recibidoAt) : undefined,
        respuesta_ms: finVozAt !== null && ttsInicioAt !== null ? Math.round(ttsInicioAt - finVozAt) : undefined,
        interrumpido: interrumpida,
      }
      if (v.turno > 0) {
        registrarTurno(t)
        api
          .metricas(v.llamada_id, {
            turno: v.turno,
            vad_silencio_ms: t.vad_silencio_ms,
            red_ms: t.red_ms,
            tts_inicio_ms: t.tts_inicio_ms,
            respuesta_ms: t.respuesta_ms,
            stt_cliente: modo,
            extra: { interrumpido: interrumpida, modo: t.modo },
          })
          .catch(() => undefined)
      }
      if (v.finalizada) {
        streamingRef.current?.cerrar()
        streamingRef.current = null
      }
      // La cola y los indicadores reflejan el expediente persistido tras cada turno
      void refrescarOperaciones()
      setEstadoVoz(streamingRef.current && !v.finalizada ? 'esperando_voz' : 'inactivo')
      if (!v.finalizada && opcionesRef.current.manosLibres && bucleActivoRef.current && modo !== 'streaming') {
        void escuchar()
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [registrarTurno, refrescarOperaciones],
  )

  const enviarTexto = useCallback(
    async (texto: string) => {
      const v = vistaRef.current
      if (!v) return
      setOcupado(true)
      silenciar()
      setVista({ ...v, historial: [...v.historial, { rol: 'vecino', texto }], mensajes_bot: [] })
      try {
        const t0 = performance.now()
        const nueva = await api.mensaje(v.llamada_id, texto)
        const red = Math.round(performance.now() - t0 - (nueva.tiempos.servidor_ms ?? 0))
        await responder(nueva, null, { red_ms: Math.max(0, red) })
      } catch (e) {
        message.error(String(e))
        setEstadoVoz('inactivo')
      } finally {
        setOcupado(false)
      }
    },
    [responder],
  )

  /** Modo streaming: una sesión por llamada; el proveedor detecta el fin de turno. */
  const abrirStreamingLlamada = useCallback(
    async (llamadaId: string) => {
      streamingRef.current?.cerrar()
      let finVozTurno = 0
      streamingRef.current = await abrirStreaming(llamadaId, {
        onListo: (ms) => {
          setStreamingInfo(`STT streaming conectado (${ms ?? '?'} ms)`)
          setEstadoVoz('esperando_voz')
        },
        onParcial: (t) => setParcial(t),
        onFinal: (t, ultimaVozAt) => {
          finVozTurno = ultimaVozAt
          setParcial(t)
          setEstadoVoz('procesando')
        },
        onRespuesta: (v, ultimaVozAt) => {
          setParcial(null)
          void responder(v, finVozTurno || ultimaVozAt || null, {
            stt_ms: v.tiempos.stt_eou_a_respuesta_ms !== undefined ? undefined : undefined,
          })
        },
        onError: (m) => message.error(m),
        onVoz: (hablando) => setEstadoVoz(hablando ? 'hablando' : 'esperando_voz'),
        onBargeIn: () => {
          if (opcionesRef.current.bargeIn) cancelarHablaRef.current()
        },
        onCerrado: () => setStreamingInfo(null),
      })
    },
    [responder],
  )

  /** Un turno de escucha (modos navegador/servidor): captura -> STT -> grafo -> respuesta hablada. */
  const escuchar = useCallback(async () => {
    const v = vistaRef.current
    if (!v || v.finalizada) return
    const { modoStt: modo, silencioMs: silencio } = opcionesRef.current
    if (modo === 'streaming') {
      if (!streamingRef.current) await abrirStreamingLlamada(v.llamada_id)
      return
    }
    setParcial(null)
    try {
      let texto = ''
      let finVozAt = 0
      let sttMs: number | undefined
      let vadMs: number | undefined
      let respuesta: VistaLlamada
      let t0 = 0
      if (modo === 'servidor') {
        const cap = capturarTurno({ ...VAD_POR_DEFECTO, silencioMs: silencio }, setEstadoVoz)
        cancelarEscuchaRef.current = cap.cancelar
        const c = await cap.resultado
        finVozAt = c.finVozAt
        vadMs = Math.round(c.decididoAt - c.finVozAt)
        setEstadoVoz('procesando')
        setParcial('…')
        t0 = performance.now()
        respuesta = await api.audio(v.llamada_id, c.blob, c.mime)
        sttMs = respuesta.tiempos.stt_ms
        texto = respuesta.transcripcion ?? ''
        if (respuesta.sin_voz) {
          setParcial(null)
          setEstadoVoz('inactivo')
          if (opcionesRef.current.manosLibres && bucleActivoRef.current) void escuchar()
          return
        }
      } else {
        const rec = reconocerEnNavegador(setEstadoVoz)
        cancelarEscuchaRef.current = rec.cancelar
        const r = await rec.resultado
        finVozAt = r.finVozAt
        sttMs = Math.round(r.resultadoAt - r.finVozAt)
        texto = r.texto
        setEstadoVoz('procesando')
        setParcial(texto)
        t0 = performance.now()
        respuesta = await api.mensaje(v.llamada_id, texto)
      }
      const red = Math.round(performance.now() - t0 - (respuesta.tiempos.servidor_ms ?? 0))
      setParcial(null)
      await responder(respuesta, finVozAt, { stt_ms: sttMs, vad_silencio_ms: vadMs, red_ms: Math.max(0, red) })
    } catch (e) {
      setParcial(null)
      setEstadoVoz('inactivo')
      if (e instanceof SinVozError) {
        if (opcionesRef.current.manosLibres && bucleActivoRef.current && !vistaRef.current?.finalizada) void escuchar()
        return
      }
      if (e instanceof Error && e.message === 'cancelado') return
      message.error(String(e))
    }
  }, [responder, abrirStreamingLlamada])

  const iniciar = async () => {
    setError(null)
    setOcupado(true)
    cancelarEscuchaRef.current()
    cancelarHablaRef.current()
    streamingRef.current?.cerrar()
    streamingRef.current = null
    silenciar()
    setTurnos([])
    bucleActivoRef.current = true
    try {
      const v = await api.iniciar()
      if (opcionesRef.current.modoStt === 'streaming' && opcionesRef.current.manosLibres) {
        // Abrimos el STT mientras suena el saludo para que la conexión no cueste latencia
        void abrirStreamingLlamada(v.llamada_id).catch((e) => message.error(String(e)))
      }
      await responder(v, null, {})
    } catch (e) {
      setError(String(e))
    } finally {
      setOcupado(false)
    }
  }

  const colgar = () => {
    bucleActivoRef.current = false
    cancelarEscuchaRef.current()
    cancelarHablaRef.current()
    streamingRef.current?.cerrar()
    streamingRef.current = null
    silenciar()
    setEstadoVoz('inactivo')
  }

  const sttServidorOk = !!salud?.stt_disponible
  const sttStreamingOk = !!salud?.stt_streaming
  const sttNavegadorOk = sttNavegadorDisponible()
  const respuestas = turnos
    .map((turno) => turno.respuesta_ms)
    .filter((valor): valor is number => typeof valor === 'number')
    .sort((a, b) => a - b)
  const p50 = respuestas.length ? respuestas[Math.floor(respuestas.length / 2)] : null
  const resultado = vista?.datos.resultado as string | undefined
  const resuelta = resultado === 'incidencia_creada'

  if (acceso === null) {
    return (
      <main className="ops-login" aria-busy="true">
        <Spin size="large" tip="Conectando con la consola…"><div style={{ minHeight: 120 }} /></Spin>
      </main>
    )
  }
  if (!acceso.autenticado || !acceso.usuario) return <Login onEntrar={sesionAbierta} />

  const yo = acceso.usuario
  const esOwner = yo.rol === 'owner'
  const opcionesPantalla: { label: string; value: Pantalla }[] = [
    { label: 'Operaciones', value: 'operaciones' },
    { label: 'Latencia', value: 'latencia' },
    ...(esOwner ? [{ label: 'Usuarios', value: 'usuarios' as Pantalla }] : []),
  ]

  return (
      <main className="ops-shell">
        <CambioContrasena
          abierto={cambioAbierto || yo.debe_cambiar_contrasena}
          obligatorio={yo.debe_cambiar_contrasena}
          onCerrar={() => setCambioAbierto(false)}
          onCambiada={(u) => { setCambioAbierto(false); sesionAbierta(u); message.success('Contraseña actualizada') }}
        />
        <header className="ops-topbar">
          <div className="ops-brand">
            <span className="ops-brand-mark" aria-hidden="true">B</span>
            <div>
              <p className="ops-eyebrow">Building Call Assistant</p>
              <h1>Centro operativo</h1>
            </div>
          </div>
          <Segmented<Pantalla> value={pantalla} onChange={setPantalla} options={opcionesPantalla} />
          <div className="ops-system">
            <span className={`ops-signal ${salud?.modelo_disponible ? 'is-online' : ''}`} />
            <span>{salud?.modelo_disponible ? 'Sistema operativo' : 'Modelo no disponible'}</span>
            {tel && (
              <span className="ops-tel" title={tel.lista ? `Teléfono ${tel.numero ?? ''} · TTS ${tel.tts}` : tel.motivo ?? ''}>
                <span className={`ops-signal ${tel.lista ? 'is-online' : ''}`} />
                {tel.lista ? `Teléfono ${tel.numero ?? 'activo'}` : 'Teléfono sin configurar'}
              </span>
            )}
            <Dropdown
              menu={{
                items: [
                  { key: 'rol', label: `Rol: ${yo.rol}`, disabled: true },
                  { type: 'divider' },
                  { key: 'contrasena', icon: <KeyOutlined />, label: 'Cambiar contraseña', onClick: () => setCambioAbierto(true) },
                  { key: 'salir', icon: <LogoutOutlined />, label: 'Salir', onClick: () => void salir() },
                ],
              }}
            >
              <Button size="small" icon={<UserOutlined />}>{yo.nombre ?? yo.nombre_usuario}</Button>
            </Dropdown>
            <Button className="ops-primary" type="primary" icon={vista ? <ReloadOutlined /> : <PhoneOutlined />} onClick={iniciar} loading={ocupado && !vista}>
              {vista ? 'Nueva llamada' : 'Iniciar llamada'}
            </Button>
          </div>
        </header>

        {error && <Alert className="ops-alert" type="error" message={error} showIcon />}

        {pantalla === 'usuarios' && esOwner && (
          <section className="ops-single"><GestionUsuarios yo={yo} /></section>
        )}

        {pantalla === 'latencia' && (
          <section className="ops-single ops-latency">
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">Pipeline de voz · esta sesión</p><h2>Latencia por turno</h2></div><span className="ops-mono">{turnos.length} turnos</span></div>
              <p className="ops-queue-note" style={{ marginTop: 4 }}>respuesta_ms = fin de voz del vecino → inicio de voz del bot. ≤700 excelente · ≤1200 objetivo · ≤1500 tolerable · ≥2000 muy mal.</p>
              <PanelLatencia turnos={turnos} />
            </div>
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">Llamada en directo</p><h2>Estado del grafo</h2></div></div>
              <PanelEstado vista={vista} />
            </div>
          </section>
        )}

        {pantalla === 'operaciones' && (<>
        <section className="ops-metrics" aria-label="Indicadores operativos">
          <article><p>Llamadas registradas</p><strong>{resumenOps?.llamadas ?? (vista ? 1 : 0)}</strong><span>{resumenOps ? `${resumenOps.activas} activas · ${resumenOps.requieren_revision} a revisar` : 'en esta consola'}</span></article>
          <article><p>Respuesta p50</p><strong>{(resumenOps?.latencia_global.p50 ?? p50) == null ? '—' : `${resumenOps?.latencia_global.p50 ?? p50} ms`}</strong><span>{(resumenOps?.latencia_global.p50 ?? p50 ?? Infinity) <= 1200 ? 'dentro del objetivo' : 'por encima del objetivo (1200 ms)'}</span></article>
          <article><p>Resueltas</p><strong>{resumenOps ? `${resumenOps.resueltas}/${resumenOps.llamadas}` : turnos.length}</strong><span>{resumenOps ? 'incidencia creada en la llamada' : 'turnos medidos'}</span></article>
          <article><p>Llamada actual</p><strong className={resultado ? (resuelta ? 'is-good' : 'is-review') : ''}>{resultado ? (resuelta ? 'Resuelta' : 'A revisar') : vista ? 'En curso' : '—'}</strong><span>{resultado ?? (vista ? 'aún sin cierre' : 'sin llamada')}</span></article>
        </section>

        <section className="ops-workbench">
          <aside className="ops-queue" aria-label="Cola de llamadas">
            <div className="ops-panel-heading"><div><p className="ops-eyebrow">Cola operativa</p><h2>Expedientes</h2></div><span>{String(expedientes.length).padStart(2, '0')}</span></div>
            <button className={`ops-call-row ${vista && !seleccionado ? 'is-selected' : ''}`} type="button" onClick={() => { setSeleccionado(null); if (!vista) void iniciar() }}>
              <span className="ops-row-state" />
              <span><b>{vista?.datos.nombre_declarado ?? (vista ? 'Llamada en directo' : 'Nueva llamada')}</b><small>{vista?.datos.finca_direccion ?? 'Sin identificar'}</small></span>
              <time>{vista ? 'ahora' : 'lista'}</time>
            </button>
            {expedientes.filter((e) => e.id !== vista?.llamada_id).map((e) => (
              <button key={e.id} className={`ops-call-row ${seleccionado?.llamada.id === e.id ? 'is-selected' : ''}`} type="button" onClick={() => void abrirExpediente(e.id)}>
                <span className="ops-row-state" style={{ background: e.requiere_revision ? 'var(--color-review)' : e.estado === 'activa' ? 'var(--color-accent)' : 'var(--color-good)' }} />
                <span><b>{e.nombre_declarado ?? 'Sin nombre'}</b><small>{e.resumen ?? e.resultado ?? e.estado}{e.requiere_revision ? ' · revisar' : ''}</small></span>
                <time>{new Date(e.iniciada_en).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}</time>
              </button>
            ))}
            <div className="ops-queue-note">Expedientes persistidos por turno, con trazabilidad completa. Pulse uno para abrir su dossier.</div>
          </aside>

          <section className="ops-dossier" aria-label="Dossier operativo">
            {seleccionado ? (
              <>
                <div className="ops-panel-heading"><div><p className="ops-eyebrow">Dossier de llamada · {seleccionado.llamada.estado}</p><h2>{seleccionado.llamada.nombre_declarado ?? 'Sin nombre'}</h2></div><span className="ops-mono">{seleccionado.llamada.id.slice(-6)}</span></div>
                <dl className="ops-facts">
                  <div><dt>Inicio</dt><dd>{new Date(seleccionado.llamada.iniciada_en).toLocaleString('es-ES')}</dd></div>
                  <div><dt>Teléfono</dt><dd>{seleccionado.llamada.telefono_llamante ?? '—'}</dd></div>
                  <div><dt>Vivienda</dt><dd>{seleccionado.llamada.vivienda_id ?? 'No identificada'}</dd></div>
                  <div><dt>Resultado</dt><dd className={seleccionado.llamada.resultado === 'incidencia_creada' ? 'is-good' : 'is-review'}>{seleccionado.llamada.resultado ?? 'en curso'}{seleccionado.llamada.motivo_revision ? ` · ${seleccionado.llamada.motivo_revision}` : ''}</dd></div>
                </dl>
                <div className="ops-summary"><p className="ops-eyebrow">Resumen</p><p>{seleccionado.llamada.resumen ?? '—'}</p></div>
                {seleccionado.pistas.length > 0 && (
                  <div className="ops-summary" style={{ marginTop: 12 }}>
                    <p className="ops-eyebrow">Grabación del vecino · URL firmada, expira en 5 min · retención 14 días</p>
                    {seleccionado.pistas.map((p) => p.url ? <audio key={p.id} controls preload="none" src={p.url} style={{ width: '100%' }} /> : <small key={p.id}>Pista eliminada por retención</small>)}
                  </div>
                )}
                <div className="ops-decision"><div className="ops-panel-heading"><div><p className="ops-eyebrow">Transcripción y decisiones</p><h2>Rastro verificable</h2></div></div>
                  <ol>{seleccionado.turnos.map((t) => <li key={t.turno}><span>{t.turno}</span><div>{t.vecino && <b>Vecino: {t.vecino}</b>}<small>Asistente: {t.asistente.join(' ')}</small><small>{t.nodo_inicial} → {t.nodo_final}{t.tiempos?.servidor_ms != null ? ` · ${t.tiempos.servidor_ms} ms servidor` : ''}{t.tiempos?.origen_interpretacion ? ` · ${t.tiempos.origen_interpretacion}` : ''}</small>{seleccionado.decisiones.filter((d) => d.turno === t.turno).map((d, i) => <small key={i}>↳ {d.nodo}: {d.accion}{d.detalle ? ` · ${d.detalle}` : ''}</small>)}</div></li>)}</ol>
                </div>
              </>
            ) : (
              <>
                <div className="ops-panel-heading"><div><p className="ops-eyebrow">Dossier de llamada</p><h2>{vista?.datos.finca_direccion ?? 'Esperando una llamada'}</h2></div><span className="ops-mono">{vista?.llamada_id?.slice(-6) ?? '—'}</span></div>
                <dl className="ops-facts">
                  <div><dt>Vecino</dt><dd>{vista?.datos.nombre_declarado ?? 'No identificado'}</dd></div>
                  <div><dt>Vivienda</dt><dd>{vista?.datos.vivienda_etiqueta ?? 'Pendiente'}</dd></div>
                  <div><dt>Categoría</dt><dd>{vista?.datos.categoria ?? 'Sin clasificar'}</dd></div>
                  <div><dt>Urgencia</dt><dd>{vista?.datos.urgencia ?? '—'}</dd></div>
                </dl>
                <div className="ops-summary"><p className="ops-eyebrow">Resumen</p><p>{vista?.datos.resumen ?? 'El expediente se irá completando mientras el vecino responde.'}</p></div>
                <div className="ops-decision"><div className="ops-panel-heading"><div><p className="ops-eyebrow">Flujo de decisión</p><h2>Rastro verificable</h2></div></div>
                  {vista?.traza?.length ? <ol>{vista.traza.slice(-5).map((paso, index) => <li key={`${paso.nodo}-${index}`}><span>{index + 1}</span><div><b>{paso.nodo}</b><small>{paso.accion}{paso.detalle ? ` · ${paso.detalle}` : ''}</small></div></li>)}</ol> : <p className="ops-empty">Cada decisión del grafo aparecerá aquí, con el motivo que la activó.</p>}
                </div>
              </>
            )}
          </section>

          <aside className="ops-live" aria-label="Llamada en directo">
            <div className="ops-panel-heading"><div><p className="ops-eyebrow">Llamada en directo</p><h2>{vista?.finalizada ? 'Finalizada' : vista ? 'En curso' : 'Preparada'}</h2></div>{vista && !vista.finalizada && <Button danger onClick={colgar}>Colgar</Button>}</div>
            <div className="ops-voice-controls">
              <label><SoundOutlined /> Voz <Switch checked={vozActiva} onChange={setVozActiva} disabled={!ttsDisponible()} size="small" /></label>
              <label>Manos libres <Switch checked={manosLibres} onChange={setManosLibres} size="small" /></label>
              <Segmented size="small" value={modoStt} onChange={(valor) => setModoStt(valor as ModoStt)} options={[{ label: 'Streaming', value: 'streaming', disabled: !sttStreamingOk }, { label: 'Navegador', value: 'navegador', disabled: !sttNavegadorOk }, { label: 'Whisper', value: 'servidor', disabled: !sttServidorOk || !capturaDisponible() }]} />
              {modoStt === 'servidor' && <Slider min={300} max={1200} step={50} value={silencioMs} onChange={setSilencioMs} />}
              {streamingInfo && <span className="ops-streaming-info">{streamingInfo}</span>}
            </div>
            <div className="ops-conversation">
              {vista ? <Conversacion historial={vista.historial} parcial={parcial} estadoVoz={estadoVoz} ocupado={ocupado} finalizada={vista.finalizada} manosLibres={manosLibres} onEnviar={enviarTexto} onEscuchar={() => void escuchar()} /> : <div className="ops-call-placeholder"><span className="ops-radar" /><p>La línea está libre</p><small>Inicia una llamada para observar conversación, tiempos y decisiones en tiempo real.</small></div>}
            </div>
            <footer className="ops-retention">Grabaciones: acceso restringido a owner, administradores y calidad · retención de 14 días.</footer>
          </aside>
        </section>
        </>)}
      </main>
    )
}
