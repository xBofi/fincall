import { useEffect } from 'react'
import { audit } from './api'

/**
 * Records how long a record with personal data stays on screen.
 *
 * On mount it opens a view in the audit log, sends a heartbeat every `heartbeat_s` and closes it
 * on unmount, when the tab is hidden or when the page closes (`sendBeacon`, which survives the
 * close). Hidden tab = not looking at it: coming back opens a new view. If the browser dies
 * without saying, the server ends the view at its last heartbeat. A failure here never breaks
 * the screen: the server already logs every read on its own.
 */
export function useAuditedView(resource: string, resourceId: string | null | undefined): void {
  useEffect(() => {
    if (!resourceId) return
    let viewId: string | null = null
    let heartbeat: number | undefined
    // Every opening has its number: an answer that arrives late for a closed view is closed
    let generation = 0

    const close = () => {
      window.clearInterval(heartbeat)
      heartbeat = undefined
      generation += 1
      if (viewId) {
        const path = audit.endPath(viewId)
        viewId = null
        if (!navigator.sendBeacon?.(path)) void fetch(path, { method: 'POST', credentials: 'same-origin', keepalive: true })
      }
    }

    const open = async () => {
      const mine = ++generation
      try {
        const v = await audit.openView(resource, resourceId)
        if (mine !== generation) {
          navigator.sendBeacon?.(audit.endPath(v.id))
          return
        }
        viewId = v.id
        heartbeat = window.setInterval(() => {
          if (viewId) void audit.heartbeat(viewId).catch(() => undefined)
        }, v.heartbeat_s * 1000)
      } catch {
        // No view: the read was already logged by the server
      }
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') close()
      else if (!viewId) void open()
    }

    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('pagehide', close)
    if (document.visibilityState !== 'hidden') void open()
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      window.removeEventListener('pagehide', close)
      close()
    }
  }, [resource, resourceId])
}
