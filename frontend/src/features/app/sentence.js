import { presetFor } from '../heat/presets'
import { fmt } from '../../geo'

// The scenario written as one question — the workspace's title. Pure: every part comes from the
// case in the store (and the what-if's substation name once it's solved).
//   "What if a 1,500 MW data center plugs in at Fort Myers at 4 PM?"
//   "What if a 500 MW data center plugs in at Fort Myers during a heat wave, on firm service?"
//   "What if a hurricane knocks out 34 lines in Florida at 4 PM?"

export function timePhrase(loadFactor) {
  const p = presetFor(loadFactor)
  if (!p) return `at ${Math.round(loadFactor * 100)} % of the summer peak`
  if (p.id === 'wave') return 'during a heat wave'
  return `at ${p.label}`
}

export function scenarioSentence({ regionName, site, mw, area, extraSites = [], loadFactor = 1, trip = [], upgrades = {}, firm = false }) {
  const where = regionName || 'this state'
  const extraMw = extraSites.reduce((a, s) => a + (Number(s.mw) || 0), 0)
  const nUp = Object.keys(upgrades || {}).length
  const parts = []
  if (site) {
    parts.push(area ? `a ${fmt(mw)} MW data center plugs in at ${area}` : `a ${fmt(mw)} MW data center plugs into ${where}'s grid`)
    if (extraSites.length) parts.push(`next to ${extraSites.length} more ${extraSites.length === 1 ? 'campus' : 'campuses'} (${fmt(extraMw)} MW)`)
  } else if (extraSites.length) {
    parts.push(`${extraSites.length} AI ${extraSites.length === 1 ? 'campus plugs' : 'campuses plug'} in across ${where} (${fmt(extraMw)} MW)`)
  }
  if (trip.length) parts.push(site || extraSites.length ? `while a hurricane knocks out ${fmt(trip.length)} ${trip.length === 1 ? 'line' : 'lines'}` : `a hurricane knocks out ${fmt(trip.length)} ${trip.length === 1 ? 'line' : 'lines'} in ${where}`)
  if (!parts.length) {
    if (loadFactor !== 1) return `What if ${where}'s grid runs ${timePhrase(loadFactor)} with nothing added?`
    return null // nothing in the case yet
  }
  let s = `What if ${parts.join(' ')} ${timePhrase(loadFactor)}`
  if (nUp) s += `, after ${nUp} line ${nUp === 1 ? 'upgrade' : 'upgrades'}`
  if (firm && (site || extraSites.length)) s += ', on firm service'
  return `${s}?`
}

// "FORT MYERS 3" -> "Fort Myers" (the town a synthetic substation is named after)
export function areaName(name) {
  return String(name || '')
    .replace(/\s+\d+$/, '')
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}
