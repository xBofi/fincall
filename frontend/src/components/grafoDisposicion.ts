import type { AristaGrafo, Grafo, NodoGrafo } from '../api'

/**
 * Disposición del grafo del asistente.
 *
 * El flujo es una estrella: `siguiente_paso` reparte cada pregunta y casi todas vuelven a él.
 * Dibujarlo como capas con curvas da una maraña de ~30 aristas cruzando bloques. Aquí:
 *
 * - el nodo de entrada es un **eje** vertical a la izquierda; cada pregunta se une a él con una
 *   línea horizontal corta (flecha doble si además vuelve),
 * - las preguntas y efectos van en una **columna principal**, en el orden de etapas,
 * - las salidas de error se ponen **al lado** del paso que las provoca,
 * - las **salidas globales** (a las que se llega desde muchos pasos) se agrupan aparte y sus
 *   aristas solo se dibujan cuando hay un bloque seleccionado que las use,
 * - repetir la misma pregunta no es una arista: es una marca ↻ en el bloque.
 */

const ANCHO_NODO = 196
const ALTO_NODO = 50
const MARGEN = 24
const ANCHO_EJE = 156
const SEP_EJE = 64
const SEP_FILA = 26
const SEP_LATERAL = 104
const CARRIL_1 = 22
const CARRIL_2 = 46
const RADIO = 9

/** Salidas de error: se dibujan junto al paso que las provoca. Presentación, no lógica. */
const LATERAL: Record<string, string> = {
  finca_no_identificada: 'identificar_finca',
  vivienda_no_identificada: 'identificar_vivienda',
  no_clasificable: 'validar_datos',
}

/** A partir de cuántos orígenes distintos una salida se considera global. */
const MIN_ORIGENES_SALIDA = 3

export type Columna = 'eje' | 'principal' | 'lateral' | 'salida'

export interface Caja {
  nodo: NodoGrafo
  x: number
  y: number
  ancho: number
  alto: number
  columna: Columna
  fila: number
  /** Cuántos pasos distintos llevan a una salida global. */
  origenes: number
}

export interface Trazo {
  clave: string
  d: string
  /** Punta al final (hacia el destino) y/o al principio (vuelta al eje). */
  final: boolean
  inicio: boolean
  veces: number
  origen: string
  destino: string
  titulo: string
  /** Solo se dibuja con un extremo seleccionado. */
  soloActiva: boolean
}

export interface FinMarca {
  x: number
  y: number
  origen: string
}

export interface Disposicion {
  cajas: Map<string, Caja>
  trazos: Trazo[]
  /** Pastilla "Fin de la llamada" bajo el último paso principal. */
  fin: { x: number; y: number; origen: string } | null
  /** Pequeñas marcas de fin junto a los bloques que cierran la llamada. */
  finales: FinMarca[]
  /** Bloques que pueden repetirse a sí mismos. */
  repiten: Set<string>
  rotuloSalidas: { x: number; y: number } | null
  ancho: number
  alto: number
}

const medio = (c: Caja) => c.y + c.alto / 2
const derecha = (c: Caja) => c.x + c.ancho

/** Polilínea ortogonal con esquinas redondeadas. */
function codo(p: [number, number][], r = RADIO): string {
  let d = `M ${p[0][0]} ${p[0][1]}`
  for (let i = 1; i < p.length - 1; i++) {
    const [x0, y0] = p[i - 1]
    const [x1, y1] = p[i]
    const [x2, y2] = p[i + 1]
    const l1 = Math.hypot(x1 - x0, y1 - y0) || 1
    const l2 = Math.hypot(x2 - x1, y2 - y1) || 1
    const k1 = Math.min(r, l1 / 2) / l1
    const k2 = Math.min(r, l2 / 2) / l2
    d += ` L ${x1 - (x1 - x0) * k1} ${y1 - (y1 - y0) * k1} Q ${x1} ${y1} ${x1 + (x2 - x1) * k2} ${y1 + (y2 - y1) * k2}`
  }
  const u = p[p.length - 1]
  return `${d} L ${u[0]} ${u[1]}`
}

export function disponer(g: Grafo): Disposicion {
  const entrada = g.entrada
  const existe = new Set(g.nodos.map((n) => n.id))

  // Orígenes distintos (sin contar el eje ni lazos) de cada nodo
  const origenes = new Map<string, Set<string>>()
  for (const a of g.aristas) {
    if (a.origen === a.destino || a.origen === entrada) continue
    if (!origenes.has(a.destino)) origenes.set(a.destino, new Set())
    origenes.get(a.destino)!.add(a.origen)
  }
  const esLateral = (n: NodoGrafo) => n.id in LATERAL && existe.has(LATERAL[n.id])
  const esSalida = (n: NodoGrafo) =>
    n.id !== entrada && !esLateral(n) && (origenes.get(n.id)?.size ?? 0) >= MIN_ORIGENES_SALIDA

  // Orden estable: etapa y, dentro de la etapa, el orden del manifiesto
  const orden = [...g.nodos].sort((a, b) => a.etapa - b.etapa)
  const principales = orden.filter((n) => n.id !== entrada && !esLateral(n) && !esSalida(n))
  const salidas = orden.filter(esSalida)

  const xEje = MARGEN
  const xPrin = MARGEN + ANCHO_EJE + SEP_EJE
  const xLat = xPrin + ANCHO_NODO + SEP_LATERAL
  const yFila = (i: number) => MARGEN + i * (ALTO_NODO + SEP_FILA)

  const cajas = new Map<string, Caja>()
  const caja = (nodo: NodoGrafo, x: number, fila: number, columna: Columna): Caja => ({
    nodo,
    x,
    y: yFila(fila),
    ancho: ANCHO_NODO,
    alto: ALTO_NODO,
    columna,
    fila,
    origenes: origenes.get(nodo.id)?.size ?? 0,
  })

  principales.forEach((n, i) => cajas.set(n.id, caja(n, xPrin, i, 'principal')))
  let ultimaLateral = -1
  for (const n of orden.filter(esLateral)) {
    const ancla = cajas.get(LATERAL[n.id])!
    cajas.set(n.id, caja(n, xLat, ancla.fila, 'lateral'))
    ultimaLateral = Math.max(ultimaLateral, ancla.fila)
  }
  // Una fila libre para el rótulo y, debajo, las salidas globales
  const primeraSalida = ultimaLateral + 2
  salidas.forEach((n, i) => cajas.set(n.id, caja(n, xLat, primeraSalida + i, 'salida')))

  // El eje abarca las filas de los pasos principales a los que reparte
  const nodoEje = g.nodos.find((n) => n.id === entrada)
  const filasEje = g.aristas
    .filter((a) => a.origen === entrada)
    .map((a) => cajas.get(a.destino))
    .filter((c): c is Caja => c?.columna === 'principal')
    .map((c) => c.fila)
  const finEje = filasEje.length ? Math.max(...filasEje) : 0
  if (nodoEje) {
    cajas.set(entrada, {
      ...caja(nodoEje, xEje, 0, 'eje'),
      ancho: ANCHO_EJE,
      alto: yFila(finEje) + ALTO_NODO - yFila(0),
    })
  }

  const ultimo = principales.at(-1)
  const fin =
    ultimo && g.aristas.some((a) => a.origen === ultimo.id && !existe.has(a.destino))
      ? { x: xPrin + ANCHO_NODO / 2, y: yFila(principales.length) - SEP_FILA / 2 + 14, origen: ultimo.id }
      : null

  const trazos: Trazo[] = []
  const finales: FinMarca[] = []
  const repiten = new Set<string>()
  const eje = cajas.get(entrada)
  const titulo = (a: AristaGrafo) =>
    `${a.origen} → ${a.destino}\n${a.condicion}\nRecorrida ${a.veces} ${a.veces === 1 ? 'vez' : 'veces'}`

  // Eje ↔ principal: una sola línea por paso, con punta en cada sentido que exista
  if (eje) {
    for (const c of cajas.values()) {
      if (c.columna !== 'principal') continue
      const ida = g.aristas.find((a) => a.origen === entrada && a.destino === c.nodo.id)
      const vuelta = g.aristas.find((a) => a.origen === c.nodo.id && a.destino === entrada)
      if (!ida && !vuelta) continue
      const y = medio(c)
      trazos.push({
        clave: `eje-${c.nodo.id}`,
        d: `M ${derecha(eje)} ${y} L ${c.x} ${y}`,
        final: !!ida,
        inicio: !!vuelta,
        veces: Math.max(ida?.veces ?? 0, vuelta?.veces ?? 0),
        origen: entrada,
        destino: c.nodo.id,
        titulo: [ida, vuelta].filter((a): a is AristaGrafo => !!a).map(titulo).join('\n\n'),
        soloActiva: false,
      })
    }
  }

  for (const a of g.aristas) {
    const o = cajas.get(a.origen)
    if (!o) continue
    if (a.origen === a.destino) {
      repiten.add(a.origen)
      continue
    }
    const d = cajas.get(a.destino)
    if (!d) {
      // Hacia el fin de la llamada
      if (fin && fin.origen === a.origen) {
        trazos.push({ clave: `${a.origen}->fin`, d: `M ${o.x + o.ancho / 2} ${o.y + o.alto} L ${fin.x} ${fin.y - 11}`, final: true, inicio: false, veces: a.veces, origen: a.origen, destino: 'fin', titulo: titulo(a), soloActiva: false })
      } else {
        finales.push({ x: derecha(o) + 16, y: medio(o), origen: a.origen })
      }
      continue
    }
    if (o.columna === 'eje' && d.columna === 'principal') continue
    if (d.columna === 'eje') continue // vueltas: ya dibujadas como punta inicial

    const base = { clave: `${a.origen}->${a.destino}`, final: true, inicio: false, veces: a.veces, origen: a.origen, destino: a.destino, titulo: titulo(a) }
    let puntos: [number, number][]
    let soloActiva = false

    if (o.columna === 'eje' && eje) {
      // Del eje a una salida lateral: por el pasillo bajo la fila del ancla y entrando por abajo
      const yc = d.y + d.alto + SEP_FILA / 2
      const xd = d.x + d.ancho / 2
      const ejeAbajo = eje.y + eje.alto
      puntos =
        yc <= ejeAbajo
          ? [[derecha(eje), yc], [xd, yc], [xd, d.y + d.alto]]
          : [[eje.x + eje.ancho / 2, ejeAbajo], [eje.x + eje.ancho / 2, yc], [xd, yc], [xd, d.y + d.alto]]
    } else if (d.columna === 'salida') {
      // Salida global: por el carril exterior; solo con un extremo seleccionado
      const xc = derecha(o) + CARRIL_2
      puntos = [[derecha(o), medio(o) + 8], [xc, medio(o) + 8], [xc, medio(d)], [d.x, medio(d)]]
      soloActiva = true
    } else if (o.columna === 'principal' && d.columna === 'principal' && d.fila === o.fila + 1) {
      puntos = [[o.x + o.ancho / 2, o.y + o.alto], [d.x + d.ancho / 2, d.y]]
    } else if (o.columna === 'principal' && d.columna === 'principal') {
      // Salto hacia atrás o por encima de otros pasos: por el carril interior, entrando por la derecha
      const xc = derecha(o) + CARRIL_1
      puntos = [[derecha(o), medio(o) - 8], [xc, medio(o) - 8], [xc, medio(d) + 8], [derecha(d), medio(d) + 8]]
    } else {
      // Mismo nivel (paso → su salida lateral)
      puntos = [[derecha(o), medio(o)], [d.x, medio(d)]]
    }
    trazos.push({ ...base, d: codo(puntos), soloActiva })
  }

  const filas = Math.max(principales.length + (fin ? 1 : 0), primeraSalida + salidas.length)
  return {
    cajas,
    trazos,
    fin,
    finales,
    repiten,
    rotuloSalidas: salidas.length ? { x: xLat, y: yFila(primeraSalida) - 10 } : null,
    ancho: xLat + ANCHO_NODO + 40,
    alto: yFila(filas) - SEP_FILA + MARGEN,
  }
}
