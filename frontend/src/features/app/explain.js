import { fmt } from '../../geo'

// Plain-sentence verdicts for the workspace, from what the backend returned — no numbers are made
// up here. Two moments: before you run (the what-if) and after (the cascade).

const lines = (n) => `${fmt(n)} ${n === 1 ? 'line' : 'lines'}`

// The what-if: {tone: 'idle' | 'ok' | 'warn', label, sentence}
export function beforeRun({ result, solving, hasCase, whatifError }) {
  if (whatifError) return { tone: 'bad', label: 'Can’t solve this', sentence: whatifError.message }
  if (!hasCase) return { tone: 'idle', label: 'Nothing added yet', sentence: 'Click the map to plug in a data center, or pick a real one from the list.' }
  if (!result || solving === 'first') return { tone: 'idle', label: 'Solving…', sentence: 'Re-solving the power flow on every line.' }
  const over = result.overloaded?.length || 0
  const main = result.sites?.[0]
  const room = main ? Math.max(0, Number(main.headroom_mw) || 0) : null
  const mw = Number(main?.mw) || 0
  if (!over) {
    const spare = room !== null ? room - mw : null
    return {
      tone: 'ok',
      label: 'Holds',
      sentence:
        room !== null
          ? `No line goes over its limit. This site has room for ${fmt(room)} MW before the first line overloads${spare > 0 ? `, ${fmt(spare)} MW more than this campus` : ''}.`
          : 'No line goes over its limit.',
    }
  }
  return {
    tone: 'warn',
    label: `${lines(over)} over the limit`,
    sentence:
      room !== null && mw > room
        ? `This site has room for ${fmt(room)} MW before the first line overloads; ${fmt(mw)} MW is ${fmt(mw - room)} MW too much. Run the cascade to see what trips.`
        : `Run the cascade to see what trips next.`,
  }
}

// The step at which the campus lost power: fully (≥ 99 % of its load) if it happened, else first.
export function cutOffStep(cascade) {
  const steps = cascade?.steps || []
  const total = (cascade?.sites || []).reduce((a, s) => a + (Number(s.mw) || 0), 0) || Number(cascade?.mw) || 0
  const full = steps.find((s) => (s.site_dark_mw || 0) >= 0.99 * total && total > 0)
  const first = steps.find((s) => (s.site_dark_mw || 0) > 0.5)
  return (full || first)?.n ?? null
}

// The cascade's outcome: {tone, label, sentence}
export function afterRun(cascade) {
  const n = cascade.steps.length
  const people = Number(cascade.people ?? 0)
  if (n === 0) return { tone: 'ok', label: 'Nothing trips', sentence: 'No line is over its limit, so nothing cascades.' }
  if (cascade.outcome !== 'islanded' || !(cascade.lost_mw > 0.5)) {
    return { tone: 'ok', label: 'Settles', sentence: `The grid settled after ${n} ${n === 1 ? 'step' : 'steps'} and no one lost power.` }
  }
  const capped = cascade.capped ? ' It was still spreading when the model stopped at 30 steps.' : ''
  const shed = Number(cascade.shed_mw) || 0
  if (cascade.firm && shed > 0.5) {
    // firm service: the operator cut customers on purpose to hold the campus's lines
    const tripped = Math.max(0, (Number(cascade.lost_mw) || 0) - shed)
    return {
      tone: 'bad',
      label: tripped > 0.5 ? 'Blackout' : 'Customers cut',
      sentence: `Over ${n} ${n === 1 ? 'step' : 'steps'} the operator cut ${fmt(shed)} MW of other customers ${cascade.site_cut_off ? 'trying to keep the campus on' : 'to keep the campus on'}${tripped > 0.5 ? `, and tripped lines cut ${fmt(tripped)} MW more` : ''}: about ${fmt(people)} people without power at the end (estimate).${capped}`,
    }
  }
  return {
    tone: 'bad',
    label: 'Blackout',
    sentence: `The grid split after ${n} ${n === 1 ? 'step' : 'steps'}: ${fmt(cascade.lost_mw)} MW of homes and businesses lost, about ${fmt(people)} people at the end (estimate).${capped}`,
  }
}

// Why the number is what it is — the answer to "why don't more people lose power when the campus
// gets bigger?". {kind, title, sentence, action?: 'firm' | 'flexible'}
export function whyNumber(cascade, room) {
  if (!cascade || !cascade.steps.length) return null
  const siteMw = (cascade.sites || []).reduce((a, s) => a + (Number(s.mw) || 0), 0)
  if (!siteMw) return null
  const at = cutOffStep(cascade)
  const many = (cascade.sites || []).length > 1
  const it = many ? 'the campuses' : 'the campus'
  const roomText = Number.isFinite(room) ? `Above this site's room (${fmt(room)} MW)` : 'Above the site’s room'
  if (!cascade.firm && cascade.site_cut_off) {
    return {
      kind: 'cutoff',
      title: many ? 'Your data centers were cut off' : 'Your data center was cut off',
      sentence: `At step ${at ?? '?'} the lines feeding ${it} tripped, so ${many ? 'they' : 'it'} stopped drawing power and the overload had nowhere left to push. ${roomText}, a bigger campus here usually trips the same lines and blacks out about the same people. That's why the count stops growing with size.`,
      action: 'firm',
    }
  }
  if (cascade.firm && cascade.firm_held) {
    return {
      kind: 'firm-held',
      title: 'Kept on: firm service',
      sentence: `The operator held the lines feeding ${it} and cut ${fmt(cascade.shed_mw || 0)} MW of other customers instead. On firm service the campus's size decides how many people lose power.`,
      action: 'flexible',
    }
  }
  if (cascade.firm && cascade.firm_held === false) {
    return {
      kind: 'firm-failed',
      title: 'Firm service couldn’t hold',
      sentence: `The operator cut ${fmt(cascade.shed_mw || 0)} MW of other customers first, but the lines feeding ${it} still couldn't carry ${many ? 'them' : 'it'}: they tripped and ${it} lost power too.`,
      action: null,
    }
  }
  return null
}
