// ONE SET OF NUMBERS. Every screen (the results panel, the presentation and its ticker, Fix it, the written
// brief) names the same figures with the same words, from the same engine fields:
//
//   People hit (estimate)          the headline: everyone whose power ran through a failed line or went out,
//                                  each person counted once (cascade.people_hit; the counter's last value)
//   Still without power when it    the load still cut when the cascade stops, counted as the people it serves
//   settled (estimate)             (cascade.people / report.event.people); a part of the people hit
//   Time without power             backend/costs.py outage_hours, written "about 22 hours" (costs.outage_label)
//   Cost of the outage             the HIGH end of the blackout estimate, with its range ("$1.08 billion",
//                                  "$381 million–$1.08 billion"; features/cost/money.js)
//
// The peak ("at the worst step") is not shown anywhere: it is neither of the two and read as a contradiction.
import { money } from './money'

export const PEOPLE_PER_HOME = 2.5

export const LABEL = {
  en: {
    hit: 'People hit (estimate)',
    hitWhy: 'Everyone whose power ran through a failed line or went out, each person counted once.',
    stillOut: 'still without power when it settled',
    stillOutK: 'Still without power when it settled (estimate)',
    time: 'Time without power (estimate)',
    cost: 'Cost of the outage (estimate)',
  },
  es: {
    hit: 'Personas afectadas (estimación)',
    hitWhy: 'Todas las personas cuya electricidad pasaba por una línea caída o se cortó, cada una contada una vez.',
    stillOut: 'aún sin luz al estabilizarse',
    stillOutK: 'Aún sin luz al estabilizarse (estimación)',
    time: 'Tiempo sin luz (estimación)',
    cost: 'Costo del apagón (estimación)',
  },
}

// The outage length, exactly as backend/costs.py outage_label writes it: "about 22 hours" up to two days,
// then "about 3 days" (half up, like the backend).
export function outageText(hours, lang = 'en') {
  const h = Number(hours) || 0
  const en = lang !== 'es'
  if (h <= 0) return en ? 'no outage' : 'sin apagón'
  if (h < 1.5) return en ? 'about an hour' : 'aproximadamente una hora'
  if (h < 47.5) {
    const n = Math.floor(h + 0.5)
    return en ? `about ${n} hours` : `unas ${n} horas`
  }
  const d = Math.floor(h / 24 + 0.5)
  return en ? `about ${d} days` : `unos ${d} días`
}

// the unit the outage is counted in, and its value there (a tween counts in the unit the end state is written in)
export function outageUnit(hours) {
  const h = Number(hours) || 0
  return h >= 47.5 ? { unit: 'days', value: h / 24 } : { unit: 'hours', value: h }
}

// A cost in the deck's two languages, the panel's precision: "$1.08 billion" / "$1.08 mil millones".
export function moneyIn(v, lang = 'en') {
  const s = money(v)
  if (lang !== 'es') return s
  return s.replace(/ billion$/, ' mil millones').replace(/ million$/, ' millones')
}

// The two people figures of a briefing report: {hit, stillOut}. People hit comes from the engine's replay of the
// case (the same cascade the panel counts); never below the people still out.
export function reportPeople(report) {
  const ev = report?.event || {}
  const stillOut = Number(ev.people) || 0
  const hit = Math.max(stillOut, Number(ev.people_hit ?? report?.replay?.people_hit) || 0)
  return { hit, stillOut }
}

// The same two figures from a cascade on the map (the counter's final value and the load still cut).
export function cascadePeople(cascade) {
  if (!cascade) return { hit: 0, stillOut: 0 }
  const last = cascade.steps?.at(-1)
  const stillOut = Number(cascade.people) || 0
  const hit = Math.max(stillOut, Number(last?.people_hit ?? last?.people_zone ?? 0) || 0, Number(cascade.people_hit ?? cascade.people_zone ?? 0) || 0)
  return { hit, stillOut }
}

export const homesOf = (people) => Math.round((Number(people) || 0) / PEOPLE_PER_HOME)
