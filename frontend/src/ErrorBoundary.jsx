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
