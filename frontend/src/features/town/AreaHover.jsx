import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { STATES, fmt, project, regionAt, unproject } from '../../geo'
import { townOf, useOverload } from '../../store'
import { peopleNum } from './areaStore'
import './town.css'

const HIT_PX = 10 // how close (screen px) the pointer must be to a light to name it
const RING_PX = 7 // the hovered light's ring
const GAP_PX = 12 // from the light to the tooltip

// Every light named by its area on hover: a GridMap child that watches the pointer over the map,
// finds the nearest substation within HIT_PX, rings it, and shows a small tooltip (HTML, portaled
// into the map's container) with the area, the substation and whether it has power. On the national
// map it names the state under the pointer. Pointer-only and pointer-events: none, so clicks, drags
// and map tools pass through untouched; touch is ignored. The tooltip stays glued to its light
// while the camera zooms or eases.
export default function AreaHover({ states = true }) {
  const o = useOverload()
  const anchor = useRef(null) // an empty <g> in the camera: its screen matrix maps map units to px
  const tipRef = useRef(null)
  const ringRef = useRef(null)
  const pos = useRef([0, 0]) // map units the tooltip points at
  const [host, setHost] = useState(null) // the map's container (the svg's parent)
  const [hovered, setHover] = useState(null) // {kind: 'sub', id, x, y, pts} | {kind: 'state', code, pts}
  const grid = o.grid
  const region = grid?.meta?.region || 'FL'
  const national = region === 'US'

  // positions in the current projection (the store sets it before handing the map a new grid)
  const pts = useMemo(
    () =>
      (grid?.subs || []).map((s) => {
        const [x, y] = project(s.lon, s.lat)
        return { id: s.id, x, y }
      }),
    [grid, region], // eslint-disable-line react-hooks/exhaustive-deps -- region: the projection changed
  )
  const ptsRef = useRef(pts)
  useEffect(() => {
    ptsRef.current = pts
  }, [pts])
  // a hover found on another grid (a region change) is stale: nothing shows until the pointer moves
  const hover = hovered && hovered.pts === pts ? hovered : null

  useLayoutEffect(() => {
    setHost(anchor.current?.ownerSVGElement?.parentElement || null)
  }, [])

  // find what's under the pointer (at most once a frame)
  useEffect(() => {
    const g = anchor.current
    const svg = g?.ownerSVGElement
    if (!svg) return undefined
    let raf = 0
    let last = null
    const find = () => {
      raf = 0
      const e = last
      const ctm = g.getScreenCTM()
      if (!e || !ctm) return
      const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse())
      const r = HIT_PX / ctm.a
      let best = null
      let bd = r * r
      for (const q of ptsRef.current) {
        const dx = q.x - p.x
        const dy = q.y - p.y
        const dd = dx * dx + dy * dy
        if (dd < bd) {
          bd = dd
          best = q
        }
      }
      if (best) {
        pos.current = [best.x, best.y]
        const now = ptsRef.current
        setHover((h) => (h?.kind === 'sub' && h.id === best.id && h.pts === now ? h : { kind: 'sub', id: best.id, x: best.x, y: best.y, pts: now }))
        return
      }
      if (national && states) {
        const { lat, lon } = unproject(p.x, p.y)
        const code = regionAt(lat, lon)
        if (code && code !== 'DC' && STATES[code]) {
          pos.current = [p.x, p.y] // follows the pointer
          const now = ptsRef.current
          setHover((h) => (h?.kind === 'state' && h.code === code && h.pts === now ? h : { kind: 'state', code, pts: now }))
          return
        }
      }
      setHover(null)
    }
    const onMove = (e) => {
      if (e.pointerType === 'touch' || e.buttons) {
        last = null
        setHover(null)
        return
      }
      last = e
      if (!raf) raf = requestAnimationFrame(find)
    }
    const onLeave = () => {
      last = null
      setHover(null)
    }
    svg.addEventListener('pointermove', onMove)
    svg.addEventListener('pointerdown', onLeave)
    svg.addEventListener('pointerleave', onLeave)
    return () => {
      cancelAnimationFrame(raf)
      svg.removeEventListener('pointermove', onMove)
      svg.removeEventListener('pointerdown', onLeave)
      svg.removeEventListener('pointerleave', onLeave)
    }
  }, [national, states])

  // keep the tooltip on its light every frame while it shows (zoom, camera eases)
  useEffect(() => {
    if (!hover || !host) return undefined
    let raf = 0
    const tick = () => {
      const ctm = anchor.current?.getScreenCTM()
      const tip = tipRef.current
      if (ctm && tip) {
        const box = host.getBoundingClientRect()
        const [x, y] = pos.current
        const sp = new DOMPoint(x, y).matrixTransform(ctm)
        const w = tip.offsetWidth
        const h = tip.offsetHeight
        let left = sp.x - box.left + GAP_PX
        let top = sp.y - box.top - GAP_PX - h
        if (left + w > box.width - 8) left = sp.x - box.left - GAP_PX - w
        if (top < 8) top = sp.y - box.top + GAP_PX
        tip.style.transform = `translate(${Math.round(Math.max(4, left))}px, ${Math.round(top)}px)`
        tip.style.visibility = 'visible'
        ringRef.current?.setAttribute('r', String(RING_PX / ctm.a))
        ringRef.current?.setAttribute('stroke-width', String(1.4 / ctm.a))
      }
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [hover, host])

  const sub = hover?.kind === 'sub' ? o.subById.get(hover.id) : null
  let tip = null
  if (sub) tip = <SubTip s={sub} o={o} />
  else if (hover?.kind === 'state') tip = <StateTip code={hover.code} />

  return (
    <g ref={anchor} className="area-hover" pointerEvents="none" aria-hidden="true">
      {sub && <circle ref={ringRef} className="area-hover__ring" cx={hover.x} cy={hover.y} r={RING_PX} />}
      {host &&
        tip &&
        createPortal(
          <div ref={tipRef} className="area-tip" role="tooltip" style={{ visibility: 'hidden' }}>
            {tip}
          </div>,
          host,
        )}
    </g>
  )
}

function SubTip({ s, o }) {
  const name = s.area || townOf(s.name)
  const cls = o.view?.subClasses?.[s.id]
  const people = s.load_mw * (o.peoplePerMw || 0)
  let status = null
  if (cls === 'sub--dark') status = <span className="area-tip__status area-tip__status--dark">Without power</span>
  else if (cls === 'sub--dim') status = <span className="area-tip__status area-tip__status--dim">Partly without power</span>
  else if (o.headroomOn && o.headroom && o.headroom[s.id] !== undefined) {
    const h = o.headroom[s.id]
    status = <span className="area-tip__status">{h >= 5000 ? 'Room for 5,000 MW or more' : `Room for about ${fmt(h)} MW`}</span>
  }
  return (
    <>
      <strong className="area-tip__name">{name}</strong>
      {status}
      <span className="area-tip__meta">
        {s.name} · {people >= 5 ? `about ${peopleNum(people)} people (estimate)` : 'no local customers'}
      </span>
    </>
  )
}

function StateTip({ code }) {
  return (
    <>
      <strong className="area-tip__name">{STATES[code].name}</strong>
      <span className="area-tip__meta">Click to open its synthetic grid model</span>
    </>
  )
}
