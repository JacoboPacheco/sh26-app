// What App needs for the map while the Strengthen page is on (App.jsx, MissionControl): the lines, the click,
// and staying on the page across a state change. Kept apart from StrengthenPage.jsx (components only).
import { useLayoutEffect, useMemo, useRef } from 'react'
import { loadClass, regionAt } from '../../geo'
import { select } from './unlockStore'

// The map on this page is the grid itself: lines by today's loading, capped at "warm" (amber and red mean the
// study's weak points and nothing else here), no demo campus, no dark areas.
export const NO_SUBS = {}
export function useStrengthenLines(grid) {
  return useMemo(() => (grid?.branches || []).map((b) => loadClass(Math.min(b.base_pct ?? 0, 79.9))), [grid])
}

// A click on the map here: on the U.S. map it opens that state (and stays on this page); on a state it clears
// the selection (sites and weak points pick themselves).
export function strengthenClick(o) {
  return (lat, lon) => {
    if (o.region === 'US') {
      const code = regionAt(lat, lon)
      if (code && code !== 'DC') o.setRegion(code)
      return
    }
    select(null)
  }
}

// A state change resets the demo's case and its mode; on this page the viewer stays on Strengthen.
export function useStayOnStrengthen({ region, mode, setMode }) {
  const prev = useRef({ region, mode })
  useLayoutEffect(() => {
    const p = prev.current
    const stay = p.region !== region && p.mode === 'unlock' && mode !== 'unlock'
    if (stay) setMode('unlock')
    prev.current = { region, mode: stay ? 'unlock' : mode }
  }, [region, mode, setMode])
}
