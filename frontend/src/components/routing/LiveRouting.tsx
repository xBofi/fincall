import { useEffect, useState } from 'react'
import { routing, type CallView } from '../../api'
import type { GraphStructure } from './types'
import { RoutingView } from './RoutingView'

/** Grafo en vivo, compacto, para la columna de «Simular llamada». */
export function LiveRouting({ view }: { view: CallView | null }) {
  const [structure, setStructure] = useState<GraphStructure | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => {
    setError(null)
    routing.structure().then(setStructure).catch((e: Error) => setError(e.message))
  }
  useEffect(load, [])
  return (
    <RoutingView
      compact
      structure={structure}
      error={error}
      onReload={load}
      modes={['live']}
      initialMode="live"
      live={{ callId: view?.call_id ?? null, view }}
    />
  )
}
