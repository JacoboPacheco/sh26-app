// The plan race's calls (backend/plan_agents.py), through the app's one API helper. A "client" is the three calls the
// panel needs: the live one below, or a recorded race replayed in the preview (fixtureClient.js).
import { api } from '../../api'

const q = (o) => new URLSearchParams(Object.entries(o).map(([k, v]) => [k, String(v)])).toString()

// start (or join, or get the finished) race for a finished Strengthen study -> {id, status, cached}; 409 without one
export const startRace = ({ region = 'FL', mw = 1000, loadFactor = 1, mode = 'firm', ai = true } = {}) =>
  api('/api/strengthen/plan-race', { method: 'POST', body: { region, mw, load_factor: loadFactor, mode, ai } })

// the race as it runs: {status, knee, lanes (live traces), result when done}
export const getRace = (id) => api(`/api/strengthen/plan-race/${encodeURIComponent(id)}`)

// the budget bar (the knee of the engine's cost curve) of a finished study; 409 when the study isn't done
export const getKnee = ({ region = 'FL', mw = 1000, loadFactor = 1, mode = 'firm' } = {}) =>
  api(`/api/strengthen/knee?${q({ region, mw, load_factor: loadFactor, mode })}`)

export const liveClient = { start: startRace, get: getRace, knee: getKnee }
