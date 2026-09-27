import { Component } from 'react'

// A component crash normally blanks the whole page. During a demo that's fatal;
// this shows the error and a reload button instead.
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error(error, info.componentStack)
    // a lazy page from an older deploy (its file is gone after a new one): reload once for the new build
    if (/dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(String(error?.message || error))) {
      let last = 0
      try {
        last = Number(sessionStorage.getItem('overload.chunkReload')) || 0
      } catch {
        last = 0
      }
      if (Date.now() - last > 30000) {
        try {
          sessionStorage.setItem('overload.chunkReload', String(Date.now()))
        } catch {
          // storage blocked: reload anyway
        }
        window.location.reload()
      }
    }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main role="alert" style={{ padding: '2rem' }}>
        <h1>Something broke</h1>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{String(this.state.error?.message || this.state.error)}</pre>
        <button type="button" onClick={() => window.location.reload()}>
          Reload
        </button>
      </main>
    )
  }
}
