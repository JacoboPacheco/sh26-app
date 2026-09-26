// A recorded plan race (a real run of backend/plan_agents.py on Florida at 1 GW, saved to fixture.json) replayed as if
// it were live: the rows arrive over time, lane by lane, then the finish. For the preview when no backend with the
// race is running; the panel can't tell it from the live client (same calls, same shapes).
import fixture from './fixture.json'

const ROW_MS = 650 // one row per lane this often
let t0 = 0
let mode = 'firm'

const recorded = (m) => fixture[m] || fixture.firm

export const fixtureClient = {
  knee: async ({ mode: m = 'firm' } = {}) => recorded(m).knee,
  start: async ({ mode: m = 'firm' } = {}) => {
    mode = m
    t0 = Date.now()
    return { id: `fixture-${m}`, status: 'running', cached: false }
  },
  get: async () => {
    const res = recorded(mode)
    const n = Math.floor((Date.now() - t0) / ROW_MS) + 1
    const longest = Math.max(...res.lanes.map((l) => l.trace.length))
    const base = { id: `fixture-${mode}`, region: res.region, mw: res.mw, load_factor: res.load_factor, mode: res.mode, knee: res.knee, elapsed_s: (Date.now() - t0) / 1000 }
    if (n >= longest) return { ...base, status: 'done', lanes: res.lanes, result: res }
    const lanes = res.lanes.map((l) => {
      const all = l.trace.length
      if (n >= all) return l
      return { ...l, trace: l.trace.slice(0, n), status: l.status === 'offline' ? 'offline' : 'working', verdict: null, plan: null }
    })
    return { ...base, status: 'running', lanes }
  },
}
