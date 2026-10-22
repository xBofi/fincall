import { useCallback, useEffect, useMemo, useState } from 'react'
import { catalogos, type AdministradorCatalogo, type EspecialistaCatalogo, type FincaDetalle } from './api'
import { etiquetaVivienda } from './textos'

export interface Catalogo {
  fincas: FincaDetalle[]
  profesionales: EspecialistaCatalogo[]
  /** Con su capa de configuración: quién atiende cada categoría en todas sus fincas. */
  administradores: AdministradorCatalogo[]
  cargando: boolean
  error: string | null
  recargar: () => void
  /** Nombre de cada profesional por id, para traducir reglas de aviso y configuración. */
  nombreProfesional: Record<string, string>
  direccionFinca: (id: string | null | undefined) => string | null
  etiquetaVivienda: (id: string | null | undefined) => string | null
  /** Fincas en las que la configuración asigna a cada profesional. */
  fincasDeProfesional: Record<string, FincaDetalle[]>
}

/**
 * Fincas (con su detalle completo) y profesionales, cargados una vez para toda la consola.
 *
 * Se piden todos los detalles de golpe: quien simula una llamada necesita ver viviendas y
 * profesionales de cualquier finca sin esperar, y el buscador tiene que encontrar viviendas.
 */
export function useCatalogo(activo: boolean): Catalogo {
  const [fincas, setFincas] = useState<FincaDetalle[]>([])
  const [profesionales, setProfesionales] = useState<EspecialistaCatalogo[]>([])
  const [administradores, setAdministradores] = useState<AdministradorCatalogo[]>([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!activo) return
    let vigente = true
    setCargando(true)
    Promise.all([catalogos.fincas(), catalogos.especialistas(), catalogos.administradores()])
      .then(async ([resumen, esp, admins]) => {
        const detalles = await Promise.all(resumen.map((f) => catalogos.finca(f.id)))
        if (!vigente) return
        setFincas(detalles)
        setProfesionales(esp)
        setAdministradores(admins)
        setError(null)
      })
      .catch((e: unknown) => {
        if (vigente) setError(e instanceof Error ? e.message : 'No se pudo leer la base de datos')
      })
      .finally(() => {
        if (vigente) setCargando(false)
      })
    return () => {
      vigente = false
    }
  }, [activo, version])

  const recargar = useCallback(() => setVersion((v) => v + 1), [])

  return useMemo(() => {
    const nombreProfesional = Object.fromEntries(profesionales.map((p) => [p.id, p.nombre]))
    const fincaPorId = new Map(fincas.map((f) => [f.id, f]))
    const viviendaPorId = new Map(fincas.flatMap((f) => f.viviendas.map((v) => [v.id, { f, v }] as const)))
    const fincasDeProfesional: Record<string, FincaDetalle[]> = {}
    for (const f of fincas) {
      const asignados = new Set(Object.values(asignacion(f)))
      for (const id of asignados) (fincasDeProfesional[id] ??= []).push(f)
    }
    return {
      fincas,
      profesionales,
      administradores,
      cargando,
      error,
      recargar,
      nombreProfesional,
      fincasDeProfesional,
      direccionFinca: (id) => (id ? fincaPorId.get(id)?.direccion ?? null : null),
      etiquetaVivienda: (id) => {
        const x = id ? viviendaPorId.get(id) : undefined
        return x ? etiquetaVivienda(x.v) : null
      },
    }
  }, [fincas, profesionales, administradores, cargando, error, recargar])
}

// ------------------------------------------------------------- lectura de la configuración efectiva

export interface ReglaAviso {
  urgencia: string
  canal: string
  destino: string
}

export const asignacion = (f: FincaDetalle): Record<string, string> =>
  (f.configuracion_efectiva.especialista_por_categoria as Record<string, string> | undefined) ?? {}

export const categoriasDe = (f: FincaDetalle): string[] =>
  (f.configuracion_efectiva.catalogo_categorias as string[] | undefined) ?? []

export const reglasDe = (f: FincaDetalle): ReglaAviso[] =>
  (f.configuracion_efectiva.reglas_aviso as ReglaAviso[] | undefined) ?? []
