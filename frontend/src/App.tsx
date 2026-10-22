import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Badge, Button, Dropdown, Popover, Segmented, Slider, Spin, Switch, Tooltip, message } from 'antd'
import {
  CheckOutlined,
  CloseOutlined,
  LogoutOutlined,
  PhoneOutlined,
  ReloadOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons'
import {
  api,
  auth,
  bandeja,
  operaciones,
  telefonia,
  usuarios as apiUsuarios,
  type DetalleExpediente,
  type EstadoTelefonia,
  type EstadoAcceso,
  type UsuarioConsola,
  type Expediente,
  type ResumenOperaciones,
  type Salud,
  type VistaLlamada,
} from './api'
import Logo from './components/Logo'
import { AccesoPendiente } from './components/AccesoPendiente'
import { Conversacion, type EstadoVoz } from './components/Conversacion'
import { Login } from './components/Login'
import { BaseDatos } from './components/BaseDatos'
import { FichaLlamada } from './components/FichaLlamada'
import { Historial } from './components/Historial'
import { PanelEstado } from './components/PanelEstado'
import type { TurnoMedido } from './components/PanelLatencia'
import { useCatalogo } from './catalogo'
import type { SesionLiveKit } from './livekit'
import { abrirStreaming, type SesionStreaming } from './streaming'
import { ROLES, mensajeError } from './textos'
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
  usarVozServidor,
} from './voz'

// Pantallas que no se usan al simular: se descargan al abrirlas (el arranque pesa menos)
const GestionUsuarios = lazy(() => import('./components/GestionUsuarios').then((m) => ({ default: m.GestionUsuarios })))
const SolicitudesAcceso = lazy(() => import('./components/SolicitudesAcceso').then((m) => ({ default: m.SolicitudesAcceso })))
const PanelFlujo = lazy(() => import('./components/PanelFlujo').then((m) => ({ default: m.PanelFlujo })))
const Bandeja = lazy(() => import('./components/Bandeja').then((m) => ({ default: m.Bandeja })))
const PanelLatencia = lazy(() => import('./components/PanelLatencia').then((m) => ({ default: m.PanelLatencia })))

type ModoStt = 'navegador' | 'servidor' | 'streaming' | 'livekit'
type Pantalla = 'simular' | 'historial' | 'bandeja' | 'datos' | 'flujo' | 'latencia' | 'usuarios' | 'solicitudes'

/** Lo que se enseña al cliente antes de la primera llamada. Hechos del grafo, no promesas. */
/** El barge-in (cortar al bot cuando el vecino habla) está siempre activo en la consola. */
const BARGE_IN = true

const PUEDE = [
  'Identifica la finca por la dirección, aunque el vecino la diga con otras palabras.',
  'Localiza la vivienda por portal, piso y puerta.',
  'Entiende el problema, lo clasifica por tipo de avería y decide la urgencia.',
  'Lee un resumen al vecino y le pide confirmación antes de crear nada.',
  'Crea la incidencia y avisa al profesional que la finca tiene asignado para esa avería.',
  'Responde dudas frecuentes (¿se graba la llamada?, ¿cuánto tardarán?).',
]
const NO_PUEDE = [
  'No atiende fincas que no estén dadas de alta: guarda el mensaje para revisarlo.',
  'Sin la vivienda identificada no crea incidencia: avisa al administrador.',
  'Si ningún profesional cubre esa avería, la incidencia queda pendiente para el administrador.',
  'Si el vecino pide hablar con una persona, no transfiere la llamada: avisa al administrador.',
  'No promete hora de visita: todavía nadie ha hablado con el profesional.',
]

export default function App() {
  const [salud, setSalud] = useState<Salud | null>(null)
  const [vista, setVista] = useState<VistaLlamada | null>(null)
  const [ocupado, setOcupado] = useState(false)
  const [estadoVoz, setEstadoVoz] = useState<EstadoVoz>('inactivo')
  const [parcial, setParcial] = useState<string | null>(null)
  const [vozActiva, setVozActiva] = useState(ttsDisponible())
  const [manosLibres, setManosLibres] = useState(true)
  const [modoStt, setModoStt] = useState<ModoStt>('navegador')
  const [silencioMs, setSilencioMs] = useState(VAD_POR_DEFECTO.silencioMs)
  const [turnos, setTurnos] = useState<TurnoMedido[]>([])
  const [error, setError] = useState<string | null>(null)
  const [streamingInfo, setStreamingInfo] = useState<string | null>(null)
  // Consola: acceso, indicadores, historial de llamadas y la llamada abierta en el historial
  const [acceso, setAcceso] = useState<EstadoAcceso | null>(null)
  const [resumenOps, setResumenOps] = useState<ResumenOperaciones | null>(null)
  const [expedientes, setExpedientes] = useState<Expediente[]>([])
  const [seleccionado, setSeleccionado] = useState<DetalleExpediente | null>(null)
  const [pantalla, setPantalla] = useState<Pantalla>('simular')
  const [tel, setTel] = useState<EstadoTelefonia | null>(null)
  const [solicitudes, setSolicitudes] = useState(0)
  // Incidencias que esperan una decisión del administrador (contador de la pestaña Bandeja)
  const [pendientesBandeja, setPendientesBandeja] = useState(0)

  const refrescarOperaciones = useCallback(async () => {
    try {
      const estado = await auth.estado()
      setAcceso(estado)
      if (!estado.autenticado || estado.usuario?.estado_acceso !== 'concedido') {
        setResumenOps(null)
        setExpedientes([])
        return
      }
      // El owner ve cuántas personas esperan acceso en la pestaña Solicitudes
      if (estado.usuario.rol === 'owner') {
        apiUsuarios
          .listar()
          .then((todos) => setSolicitudes(todos.filter((u) => u.estado_acceso === 'solicitado').length))
          .catch(() => undefined)
      }
      if (estado.usuario.rol === 'owner' || estado.usuario.rol === 'administrador') {
        bandeja
          .listar({ pendientes_administrador: true })
          .then((filas) => setPendientesBandeja(filas.length))
          .catch(() => undefined)
      }
      const [resumen, lista] = await Promise.all([operaciones.resumen(), operaciones.llamadas()])
      setResumenOps(resumen)
      setExpedientes(lista)
      telefonia.estado().then(setTel).catch(() => undefined)
    } catch {
      // Backend caído: se mantiene el último estado conocido
    }
  }, [])

  const personaActualizada = useCallback((usuario: UsuarioConsola) => {
    setAcceso({ modo: 'oidc', autenticado: true, usuario })
  }, [])

  const abrirExpediente = useCallback(async (id: string) => {
    try {
      setSeleccionado(await operaciones.detalle(id))
    } catch (e) {
      message.error(mensajeError(e))
    }
  }, [])

  // Referencias para el bucle de voz (evitan closures obsoletas)
  const vistaRef = useRef<VistaLlamada | null>(null)
  const cancelarEscuchaRef = useRef<() => void>(() => undefined)
  const cancelarHablaRef = useRef<() => void>(() => undefined)
  const streamingRef = useRef<SesionStreaming | null>(null)
  const livekitRef = useRef<SesionLiveKit | null>(null)
  const bucleActivoRef = useRef(false)
  // Cada llamada (o colgar) invalida lo que siga en vuelo de la anterior: micrófono, WebSocket
  const generacionRef = useRef(0)
  const abriendoStreamingRef = useRef<Promise<void> | null>(null)
  const opcionesRef = useRef({ vozActiva, manosLibres, modoStt, silencioMs })
  opcionesRef.current = { vozActiva, manosLibres, modoStt, silencioMs }

  useEffect(() => {
    api
      .salud()
      .then((s) => {
        setSalud(s)
        usarVozServidor(!!s.tts_servidor)
        if (s.stt_streaming) setModoStt('streaming')
        else if (s.stt_disponible && !sttNavegadorDisponible()) setModoStt('servidor')
      })
      .catch((e) => setError(mensajeError(e)))
    void refrescarOperaciones()
  }, [refrescarOperaciones])

  // El historial se refresca mientras está a la vista, no tras cada turno de voz: así no compite
  // con la respuesta del asistente y recoge también las llamadas que entren por teléfono
  useEffect(() => {
    if (pantalla !== 'historial') return
    void refrescarOperaciones()
    const id = window.setInterval(() => {
      if (!document.hidden) void refrescarOperaciones()
    }, 15_000)
    return () => window.clearInterval(id)
  }, [pantalla, refrescarOperaciones])

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
        const rep = hablar(texto, BARGE_IN && !v.finalizada && !streamingRef.current)
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
        stt: finVozAt === null ? null : modo === 'streaming' || modo === 'livekit' ? 'servidor' : modo,
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
      setEstadoVoz(streamingRef.current && !v.finalizada ? 'esperando_voz' : 'inactivo')
      if (!v.finalizada && opcionesRef.current.manosLibres && bucleActivoRef.current && modo !== 'streaming') {
        void escuchar()
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [registrarTurno],
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
        message.error(mensajeError(e))
        setEstadoVoz('inactivo')
      } finally {
        setOcupado(false)
      }
    },
    [responder],
  )

  /** Modo streaming: una sesión por llamada; el proveedor detecta el fin de turno. */
  const abrirStreamingLlamada = useCallback(
    (llamadaId: string): Promise<void> => {
      if (abriendoStreamingRef.current) return abriendoStreamingRef.current
      streamingRef.current?.cerrar()
      streamingRef.current = null
      const generacion = generacionRef.current
      let finVozTurno = 0
      const apertura = abrirStreaming(llamadaId, {
        onListo: (ms) => {
          setStreamingInfo(`Reconocimiento de voz conectado (${ms ?? '?'} ms)`)
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
          void responder(v, finVozTurno || ultimaVozAt || null, {})
        },
        onError: (m) => message.error(m),
        onVoz: (hablando) => setEstadoVoz(hablando ? 'hablando' : 'esperando_voz'),
        onBargeIn: () => {
          if (BARGE_IN) cancelarHablaRef.current()
        },
        onCerrado: () => setStreamingInfo(null),
      })
        .then((sesion) => {
          // Si se colgó (o empezó otra llamada) mientras se abría el micrófono, no se engancha
          if (generacion !== generacionRef.current) sesion.cerrar()
          else streamingRef.current = sesion
        })
        .finally(() => {
          abriendoStreamingRef.current = null
        })
      abriendoStreamingRef.current = apertura
      return apertura
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
      message.error(mensajeError(e))
    }
  }, [responder, abrirStreamingLlamada])

  /** Corta todo lo que tenga en marcha la llamada: escucha, voz del bot, micrófono y WebSocket. */
  const detenerVoz = () => {
    generacionRef.current += 1
    cancelarEscuchaRef.current()
    cancelarHablaRef.current()
    streamingRef.current?.cerrar()
    streamingRef.current = null
    livekitRef.current?.cerrar()
    livekitRef.current = null
    silenciar()
  }

  /** Por LiveKit el agente del servidor escucha y habla; la consola solo enseña la llamada. */
  const iniciarLiveKit = async () => {
    const v = await api.iniciar()
    vistaRef.current = v
    setVista(v)
    const generacion = generacionRef.current
    // Carga bajo demanda: livekit-client solo hace falta en este modo
    const { abrirLiveKit } = await import('./livekit')
    const sesion = await abrirLiveKit(v.llamada_id, {
      onVista: (nueva) => {
        vistaRef.current = nueva
        setVista(nueva)
      },
      onTiempos: (ts) =>
        setTurnos(
          ts.map((t) => ({
            turno: t.turno,
            modo: 'voz',
            stt: 'servidor',
            stt_ms: t.stt_ms ?? undefined,
            servidor_ms: t.servidor_ms,
            interpretacion_ms: t.interpretacion_ms ?? undefined,
            llm_llamadas: t.llm_llamadas,
            respuesta_ms: t.respuesta_ms ?? undefined,
          })),
        ),
      onHablando: (quien) =>
        setEstadoVoz(quien === 'bot' ? 'respondiendo' : quien === 'vecino' ? 'hablando' : 'esperando_voz'),
      onCerrada: () => setEstadoVoz('inactivo'),
    })
    // Si se colgó mientras se conectaba, la sesión recién abierta no debe quedarse viva
    if (generacion !== generacionRef.current) sesion.cerrar()
    else livekitRef.current = sesion
  }

  const iniciar = async () => {
    setError(null)
    setOcupado(true)
    detenerVoz()
    setTurnos([])
    bucleActivoRef.current = true
    try {
      if (opcionesRef.current.modoStt === 'livekit') {
        await iniciarLiveKit()
        return
      }
      const v = await api.iniciar()
      if (opcionesRef.current.modoStt === 'streaming' && opcionesRef.current.manosLibres) {
        // Abrimos el STT mientras suena el saludo para que la conexión no cueste latencia
        void abrirStreamingLlamada(v.llamada_id).catch((e) => message.error(mensajeError(e)))
      }
      await responder(v, null, {})
    } catch (e) {
      setError(mensajeError(e))
    } finally {
      setOcupado(false)
    }
  }

  const colgar = () => {
    bucleActivoRef.current = false
    detenerVoz()
    setEstadoVoz('inactivo')
  }

  const salir = async () => {
    colgar() // salir con una llamada en curso no puede dejar el micrófono abierto
    setVista(null)
    await auth.logout().catch(() => undefined)
    setAcceso({ modo: 'oidc', autenticado: false, usuario: null })
    setResumenOps(null)
    setExpedientes([])
    setSeleccionado(null)
  }

  const sttServidorOk = !!salud?.stt_disponible
  const sttStreamingOk = !!salud?.stt_streaming
  const sttNavegadorOk = sttNavegadorDisponible()
  const sesionLista = !!acceso?.autenticado && acceso.usuario?.estado_acceso === 'concedido'
  const catalogo = useCatalogo(sesionLista)

  if (acceso === null) {
    return (
      <main className="ops-login" aria-busy="true">
        <Spin size="large" tip="Conectando con la consola…"><div style={{ minHeight: 120 }} /></Spin>
      </main>
    )
  }
  if (!acceso.autenticado || !acceso.usuario) return <Login />
  if (acceso.usuario.estado_acceso !== 'concedido') {
    return <AccesoPendiente usuario={acceso.usuario} onCambio={personaActualizada} onSalir={() => void salir()} />
  }

  const yo = acceso.usuario
  const esOwner = yo.rol === 'owner'
  const gestionaIncidencias = esOwner || yo.rol === 'administrador'
  const opcionesPantalla: { label: React.ReactNode; value: Pantalla }[] = [
    { label: 'Simular llamada', value: 'simular' },
    { label: 'Historial', value: 'historial' },
    ...(gestionaIncidencias
      ? [
          {
            label: (
              <Badge count={pendientesBandeja} size="small" offset={[8, -2]}>
                Bandeja
              </Badge>
            ),
            value: 'bandeja' as Pantalla,
          },
        ]
      : []),
    { label: 'Fincas y profesionales', value: 'datos' },
    { label: 'Flujo', value: 'flujo' },
    { label: 'Rendimiento', value: 'latencia' },
    ...(esOwner
      ? [
          {
            label: (
              <Badge count={solicitudes} size="small" offset={[8, -2]}>
                Solicitudes
              </Badge>
            ),
            value: 'solicitudes' as Pantalla,
          },
          { label: 'Usuarios', value: 'usuarios' as Pantalla },
        ]
      : []),
  ]

  const enCurso = !!vista && !vista.finalizada
  const ajustesVoz = (
    <div className="sim-ajustes">
      <label>
        <span>
          <b>Voz del asistente</b>
          <small>Lee las respuestas en voz alta</small>
        </span>
        <Switch checked={vozActiva} onChange={setVozActiva} disabled={!ttsDisponible()} size="small" />
      </label>
      <label>
        <span>
          <b>Manos libres</b>
          <small>Vuelve a escuchar solo después de cada respuesta</small>
        </span>
        <Switch checked={manosLibres} onChange={setManosLibres} size="small" />
      </label>
      <div>
        <b>Reconocimiento de voz</b>
        <Segmented
          block
          size="small"
          value={modoStt}
          onChange={(valor) => setModoStt(valor as ModoStt)}
          options={[
            { label: 'LiveKit', value: 'livekit', disabled: !salud?.livekit },
            { label: 'Servidor', value: 'streaming', disabled: !sttStreamingOk },
            { label: 'Navegador', value: 'navegador', disabled: !sttNavegadorOk },
            { label: 'Grabación', value: 'servidor', disabled: !sttServidorOk || !capturaDisponible() },
          ]}
        />
        <small>«LiveKit» es el camino de las llamadas reales: voz, interrupciones y ledger en el servidor.</small>
      </div>
      {modoStt === 'servidor' && (
        <div>
          <b>Silencio que cierra el turno: {silencioMs} ms</b>
          <Slider min={300} max={1200} step={50} value={silencioMs} onChange={setSilencioMs} />
        </div>
      )}
      {streamingInfo && <small className="ops-mono">{streamingInfo}</small>}
    </div>
  )

  return (
      <main className={`ops-shell ${pantalla === 'simular' ? 'is-fija' : ''}`}>
        <header className="ops-topbar">
          <div className="ops-brand">
            <Logo conNombre />
            <div className="ops-brand-producto">
              <h1>Asistente de incidencias</h1>
              <div className="ops-system">
                <Tooltip title={salud?.modelo_disponible ? 'El asistente puede atender llamadas' : 'El modelo de lenguaje no responde'}>
                  <span className="ops-estado">
                    <span className={`ops-signal ${salud?.modelo_disponible ? 'is-online' : ''}`} />
                    {salud?.modelo_disponible ? 'Asistente disponible' : 'Asistente no disponible'}
                  </span>
                </Tooltip>
                {tel && (
                  <Tooltip title={tel.lista ? `Teléfono ${tel.numero ?? ''}` : 'Pendiente de contratar el número. Mientras tanto, las llamadas se simulan desde esta consola.'}>
                    <span className="ops-estado">
                      <span className={`ops-signal ${tel.lista ? 'is-online' : ''}`} />
                      {tel.lista ? `Línea ${tel.numero ?? 'activa'}` : 'Línea telefónica pendiente'}
                    </span>
                  </Tooltip>
                )}
              </div>
            </div>
          </div>
          <Segmented<Pantalla> className="ops-nav" value={pantalla} onChange={setPantalla} options={opcionesPantalla} />
          <Dropdown
            menu={{
              items: [
                { key: 'rol', label: `Rol: ${ROLES[yo.rol] ?? yo.rol}`, disabled: true },
                { type: 'divider' },
                { key: 'salir', icon: <LogoutOutlined />, label: 'Salir', onClick: () => void salir() },
              ],
            }}
          >
            <Tooltip title={yo.nombre ?? yo.nombre_usuario} placement="left">
              <Button className="ops-usuario" icon={<UserOutlined />}><span>{yo.nombre ?? yo.nombre_usuario}</span></Button>
            </Tooltip>
          </Dropdown>
        </header>

        {error && <Alert className="ops-alert" type="error" message={error} showIcon closable onClose={() => setError(null)} />}

        {pantalla === 'simular' && (
          <section className="sim">
            <section className="sim-col sim-llamada" aria-label="Llamada">
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">Llamada simulada</p>
                  <h2>{enCurso ? 'En curso' : vista?.finalizada ? 'Llamada terminada' : 'Línea libre'}</h2>
                </div>
                <div className="sim-acciones">
                  <Popover content={ajustesVoz} title="Ajustes de voz" trigger="click" placement="bottomRight">
                    <Button icon={<SettingOutlined />} title="Ajustes de voz" />
                  </Popover>
                  {enCurso ? (
                    <Button danger onClick={colgar}>Colgar</Button>
                  ) : vista && (
                    <Button type="primary" icon={<ReloadOutlined />} onClick={() => void iniciar()} loading={ocupado}>
                      Nueva llamada
                    </Button>
                  )}
                </div>
              </div>
              <div className="sim-conversacion">
                {vista ? (
                  <Conversacion historial={vista.historial} parcial={parcial} estadoVoz={estadoVoz} ocupado={ocupado} finalizada={vista.finalizada} manosLibres={manosLibres} onEnviar={enviarTexto} onEscuchar={() => void escuchar()} />
                ) : (
                  <div className="sim-bienvenida">
                    <p className="sim-intro">
                      Haz de vecino: pulsa <b>Iniciar llamada</b> y habla con el micrófono, o escribe lo que diría.
                      Las direcciones y viviendas que existen están en <b>Fincas y profesionales</b>.
                    </p>
                    <Button type="primary" size="large" icon={<PhoneOutlined />} onClick={() => void iniciar()} loading={ocupado}>
                      Iniciar llamada
                    </Button>
                    <div className="sim-capacidades">
                      <div>
                        <h3>Qué hace el asistente</h3>
                        <ul>{PUEDE.map((t) => <li key={t}><CheckOutlined className="is-good" />{t}</li>)}</ul>
                      </div>
                      <div>
                        <h3>Qué no hace</h3>
                        <ul>{NO_PUEDE.map((t) => <li key={t}><CloseOutlined className="is-review" />{t}</li>)}</ul>
                      </div>
                    </div>
                    <small className="sim-pie">En esta demostración los avisos a profesionales y administradores se simulan: no se envía ningún correo ni llamada.</small>
                  </div>
                )}
              </div>
            </section>

            <section className="sim-col sim-ficha" aria-label="Lo que ha entendido el asistente">
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">En tiempo real</p>
                  <h2>Lo que ha entendido el asistente</h2>
                </div>
              </div>
              <FichaLlamada vista={vista} />
            </section>

            <section className="sim-col sim-datos" aria-label="Fincas y profesionales">
              <div className="ops-panel-heading">
                <div>
                  <p className="ops-eyebrow">Para comprobar</p>
                  <h2>Fincas y profesionales</h2>
                </div>
              </div>
              <BaseDatos
                modo="panel"
                catalogo={catalogo}
                resaltado={{
                  fincaId: vista?.datos.finca_id,
                  viviendaId: vista?.datos.vivienda_id,
                  profesionalId: vista?.datos.especialista_id,
                  categoria: vista?.datos.categoria,
                }}
              />
            </section>
          </section>
        )}

        {pantalla === 'historial' && (
          <Historial resumen={resumenOps} expedientes={expedientes} seleccionado={seleccionado} onAbrir={(id) => void abrirExpediente(id)} catalogo={catalogo} />
        )}

        {pantalla === 'datos' && (
          <section className="ops-single">
            <div className="ops-panel-heading">
              <div>
                <p className="ops-eyebrow">Lo que el asistente consulta en cada llamada</p>
                <h2>Fincas y profesionales</h2>
              </div>
            </div>
            <BaseDatos modo="pagina" catalogo={catalogo} />
          </section>
        )}

        <Suspense fallback={<Spin className="ops-cargando" />}>
        {pantalla === 'bandeja' && gestionaIncidencias && (
          <Bandeja catalogo={catalogo} puedeActuar={gestionaIncidencias} onPendientes={setPendientesBandeja} />
        )}

        {pantalla === 'usuarios' && esOwner && (
          <section className="ops-single"><GestionUsuarios yo={yo} /></section>
        )}

        {pantalla === 'solicitudes' && esOwner && (
          <section className="ops-single"><SolicitudesAcceso onCambio={() => void refrescarOperaciones()} /></section>
        )}

        {pantalla === 'flujo' && (
          <section className="ops-single"><PanelFlujo esOwner={esOwner} /></section>
        )}

        {pantalla === 'latencia' && (
          <section className="ops-single ops-latency">
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">Voz · esta sesión</p><h2>Tiempo de respuesta por turno</h2></div><span className="ops-mono">{turnos.length} turnos</span></div>
              <PanelLatencia turnos={turnos} />
            </div>
            <div>
              <div className="ops-panel-heading"><div><p className="ops-eyebrow">Llamada en directo</p><h2>Estado interno del asistente</h2></div></div>
              <PanelEstado vista={vista} />
            </div>
          </section>
        )}
        </Suspense>
      </main>
    )
}
