// Picking a campus of the capacity plan (from the plan, the meter or the map): select it (its card opens) and fly
// the map to it with its upgrades. On a wide screen the card opens over the map's right side, so the camera frames
// the campus left of center: an empty point east of it widens the frame (the map centers on the frame's middle).
import { select, settleCap } from './unlockStore'

export function pickCampus(o, m, n) {
  const st = m?.steps?.[n - 1]
  if (!st) return
  settleCap() // a pick mid build-up finishes it: the card describes the plan as the budget buys it
  select({ type: 'cap', id: n })
  flyTo(o, [[st.site.lon, st.site.lat], ...st.projects.flatMap((p) => [[p.from.lon, p.from.lat], [p.to.lon, p.to.lat]])])
}

export function flyTo(o, pts) {
  if (!pts.length) return
  let wide = false
  try {
    wide = window.matchMedia('(min-width: 861px)').matches
  } catch {
    wide = false
  }
  const out = [...pts]
  if (wide) {
    const lons = pts.map((q) => q[0])
    const lats = pts.map((q) => q[1])
    const span = Math.max(Math.max(...lons) - Math.min(...lons), 0.9)
    out.push([Math.max(...lons) + span * 0.85, (Math.min(...lats) + Math.max(...lats)) / 2])
  }
  o.focus(out)
}
