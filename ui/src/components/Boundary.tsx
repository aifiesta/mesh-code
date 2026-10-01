import { Component, type ReactNode } from 'react'

/**
 * A render error used to blank the entire window — no message, no way back.
 * That is an unacceptable failure mode for an app whose whole job is showing
 * you what an agent is doing, so a crash now degrades to a visible error with
 * the session intact behind it. The engine keeps running either way; only the
 * view is affected, so "reload" genuinely recovers.
 */
export class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="crash">
        <h2>Something went wrong drawing this view.</h2>
        <p>Your session is still running — the engine is a separate process.</p>
        <pre>{String(this.state.error?.message ?? this.state.error)}</pre>
        <button className="btn primary" onClick={() => location.reload()}>Reload the view</button>
      </div>
    )
  }
}
