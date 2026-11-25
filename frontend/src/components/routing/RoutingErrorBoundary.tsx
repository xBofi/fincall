import { Component, Fragment, type ErrorInfo, type ReactNode } from 'react'
import { Alert, Button } from 'antd'
import { t } from '../../i18n'

interface Props {
  children: ReactNode
  /** Más pequeño, para el lienzo dentro de la vista. */
  compact?: boolean
  /** Antes de reintentar: p. ej. quitar la selección que llevó al fallo. */
  onReset?: () => void
}

interface State {
  error: Error | null
  /** Cambia en cada reintento: vuelve a montar lo de dentro desde cero. */
  attempt: number
}

/**
 * Un fallo al dibujar el flujo no deja la consola en blanco: se ve un aviso pequeño con
 * «Reintentar» y el resto de la aplicación sigue funcionando.
 */
export class RoutingErrorBoundary extends Component<Props, State> {
  state: State = { error: null, attempt: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Routing view crashed', error, info.componentStack)
  }

  retry = () => {
    this.props.onReset?.()
    this.setState((s) => ({ error: null, attempt: s.attempt + 1 }))
  }

  render() {
    const { error, attempt } = this.state
    if (error) {
      return (
        <div className={`rv-crash ${this.props.compact ? 'is-compact' : ''}`} role="alert">
          <Alert
            type="error"
            showIcon
            message={t('routing.crash')}
            description={<span className="rv-mono rv-small">{error.message}</span>}
            action={<Button size="small" onClick={this.retry}>{t('routing.retry')}</Button>}
          />
        </div>
      )
    }
    return <Fragment key={attempt}>{this.props.children}</Fragment>
  }
}
