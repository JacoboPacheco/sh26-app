import { useSyncExternalStore } from 'react'

// GEMINI AGAINST THE ENGINE, replayed on the map (GeminiDuelLayer.jsx). The AI proposer's run (backend/solutions.py →
// report.agentic.trace) is turned into beats, one per recorded row, in the order it happened: Gemini's plan, the
// engine's verdict on it, the findings sent back, the revision, and the end of the contest (the engine's own plan's
// price is the bar). Nothing is staged: every beat is a row the backend recorded, and the replay only sets its pace.
//
// Row fields read here (solutions.py _Trace): kind 'ask'|'propose'|'revise'|'verify'|'feedback'|'skip'|'offline'|
// 'result', round, lines [{id, label, from_mva, to_mva}], lines_total, holds, over [{id, label, pct, mva}], over_count,
// cost_usd, engine_cost_usd, margin_pct, beats_engine_by, vs 'beat'|'match'|'pricier'|'smaller'|'thin', top [{id, pct}],
// peak_pct, outcome, best_cost_usd, beat_by_usd.

// the pace: a whole replay runs 12 to 20 s whatever the number of rounds
const MS = { ask: 1500, plan: 1250, verdict: 1500, feedback: 1800, skip: 900, offline: 1500 }
const TOTAL_MIN = 12000
const TOTAL_MAX = 20000
const BEAT_MIN = 560

/** The beats of a recorded run. Each: {kind, row, ms, round, rounds, planNo, plans, plan (a verdict's plan row),
 *  over (a feedback beat's lines still over), best (the result's plan and verdict)}. */
export function duelBeats(trace) {
  const rows = Array.isArray(trace) ? trace.filter((r) => r && typeof r === 'object') : []
  const rounds = Math.max(1, ...rows.map((r) => Number(r.round) || 1))
  const plansIn = new Map()
  rows.forEach((r) => (r.kind === 'propose' || r.kind === 'revise') && plansIn.set(r.round, (plansIn.get(r.round) || 0) + 1))
  const out = []
  let plan = null
  let planNo = 0
  let round = 0
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i]
    if (r.kind === 'propose' || r.kind === 'revise') {
      if (r.round !== round) {
        round = r.round
        planNo = 0
      }
      planNo += 1
      plan = r
      out.push({ kind: 'plan', row: r, ms: MS.plan, round: r.round, rounds, planNo, plans: plansIn.get(r.round) || 1 })
    } else if (r.kind === 'verify') {
      out.push({ kind: 'verdict', row: r, plan, ms: MS.verdict, round: r.round, rounds, planNo, plans: plansIn.get(r.round) || 1 })
    } else if (r.kind === 'feedback') {
      // what goes back: the lines the last round's plans left over their limit, each at the worst loading any
      // failed plan left it at; then the lines a cheaper plan left hot (within their rating, but above the engine's
      // plan's margin), only where no plan left that line over its limit (`hot`: drawn amber, never red)
      const prev = rows.slice(0, i).filter((x) => x.kind === 'verify' && x.round === r.round - 1)
      const over = new Map()
      prev.forEach((v) =>
        (v.over || []).forEach((o) => {
          const had = over.get(o.id)
          if (!had || o.pct > had.pct) over.set(o.id, { id: o.id, pct: o.pct, mva: o.mva, label: o.label })
        }),
      )
      prev.forEach((v) => {
        if (v.vs !== 'thin') return
        ;(v.top || [])
          .filter((t) => t.pct > (v.margin_pct ?? 100) + 0.5)
          .forEach((t) => {
            const had = over.get(t.id)
            if (!had || (had.hot && t.pct > had.pct)) over.set(t.id, { ...t, hot: true })
          })
      })
      const priced = prev.filter((v) => v.holds && v.cost_usd != null).map((v) => v.cost_usd)
      out.push({ kind: 'feedback', row: r, ms: MS.feedback, round: r.round, rounds, over: [...over.values()], priced })
    } else if (r.kind === 'result') {
      out.push({ kind: 'result', row: r, ms: 0, round: r.round, rounds, best: bestOf(rows) })
    } else if (r.kind === 'ask' || r.kind === 'skip' || r.kind === 'offline') {
      out.push({ kind: r.kind, row: r, ms: MS[r.kind], round: r.round || 1, rounds })
    }
  }
  // fit the whole run into 12-20 s
  const natural = out.reduce((s, b) => s + b.ms, 0)
  const scale = natural > TOTAL_MAX ? TOTAL_MAX / natural : natural < TOTAL_MIN && natural > 0 ? TOTAL_MIN / natural : 1
  out.forEach((b) => b.ms && (b.ms = Math.max(BEAT_MIN, Math.round(b.ms * scale))))
  return out
}

/** The plan the contest ends on: the verified one the result names (its price), else the cheapest that held. */
export function bestOf(rows) {
  const pairs = []
  for (let i = 1; i < rows.length; i++) {
    const v = rows[i]
    const p = rows[i - 1]
    if (v.kind === 'verify' && v.holds && (p.kind === 'propose' || p.kind === 'revise')) pairs.push({ plan: p, verdict: v })
  }
  if (!pairs.length) return null
  const res = rows.find((r) => r.kind === 'result')
  // the plan the result names (by its price); a winning plan first when two share the price
  const named = res?.best_cost_usd != null ? pairs.filter((x) => x.verdict.cost_usd === res.best_cost_usd).sort((a, b) => (b.verdict.vs === 'beat') - (a.verdict.vs === 'beat'))[0] : null
  if (named) return named
  const priced = pairs.filter((x) => x.verdict.cost_usd != null)
  return priced.length ? priced.reduce((a, b) => (b.verdict.cost_usd < a.verdict.cost_usd ? b : a)) : pairs[0]
}

/** A line's loading as the replay prints it: whole numbers, but one decimal near the limit (99.7 % is inside its
 *  rating, 100.3 % over it) and wherever `fine` asks for it (a hot line within its rating). */
export function pctText(p, fine = false) {
  const v = Number(p) || 0
  return `${fine || (v >= 99.5 && v < 100.5) ? v.toFixed(1) : Math.round(v)} %`
}

/** How the run ended, in one word: 'beat' | 'matched' | 'lost' | 'failed' | 'none' | 'verified' (older runs carry no
 *  outcome: read from the counts). */
export function outcomeOf(agentic) {
  const res = (agentic?.trace || []).find((r) => r.kind === 'result')
  if (res?.outcome) return res.outcome
  if (!agentic?.asked) return 'none'
  return agentic.verified ? 'verified' : 'failed'
}

// ------------------------------------------------------------------ the store (module-level, shared by the offer,
// the map layer and the card)
const IDLE = { status: 'idle', beats: [], i: -1 }
let st = IDLE
const subs = new Set()
const emit = (patch) => {
  st = { ...st, ...patch }
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
const read = () => st
let timer = 0
let beatStart = 0
let left = 0 // ms left of the current beat when paused

/** {status: 'idle'|'playing'|'paused'|'done', beats, i, agentic, trace, body, report, fix, rate, cascade, started} */
export const useDuel = () => useSyncExternalStore(subscribe, read, read)
export const duelState = () => st

function arm(ms) {
  clearTimeout(timer)
  beatStart = performance.now()
  left = ms
  timer = setTimeout(next, ms)
}

function next() {
  const i = st.i + 1
  if (i >= st.beats.length - 1) {
    emit({ i: st.beats.length - 1, status: 'done' })
    return
  }
  emit({ i })
  arm(st.beats[i].ms)
}

/** Replay a run. `reduced`: straight to the end (the rounds listed, the final plan on the map). */
export function startDuel({ agentic, body, report, fix, rate, cascade, reduced = false }) {
  const beats = duelBeats(agentic?.trace)
  if (!beats.length) return
  clearTimeout(timer)
  st = { status: 'playing', beats, i: -1, agentic, trace: agentic.trace, body, report, fix, rate, cascade, started: performance.now() }
  if (reduced || beats.length === 1) {
    emit({ i: beats.length - 1, status: 'done' })
    return
  }
  next()
}

export function pauseDuel() {
  if (st.status !== 'playing') return
  clearTimeout(timer)
  left = Math.max(0, left - (performance.now() - beatStart))
  emit({ status: 'paused' })
}

export function resumeDuel() {
  if (st.status !== 'paused') return
  emit({ status: 'playing' })
  arm(Math.max(200, left))
}

export function skipDuel() {
  if (st.status === 'idle') return
  clearTimeout(timer)
  emit({ i: st.beats.length - 1, status: 'done' })
}

export function closeDuel() {
  clearTimeout(timer)
  if (st.status !== 'idle') {
    st = IDLE
    subs.forEach((f) => f())
  }
}
