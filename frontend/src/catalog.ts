import { useCallback, useEffect, useMemo, useState } from 'react'
import { catalogs, type CatalogAdministrator, type CatalogSpecialist, type EstateDetail } from './api'
import { homeLabel } from './texts'

export interface Catalog {
  estates: EstateDetail[]
  professionals: CatalogSpecialist[]
  /** Con su capa de configuración: quién atiende cada categoría en todas sus fincas. */
  administrators: CatalogAdministrator[]
  loading: boolean
  error: string | null
  reload: () => void
  /** Nombre de cada profesional por id, para traducir reglas de aviso y configuración. */
  professionalName: Record<string, string>
  estateAddress: (id: string | null | undefined) => string | null
  homeLabel: (id: string | null | undefined) => string | null
  /** Fincas en las que la configuración asigna a cada profesional. */
  estatesByProfessional: Record<string, EstateDetail[]>
}

/**
 * Fincas (con su detalle completo) y profesionales, cargados una vez para toda la consola.
 *
 * Se piden todos los detalles de golpe: quien simula una llamada necesita ver viviendas y
 * profesionales de cualquier finca sin esperar, y el buscador tiene que encontrar viviendas.
 */
export function useCatalog(active: boolean): Catalog {
  const [estates, setEstates] = useState<EstateDetail[]>([])
  const [professionals, setProfessionals] = useState<CatalogSpecialist[]>([])
  const [administrators, setAdministrators] = useState<CatalogAdministrator[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!active) return
    let is_current = true
    setLoading(true)
    Promise.all([catalogs.estates(), catalogs.specialists(), catalogs.administrators()])
      .then(async ([summary, spec, admins]) => {
        const details = await Promise.all(summary.map((f) => catalogs.estate(f.id)))
        if (!is_current) return
        setEstates(details)
        setProfessionals(spec)
        setAdministrators(admins)
        setError(null)
      })
      .catch((e: unknown) => {
        if (is_current) setError(e instanceof Error ? e.message : 'No se pudo leer la base de datos')
      })
      .finally(() => {
        if (is_current) setLoading(false)
      })
    return () => {
      is_current = false
    }
  }, [active, version])

  const reload = useCallback(() => setVersion((v) => v + 1), [])

  return useMemo(() => {
    const professionalName = Object.fromEntries(professionals.map((p) => [p.id, p.name]))
    const estateById = new Map(estates.map((f) => [f.id, f]))
    const homeById = new Map(estates.flatMap((f) => f.homes.map((v) => [v.id, { f, v }] as const)))
    const estatesByProfessional: Record<string, EstateDetail[]> = {}
    for (const f of estates) {
      const assignees = new Set(Object.values(assignment(f)))
      for (const id of assignees) (estatesByProfessional[id] ??= []).push(f)
    }
    return {
      estates,
      professionals,
      administrators,
      loading,
      error,
      reload,
      professionalName,
      estatesByProfessional,
      estateAddress: (id) => (id ? estateById.get(id)?.address ?? null : null),
      homeLabel: (id) => {
        const x = id ? homeById.get(id) : undefined
        return x ? homeLabel(x.v) : null
      },
    }
  }, [estates, professionals, administrators, loading, error, reload])
}

// ------------------------------------------------------------- lectura de la configuración efectiva

export interface NotificationRule {
  urgency: string
  channel: string
  target: string
}

export const assignment = (f: EstateDetail): Record<string, string> =>
  (f.effective_config.specialist_by_category as Record<string, string> | undefined) ?? {}

export const categoriesOf = (f: EstateDetail): string[] =>
  (f.effective_config.category_catalog as string[] | undefined) ?? []

export const rulesOf = (f: EstateDetail): NotificationRule[] =>
  (f.effective_config.notice_rules as NotificationRule[] | undefined) ?? []
