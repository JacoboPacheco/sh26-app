// The AI boom, year by year (backend/timelapse.py): GET /api/timelapse, a job keyed by (region, statuses) that
// the page polls at the same URL until it is done. Every call goes through api.js.
import { api } from '../../api'

export const STATUS_LABELS = {
  operating: 'Operating',
  'under construction': 'Under construction',
  announced: 'Announced',
  'paused/canceled': 'Paused or canceled',
}

export const DEFAULT_STATUSES = ['operating', 'under construction', 'announced']

export const getTimelapse = (region, statuses) =>
  api(`/api/timelapse?region=${encodeURIComponent(region)}&statuses=${encodeURIComponent(statuses.join(','))}`)

// Poll until the job is done (or errors). `alive()` stops it when the page closes or the question changes.
export async function loadTimelapse(region, statuses, alive, onWait) {
  for (;;) {
    const r = await getTimelapse(region, statuses)
    if (!alive()) return null
    if (r.state === 'done') return r
    if (r.state === 'error') throw new Error(r.detail || 'The engine could not finish this time-lapse.')
    onWait?.(r)
    await new Promise((res) => setTimeout(res, 700))
    if (!alive()) return null
  }
}

// "276 MW", "1.48 GW"
export function mwText(mw) {
  const v = Number(mw) || 0
  if (Math.abs(v) < 1000) return `${Math.round(v).toLocaleString('en-US')} MW`
  const gw = v / 1000
  return `${parseFloat(gw >= 10 ? gw.toFixed(1) : gw.toFixed(2))} GW`
}

export function moneyText(x) {
  const v = Number(x) || 0
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)} billion`
  if (v >= 1e6) return `$${Math.round(v / 1e6)} million`
  return `$${Math.round(v).toLocaleString('en-US')}`
}

export const reducedMotion = () => {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

// A loading % -> the map's class bucket: quiet, warming, strained (near the limit), over it.
export function tlClass(pct) {
  if (pct >= 100) return 'tl-ln tl-ln--over'
  if (pct >= 80) return 'tl-ln tl-ln--strain'
  if (pct >= 60) return 'tl-ln tl-ln--warm'
  return 'tl-ln tl-ln--calm'
}
