import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api'

// Before the vote: the page's calls and its address. Every request goes through api.js.

export const searchProposals = ({ q = '', state = '', limit = 20 } = {}) => {
  const p = new URLSearchParams()
  if (q) p.set('q', q)
  if (state) p.set('state', state)
  p.set('limit', String(limit))
  return api(`/api/vote/search?${p}`)
}

export const getProposal = (id) => api(`/api/vote/proposal/${encodeURIComponent(id)}`)

// The AI analyst ("What it would take", backend/analyst.py): a finished analysis comes back at once ({status: 'done',
// result}); otherwise a job whose trace grows while Gemini calls the engine ({job, status: 'running', trace}).
export const startAnalyst = (id) => api('/api/analyst', { method: 'POST', body: { id, live: true } })
export const getAnalystJob = (job) => api(`/api/analyst/jobs/${encodeURIComponent(job)}`)

// ---------------------------------------------------------------- the address
// #/vote?q=&state=  (search)   #/vote/<id>  (a proposal)   #/vote/<id>/brief  (the printable brief)
const ID = /^[a-z0-9-]{1,120}$/

export function parseRoute(hash) {
  const [path, query = ''] = String(hash || '').replace(/^#/, '').split('?')
  const parts = path.split('/').filter(Boolean)
  const params = new URLSearchParams(query)
  if (parts[0] === 'vote' && parts[1] && ID.test(parts[1])) return { page: parts[2] === 'brief' ? 'brief' : 'proposal', id: parts[1] }
  const state = (params.get('state') || '').toUpperCase()
  return { page: 'home', q: (params.get('q') || '').slice(0, 80), state: /^[A-Z]{2}$/.test(state) ? state : '' }
}

export const proposalHref = (id) => `#/vote/${id}`
export const briefHref = (id) => `#/vote/${id}/brief`

export function searchHash(q, state) {
  const p = new URLSearchParams()
  if (q) p.set('q', q)
  if (state) p.set('state', state)
  const s = p.toString()
  return s ? `#/vote?${s}` : '#/vote'
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash))
  useEffect(() => {
    const f = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])
  return route
}

// ---------------------------------------------------------------- one proposal
// While the AI step of the ranked plans is still running, ask again every few seconds (the engine's
// answers are cached, so this is cheap) until it is done, at most 8 times.
const POLL_MS = 3000
const POLL_MAX = 8

export function useProposal(id) {
  const [state, setState] = useState({ id: null, data: null, error: null })
  const [nonce, setNonce] = useState(0)
  const timer = useRef(0)

  useEffect(() => {
    let live = true
    let polls = 0
    const load = (first) => {
      getProposal(id)
        .then((data) => {
          if (!live) return
          setState({ id, data, error: null })
          if (data.safe?.agentic?.status === 'running' && polls < POLL_MAX) {
            polls += 1
            timer.current = window.setTimeout(() => load(false), POLL_MS)
          }
        })
        .catch((error) => {
          if (live && first) setState({ id, data: null, error })
        })
    }
    load(true)
    return () => {
      live = false
      window.clearTimeout(timer.current)
    }
  }, [id, nonce])

  const retry = useCallback(() => {
    setState({ id: null, data: null, error: null })
    setNonce((n) => n + 1)
  }, [])
  const current = state.id === id
  return { data: current ? state.data : null, error: current ? state.error : null, loading: !current || (!state.data && !state.error), retry }
}
