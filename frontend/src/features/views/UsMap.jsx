import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MAP_BOX, STATE_SHAPES, boxForStates, sitePoint } from './albers'
import { Tip } from './charts'
import { STATUS_LABEL, fmt, markRadius, mwText, useTip, useWidth } from './viewsKit'

// The national map: every state's outline, one mark per data center. Mark area follows the reported MW, its
// fill and outline follow the status (so status never rests on color alone), a square is a crypto-mining site.
// Wheel to zoom, drag to pan, click a state to filter to it, click a mark for its card. The map eases to the
// selected states. Marks are drawn biggest first so small ones stay visible on top.

const RATIO = MAP_BOX.w / MAP_BOX.h
const MAX_ZOOM = 18

const statusClass = (s) => `vw-mk--${String(s).replace(/[^a-z]+/g, '-')}`

const Marks = memo(function Marks({ pts, unit }) {
  return (
    <g className="vw-marks">
      {pts.map((p) =>
        p.crypto ? (
          <rect key={p.id} data-id={p.id} x={p.x - p.r * unit} y={p.y - p.r * unit} width={2 * p.r * unit} height={2 * p.r * unit} className={`vw-mk ${statusClass(p.status)}`} />
        ) : (
          <circle key={p.id} data-id={p.id} cx={p.x} cy={p.y} r={p.r * unit} className={`vw-mk ${statusClass(p.status)}`} />
        ),
      )}
    </g>
  )
})

const ease = (t) => 1 - (1 - t) ** 3

export default function UsMap({ sites, stateFilter, selectedId, onSelect, onToggleState, loading }) {
  const [boxRef, width] = useWidth()
  const { tip, show, hide } = useTip(boxRef)
  const svgRef = useRef(null)
  const [vb, setVb] = useState(MAP_BOX)
  const vbRef = useRef(vb)
  vbRef.current = vb
  const widthRef = useRef(width)
  widthRef.current = width
  const drag = useRef(null)
  const moved = useRef(false)
  const raf = useRef(0)
  const unit = width ? vb.w / width : 1

  const byId = useMemo(() => new Map(sites.map((s) => [s.id, s])), [sites])
  const pts = useMemo(
    () =>
      sites
        .map((s) => {
          const p = sitePoint(s)
          return p ? { id: s.id, x: p[0], y: p[1], r: markRadius(s.mw), status: s.status, crypto: s.kind === 'crypto_mining' } : null
        })
        .filter(Boolean),
    [sites],
  )
  const selected = selectedId ? byId.get(selectedId) : null
  const selPt = selected ? sitePoint(selected) : null

  // ease the view to a box
  const flyTo = useCallback((target) => {
    cancelAnimationFrame(raf.current)
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setVb(target)
      return
    }
    const from = vbRef.current
    const t0 = performance.now()
    const tick = (now) => {
      const k = ease(Math.min(1, (now - t0) / 650))
      setVb({ x: from.x + (target.x - from.x) * k, y: from.y + (target.y - from.y) * k, w: from.w + (target.w - from.w) * k, h: from.h + (target.h - from.h) * k })
      if (k < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
  }, [])
  useEffect(() => () => cancelAnimationFrame(raf.current), [])

  // the selected states frame the view; none selected shows the whole country
  const key = stateFilter.join(',')
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      if (!stateFilter.length) return
    }
    flyTo(boxForStates(stateFilter))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, flyTo])

  const clamp = (b) => {
    const w = Math.min(MAP_BOX.w, Math.max(MAP_BOX.w / MAX_ZOOM, b.w))
    const h = w / RATIO
    const x = Math.min(MAP_BOX.x + MAP_BOX.w - w * 0.25, Math.max(MAP_BOX.x - w * 0.75, b.x))
    const y = Math.min(MAP_BOX.y + MAP_BOX.h - h * 0.25, Math.max(MAP_BOX.y - h * 0.75, b.y))
    return { x, y, w, h }
  }

  const zoomAt = useCallback((clientX, clientY, factor) => {
    const el = svgRef.current
    if (!el) return
    cancelAnimationFrame(raf.current)
    const r = el.getBoundingClientRect()
    const cur = vbRef.current
    const u = cur.w / r.width
    const mx = cur.x + (clientX - r.left) * u
    const my = cur.y + (clientY - r.top) * u
    const w = Math.min(MAP_BOX.w, Math.max(MAP_BOX.w / MAX_ZOOM, cur.w * factor))
    const k = w / cur.w
    setVb(clamp({ x: mx - (mx - cur.x) * k, y: my - (my - cur.y) * k, w, h: w / RATIO }))
  }, [])

  // a wheel listener that can preventDefault (React's onWheel is passive)
  useEffect(() => {
    const el = svgRef.current
    if (!el) return undefined
    const on = (e) => {
      e.preventDefault()
      zoomAt(e.clientX, e.clientY, Math.exp(e.deltaY * 0.0016))
    }
    el.addEventListener('wheel', on, { passive: false })
    return () => el.removeEventListener('wheel', on)
  }, [zoomAt, width])

  const zoomButton = (factor) => {
    const r = svgRef.current?.getBoundingClientRect()
    if (r) zoomAt(r.left + r.width / 2, r.top + r.height / 2, factor)
  }

  const onPointerDown = (e) => {
    if (e.button !== 0) return
    moved.current = false
    drag.current = { x: e.clientX, y: e.clientY, vb: vbRef.current, captured: false, id: e.pointerId }
  }
  const onPointerMove = (e) => {
    const d = drag.current
    if (!d) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    if (!moved.current && Math.hypot(dx, dy) < 5) return
    if (!d.captured) {
      d.captured = true
      cancelAnimationFrame(raf.current)
      try {
        svgRef.current.setPointerCapture(d.id)
      } catch {
        /* the pointer is already gone */
      }
      hide()
    }
    moved.current = true
    const u = d.vb.w / (widthRef.current || 1)
    setVb(clamp({ ...d.vb, x: d.vb.x - dx * u, y: d.vb.y - dy * u }))
  }
  const endDrag = () => {
    drag.current = null
    window.setTimeout(() => {
      moved.current = false
    }, 0)
  }

  const siteTip = (id, e) => {
    const s = byId.get(id)
    if (!s) return
    show(
      e,
      <>
        <strong>{s.name}</strong>
        <span className="vw-tip__row">
          <span>{s.company}</span>
          <span>{s.state}</span>
        </span>
        <span className="vw-tip__row">
          <span>{STATUS_LABEL[s.status] || s.status}</span>
          <span>{s.mw ? `${fmt(s.mw)} MW reported` : 'size not reported'}</span>
        </span>
      </>,
    )
  }
  const onMarksOver = (e) => {
    if (drag.current?.captured) return
    const id = e.target.dataset?.id
    if (id) siteTip(id, e)
  }
  const onMarksMove = (e) => {
    if (drag.current?.captured) return
    const id = e.target.dataset?.id
    if (id) siteTip(id, e)
    else hide()
  }
  const onMarksClick = (e) => {
    if (moved.current) return
    const id = e.target.dataset?.id
    if (id) {
      e.stopPropagation()
      onSelect(id === selectedId ? null : id)
    }
  }

  const sel = new Set(stateFilter)
  return (
    <div className={loading ? 'vw-map vw-map--loading' : 'vw-map'} ref={boxRef}>
      <svg
        ref={svgRef}
        className="vw-map__svg"
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        role="group"
        aria-label={`Map of the lower 48 states with ${fmt(pts.length)} data center sites. The list below the map has the same sites.`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={hide}
      >
        <g className="vw-states">
          {STATE_SHAPES.map((s) => (
            <path
              key={s.code}
              d={s.d}
              className={sel.has(s.code) ? 'vw-state vw-state--on' : 'vw-state'}
              vectorEffect="non-scaling-stroke"
              onClick={() => {
                if (!moved.current) onToggleState(s.code)
              }}
            >
              <title>{`${s.name}: click to ${sel.has(s.code) ? 'remove the filter' : 'show only this state'}`}</title>
            </path>
          ))}
        </g>
        <g onPointerOver={onMarksOver} onPointerMove={onMarksMove} onPointerOut={hide} onClick={onMarksClick}>
          <Marks pts={pts} unit={unit} />
        </g>
        {selPt && selected && (
          <circle cx={selPt[0]} cy={selPt[1]} r={(markRadius(selected.mw) + 4) * unit} className="vw-mk-ring" vectorEffect="non-scaling-stroke" pointerEvents="none" />
        )}
      </svg>
      <div className="vw-map__zoom">
        <button type="button" className="vw-iconbtn" aria-label="Zoom in" onClick={() => zoomButton(0.6)}>
          +
        </button>
        <button type="button" className="vw-iconbtn" aria-label="Zoom out" onClick={() => zoomButton(1.7)}>
          −
        </button>
        <button type="button" className="vw-iconbtn vw-iconbtn--text" onClick={() => flyTo(boxForStates(stateFilter))}>
          {stateFilter.length ? 'Fit selection' : 'Reset view'}
        </button>
      </div>
      <Tip tip={tip} />
    </div>
  )
}

// The key to the marks: status = fill and outline, size = area, square = crypto mining.
export function MapLegend({ crypto }) {
  const item = (cls, label, sq) => (
    <li>
      <svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true">
        {sq ? <rect x="-4.5" y="-4.5" width="9" height="9" className={`vw-mk ${cls}`} /> : <circle r="5.5" className={`vw-mk ${cls}`} />}
      </svg>
      {label}
    </li>
  )
  return (
    <div className="vw-maplegend">
      <ul aria-label="Marks by status">
        {item('vw-mk--operating', 'Operating')}
        {item('vw-mk--under-construction', 'Under construction')}
        {item('vw-mk--announced', 'Announced or proposed')}
        {item('vw-mk--paused-canceled', 'Paused or canceled')}
        {item('vw-mk--unknown', 'Status not reported')}
        {crypto && item('vw-mk--operating', 'Crypto-mining site', true)}
      </ul>
      <div className="vw-maplegend__size" aria-label="Mark size by reported capacity">
        <span>Area follows reported size</span>
        {[100, 1000, 10000].map((mw) => (
          <span key={mw} className="vw-maplegend__dot">
            <svg width={2 * markRadius(mw) + 2} height={2 * markRadius(mw) + 2} aria-hidden="true">
              <circle cx={markRadius(mw) + 1} cy={markRadius(mw) + 1} r={markRadius(mw)} className="vw-mk vw-mk--ref" />
            </svg>
            {mwText(mw)}
          </span>
        ))}
      </div>
    </div>
  )
}
