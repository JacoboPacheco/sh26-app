import { Component, useEffect, useState } from 'react'
import { Loading } from '../../ui'

// Components other tracks build (catalog, forecast, cost, hospitals, areas, library, briefing,
// planner), loaded lazily and only if the file exists. A missing file, a file that fails to
// compile while someone is still writing it, or a component that throws while rendering all show
// the caller's fallback — they never take the app down.
const MODS = import.meta.glob([
  '../catalog/*.jsx',
  '../forecast/*.jsx',
  '../cost/*.jsx',
  '../hospitals/*.jsx',
  '../town/*.jsx',
  '../library/*.jsx',
  '../briefing/*.jsx',
  '../planner/*.jsx',
  '../ask/*.jsx',
  '!../*/Preview.jsx',
])

const cache = new Map() // 'catalog/CatalogList' -> {mod} | {error}
const pending = new Map() // path -> Promise

export const hasModule = (path) => !!MODS[`../${path}.jsx`]

function load(path) {
  const loader = MODS[`../${path}.jsx`]
  if (!loader) return Promise.resolve({ missing: true })
  if (cache.has(path)) return Promise.resolve(cache.get(path))
  if (!pending.has(path)) {
    pending.set(
      path,
      loader()
        .then((mod) => ({ mod }))
        .catch((error) => {
          if (import.meta.env.DEV) console.warn(`optional module ${path} failed to load`, error)
          return { error }
        })
        .then((res) => {
          cache.set(path, res)
          pending.delete(path)
          return res
        }),
    )
  }
  return pending.get(path)
}

// {status: 'missing' | 'loading' | 'ready' | 'error', mod}
export function useModule(path) {
  const initial = () => (!hasModule(path) ? { status: 'missing' } : cache.has(path) ? toState(cache.get(path)) : { status: 'loading' })
  const [state, setState] = useState(initial)
  useEffect(() => {
    let live = true
    if (!hasModule(path)) {
      setState({ status: 'missing' })
      return undefined
    }
    load(path).then((res) => live && setState(toState(res)))
    return () => {
      live = false
    }
  }, [path])
  return state
}

function toState(res) {
  if (res.missing) return { status: 'missing' }
  if (res.error) return { status: 'error', error: res.error }
  return { status: 'ready', mod: res.mod }
}

// <Optional from="catalog/CatalogList" name="default" fallback={…} {...props} />
// `loading` is shown while the file loads (default: a quiet line); the fallback otherwise.
export function Optional({ from, name = 'default', fallback = null, loading, ...props }) {
  const { status, mod } = useModule(from)
  const C = mod?.[name]
  if (status === 'loading') return loading === undefined ? <Loading /> : loading
  if (!C) return fallback
  return (
    <Guard fallback={fallback} name={from}>
      <C {...props} />
    </Guard>
  )
}

// A sibling component that throws while rendering shows the fallback, not a blank app.
export class Guard extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error(`[${this.props.name || 'component'}]`, error, info?.componentStack)
  }

  render() {
    if (this.state.error) return this.props.fallback ?? null
    return this.props.children
  }
}
