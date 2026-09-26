// "Who goes dark first?" on the map (a GridMap child: SVG inside the camera, App.jsx mounts it with the demo's
// layers). The base map already shows the end state of the rule the case ran under (flexible: the plain
// cascade). Picking another rule in the results column (DarkFirst.jsx) crossfades to that rule's end state:
//   keep the campus on  the people cut around the campus (hatched, the base map's own hatch), the line that still
//                       tripped (dashed red), the lines the operator held at their limit by cutting load (amber),
//                       the campus kept on;
//   steps down first    calm: no hatch, no trip; the line that sets the room at its limit (amber) and the campus
//                       ring in the solution green, filled to its share ("557 MW · 37 %"); when the grid is past its
//                       limits even without it (a storm, a heat wave), the campus switches off: a dotted grey ring,
//                       "Switched off", and that outage's dark areas;
//   nobody planned      (only when the case ran firm) the plain cascade's dark areas and trips.
// While another rule is on screen the map box gets .df-alt, and darkfirst.css fades the base map's end state
// (its dark areas, trips, embers, town labels, the blast's afterglow) back to the calm grid under this layer.
// Sizes are screen px divided by the zoom, like the other layers. Reduced motion: no fades.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMapView } from '../../GridMap'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { plantsOut } from '../fix/flipCase'
import { textWidth } from '../impact/towns'
import { RULES, allCut, baseRule, prefetchRules, rulesBody, rulesKey, shownRule, useDarkFirst } from './rulesStore'
import './darkfirst.css'

// GridMap's zoom buckets: its dark areas are 7 / sqrt(bucket) map units; these match them
const bucketOf = (k) => (k < 1.5 ? 1 : k < 2.5 ? 2 : k < 4 ? 3 : k < 6 ? 5 : 8)
const DARK_SHARE = 0.6 // a substation that lost this share of its load reads as dark (the store's rule)
const LABEL_PX = 12
const RING_PX = 20

export default function DarkFirstLayer() {
  const O = useOverload()
  const { cascade, caseBody, playing, subById, branchById, loadFactor } = O
  const { k, project } = useMapView()
  const s = useDarkFirst()
  const body = useMemo(() => (plantsOut(cascade) ? null : rulesBody(caseBody, cascade)), [caseBody, cascade])
  const key = rulesKey(body)
  // ask as soon as the cascade lands (never before it: LAZY), when it hits anyone: the block's numbers are ready
  // when the replay ends
  const hits = !!cascade && (cascade.people_zone ?? cascade.people ?? 0) > 0
  useEffect(() => {
    if (key && hits) prefetchRules(JSON.parse(key))
  }, [key, hits])

  const data = s.key === key && s.status === 'done' ? s.data : null
  const on = !!data && s.active > 0 && !playing
  const base = baseRule(cascade)
  const shown = on ? shownRule(s, key, base) : base
  const alt = on && shown !== base

  // the map box: .df-on while the block is showing, .df-alt while another rule than the case's is on screen
  const [host, setHost] = useState(null)
  const attach = useCallback((el) => setHost(el?.ownerSVGElement?.parentElement || null), [])
  useEffect(() => {
    if (!host) return undefined
    host.classList.toggle('df-on', on)
    host.classList.toggle('df-alt', alt)
    return () => host.classList.remove('df-on', 'df-alt')
  }, [host, on, alt])

  // every rule's end state, positioned in map units (the zoom only changes sizes, applied at draw time)
  const ends = useMemo(() => {
    if (!data) return null
    const xy = (id) => {
      const sub = subById.get(id)
      return sub ? project(sub.lon, sub.lat) : null
    }
    const seg = (bid) => {
      const b = branchById.get(bid)
      if (!b || b.from_sub === b.to_sub) return null
      const a = xy(b.from_sub)
      const z = xy(b.to_sub)
      return a && z ? { id: bid, x1: a[0], y1: a[1], x2: z[0], y2: z[1] } : null
    }
    const lf = Number(data.load_factor) || loadFactor || 1
    const out = {}
    for (const r of data.rules) {
      const e = r.end || {}
      const darkSet = new Set(e.dark_subs || [])
      const areas = []
      let cx = 0
      let cy = 0
      let w = 0
      for (const [sid, lost] of Object.entries(e.affected || {})) {
        const id = Number(sid)
        const sub = subById.get(id)
        const p = xy(id)
        if (!sub || !p) continue
        const full = darkSet.has(id) || lost >= DARK_SHARE * Math.max(sub.load_mw * lf, 0.1)
        areas.push({ id, x: p[0], y: p[1], full })
        const wt = Math.max(Number(lost) || 0, 0.1)
        cx += p[0] * wt
        cy += p[1] * wt
        w += wt
      }
      out[r.key] = {
        r,
        areas,
        center: w > 0 ? [cx / w, cy / w] : null,
        tripped: [...(e.storm || []), ...(e.tripped || [])].map(seg).filter(Boolean),
        held: (e.held || []).map(seg).filter(Boolean),
      }
    }
    out.limit = data.step_down?.limit_line != null ? seg(data.step_down.limit_line) : null
    out.campuses = (data.sites || []).map((x) => ({ ...x, p: xy(x.sub) })).filter((x) => x.p)
    return out
  }, [data, subById, branchById, project, loadFactor])

  if (!ends) return <g ref={attach} className="df-map" aria-hidden="true" />
  const u = 1 / k
  const bucket = bucketOf(k)
  const R = 7 / Math.sqrt(bucket)
  return (
    <g ref={attach} className={`df-map${on ? ' df-map--on' : ''}`} aria-hidden="true" pointerEvents="none">
      <defs>
        <radialGradient id="df-scrim">
          <stop offset="0" className="df-scrim__core" />
          <stop offset="0.72" className="df-scrim__mid" />
          <stop offset="1" className="df-scrim__edge" />
        </radialGradient>
      </defs>
      {RULES.map(({ key: rk }) => {
        if (rk === base) return null // the base map shows it
        const st = ends[rk]
        if (!st) return null
        return (
          <g key={rk} className={`df-state${alt && shown === rk ? ' df-state--on' : ''}`}>
            <State st={st} rk={rk} ends={ends} data={data} u={u} R={R} />
          </g>
        )
      })}
    </g>
  )
}

function State({ st, rk, ends, data, u, R }) {
  const { r } = st
  const many = (data.sites?.length || 1) > 1
  const main = ends.campuses[0]
  const off = !!data.step_down?.over_without // stepping down means switching off here: no green, no "0 MW · 0 %"
  return (
    <>
      {st.areas.length > 0 && (
        <g className="df-areas">
          {st.areas.map((a) => (
            <circle key={a.id} cx={a.x} cy={a.y} r={a.full ? R : R * 0.65} />
          ))}
        </g>
      )}
      {st.tripped.map((l) => (
        <line key={`t${l.id}`} className="df-tripped" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} />
      ))}
      {st.held.map((l) => (
        <line key={`h${l.id}`} className="df-held" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} />
      ))}
      {rk === 'step_down' && ends.limit && <line className="df-held df-held--limit" x1={ends.limit.x1} y1={ends.limit.y1} x2={ends.limit.x2} y2={ends.limit.y2} />}
      {ends.campuses.map((c, i) => (
        // two ctrl+click campuses can snap to the same substation: the index keeps each key unique
        <Campus key={`${i}-${c.sub}`} c={c} rk={rk} r={r} u={u} off={rk === 'step_down' && off} />
      ))}
      {/* the rule in one label: under the main campus (clear of the city names), and the people it costs where they are */}
      {main && rk === 'step_down' && off && (
        <Label x={main.p[0]} y={main.p[1] + (RING_PX + 17) * u} u={u} anchor="middle" cls="df-lab df-lab--idle">
          {many ? 'Switched off' : `Switched off · 0 of ${fmt(data.mw)} MW`}
        </Label>
      )}
      {main && rk === 'step_down' && !off && (
        <Label x={main.p[0]} y={main.p[1] + (RING_PX + 17) * u} u={u} anchor="middle" cls="df-lab df-lab--ok">
          {many ? `${Math.round(data.step_down.share_pct)} % of their size` : `${fmt(data.step_down.level_mw)} MW · ${Math.round(data.step_down.share_pct)} %`}
        </Label>
      )}
      {main && rk === 'firm' && r.firm_held && (
        <Label x={main.p[0]} y={main.p[1] + (RING_PX + 17) * u} u={u} anchor="middle" cls="df-lab">
          {many ? 'Kept on' : `Kept on · ${fmt(data.mw)} MW`}
        </Label>
      )}
      {main && rk === 'flexible' && r.campus_cut_off && (
        <Label x={main.p[0]} y={main.p[1] + (RING_PX + 17) * u} u={u} anchor="middle" cls="df-lab df-lab--loss">
          Cut off too
        </Label>
      )}
      {st.center && r.people_dark > 0 && (
        <Label x={st.center[0]} y={st.center[1] + (rk === 'step_down' ? 0 : 26 * u)} u={u} cls="df-lab df-lab--loss" anchor="middle">
          {/* "cut" only when everyone dark was cut on purpose for the campus (a storm's victims are not) */}
          {`${fmt(r.people_dark)} ${allCut(r) ? 'people cut' : 'people dark'}`}
        </Label>
      )}
    </>
  )
}

// A campus under a rule: kept on (a plain ring), stepped down (the green ring filled to its share), cut off (dashed red),
// switched off because the grid is past its limits without it (a dotted grey ring: neither a loss nor a solution)
function Campus({ c, rk, r, u, off }) {
  const [x, y] = c.p
  const rad = RING_PX * u
  if (off)
    return (
      <g className="df-campus df-campus--idle" transform={`translate(${x} ${y})`}>
        <circle className="df-campus__track" r={rad} />
      </g>
    )
  if (rk === 'step_down') {
    const share = c.mw > 0 ? Math.max(0, Math.min(1, c.step_down_mw / c.mw)) : 0
    return (
      <g className="df-campus df-campus--ok" transform={`translate(${x} ${y})`}>
        <circle className="df-campus__track" r={rad} />
        {/* pathLength 100: the arc is the share of the campus that keeps running, from 12 o'clock */}
        <circle className="df-campus__arc" r={rad} pathLength={100} strokeDasharray={`${(share * 100).toFixed(2)} 100`} transform="rotate(-90)" />
      </g>
    )
  }
  const lost = rk === 'flexible' ? r.campus_cut_off : r.firm_held === false
  return (
    <g className={`df-campus${lost ? ' df-campus--off' : ''}`} transform={`translate(${x} ${y})`}>
      <circle className="df-campus__track" r={rad} />
    </g>
  )
}

// quiet text over a soft dark scrim (like the town labels: no outline, no box)
function Label({ x, y, u, cls, anchor = 'start', children }) {
  const text = String(children)
  const w = textWidth(text, LABEL_PX, 700, 'condensed') * u
  const x0 = anchor === 'middle' ? x - w / 2 : x
  return (
    <g className="df-label">
      <ellipse cx={x0 + w / 2} cy={y - LABEL_PX * 0.3 * u} rx={w / 2 + 14 * u} ry={LABEL_PX * 0.9 * u} fill="url(#df-scrim)" />
      <text className={cls} x={x} y={y} fontSize={LABEL_PX * u} textAnchor={anchor}>
        {text}
      </text>
    </g>
  )
}
