import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'

// Small shared pieces of the Views page: number and status words, the sizes a chart reads from its container,
// and the deep links into the Overload map.

export const fmt = (n) => Math.round(Number(n) || 0).toLocaleString('en-US')
export const fmt1 = (n) => (Math.round((Number(n) || 0) * 10) / 10).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 1 })

// 39431263 -> "39.4M", 66797 -> "66.8k", 512 -> "512"
export function compact(n) {
  const v = Number(n) || 0
  const a = Math.abs(v)
  if (a >= 1e9) return `${Number((v / 1e9).toPrecision(3))}B`
  if (a >= 1e6) return `${Number((v / 1e6).toPrecision(3))}M`
  if (a >= 1e4) return `${Number((v / 1e3).toPrecision(3))}k`
  return fmt(v)
}

// 1200 -> "1,200 MW", 29825 -> "29.8 GW"
export function mwText(n) {
  const v = Number(n) || 0
  return v >= 10000 ? `${Number((v / 1000).toPrecision(3)).toLocaleString('en-US')} GW` : `${fmt(v)} MW`
}

export const STATUS_LABEL = {
  operating: 'Operating',
  'under construction': 'Under construction',
  announced: 'Announced or proposed',
  'paused/canceled': 'Paused or canceled',
  unknown: 'Status not reported',
}
export const STATUS_ORDER = ['operating', 'under construction', 'announced', 'paused/canceled', 'unknown']
export const DEFAULT_STATUSES = ['operating', 'under construction', 'announced', 'unknown']

export const ORIGIN_LABEL = { curated: 'Overload catalog', 'compute-atlas': 'Compute Atlas', 'epoch-ai': 'Epoch AI' }

// A ref for an element and its current width in pixels (charts draw to the width they are given)
export function useWidth() {
  const ref = useRef(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    setW(Math.round(el.getBoundingClientRect().width))
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

// The tooltip a chart shows near the pointer: {x, y, content} relative to the chart's box.
export function useTip(boxRef) {
  const [tip, setTip] = useState(null)
  const show = (e, content) => {
    const b = boxRef.current?.getBoundingClientRect()
    if (!b) return
    setTip({ x: e.clientX - b.left, y: e.clientY - b.top, w: b.width, content })
  }
  const hide = () => setTip(null)
  return { tip, show, hide }
}

// The Overload map with a campus of a site's reported size placed at its reported point. The app shell reads
// the query once on entry (features/app/router.js): `dc` is only added for the curated catalog's own ids.
export function testHref(site) {
  if (!site?.state || site.lat == null || site.lon == null || !site.mw) return null
  const q = new URLSearchParams({ lat: Number(site.lat).toFixed(4), lon: Number(site.lon).toFixed(4), mw: String(Math.min(Math.round(site.mw), 50000)) })
  if (site.origin === 'curated') q.set('dc', site.id)
  return `#/next/state/${site.state}?${q.toString()}`
}

export const stateHref = (code) => `#/next/state/${code}`

// round-number ticks for a 0-based axis, ending at or past the largest value
export function niceTicks(max, want = 4) {
  if (!(max > 0)) return [0]
  const raw = max / want
  const pow = 10 ** Math.floor(Math.log10(raw))
  const f = raw / pow
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow
  const out = []
  for (let v = 0; ; v += step) {
    out.push(Math.round(v * 1e6) / 1e6)
    if (v >= max * 0.9999) break // the last tick reaches the largest value: no bar runs past the axis
  }
  return out
}

// A GET the page reads once and keeps for the visit (population and energy never change while it is open)
// (`retry` asks again after an error).
const memo = new Map()
export function useCached(path) {
  const [state, setState] = useState(() => ({ data: memo.get(path) || null, error: null }))
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    if (memo.has(path)) return undefined
    let live = true
    api(path)
      .then((d) => {
        memo.set(path, d)
        if (live) setState({ data: d, error: null })
      })
      .catch((e) => live && setState({ data: null, error: e }))
    return () => {
      live = false
    }
  }, [path, nonce])
  const retry = () => {
    setState({ data: null, error: null })
    setNonce((n) => n + 1)
  }
  return { ...state, retry }
}

// A mark's radius in pixels: area follows the reported MW (with a floor so a small site is still a mark, and a
// cap so a 10 GW campus doesn't hide its neighbours); a site with no reported size is a small fixed dot.
export const markRadius = (mw) => (mw ? Math.min(15, 2 + 0.115 * Math.sqrt(mw)) : 2.4)
