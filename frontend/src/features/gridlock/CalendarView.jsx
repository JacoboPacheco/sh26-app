import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { EmptyState, ErrorBanner, Loading } from '../../ui'
import { useGridlock } from './context'
import { downloadUrl } from './gridlockApi'
import { KIND_LABEL, MONTHS, UTILITIES, boundsOf, displayName, fmtDate, fmtInt, fmtKv, projectPoints, stationSentence, toneOf, utilityShort, whenOf } from './format'
import './calendar.css'

// The coordination calendar (Build together, "Calendar"): the two utilities' filed projects on one timeline, a row per
// project grouped by utility, and BETWEEN the two groups each flagged pair's shared build window: the months both plans
// are building, as filed. Every date is the engine's (GET /api/gridlock/calendar): the same windows the ranking scores,
// so a shared window here is exactly the pair's "shares N months" in the list. Wide, a Gantt; narrow (a phone), a list
// by year. Hovering a row finds the project (or the pair) on the map; a bar opens its card and its pairs; a shared window
// opens that pair's sheet. Exports: the whole calendar or one pair as .ics, and the Excel workbook's calendar sheet.

const GANTT_MIN = 560 // narrower than this, the calendar is a list by year (no sideways scrolling)
const GROUP_H = 34
const ROW_H = 26
const BAND_H = 56
const SHARED_H = 26
const STATION_H = 36 // a same-station row: its station, and under it "same station" (and Sperry's number)
const GAP_H = 10
const PAD_R = 20
const DAY = 86400000
const MAX_PAIRS_IN_CARD = 8

const ts = (iso) => Date.parse(`${iso.slice(0, 10)}T00:00:00Z`)
const year = (iso) => Number(iso.slice(0, 4))
const monthYear = (iso) => {
  if (!iso) return 'no date'
  const [y, m] = iso.split('-').map(Number)
  return `${MONTHS[m - 1]} ${y}`
}
const span = (s, e) => {
  const a = monthYear(s)
  const b = monthYear(e)
  return a === b ? a : `${a} – ${b}`
}
const months = (m) => `${Math.round(m)} month${Math.round(m) === 1 ? '' : 's'}`
const NOW_WORD = { future: 'still ahead', open: 'open now', past: 'passed as filed' }
const labelWidth = (w) => Math.round(Math.min(310, Math.max(180, w * 0.36)))
// a filed name cut to where it says which line or station (before its kV, a parenthesis, a colon or a comma), for the
// shared rows' two-name labels: 'Stevens Creek - Hooks 115kV/LR Plumb Branch 46kV Rebuilds' -> 'Stevens Creek - Hooks' (a sponsor
// prefix like 'SAV:' dropped; the row's title keeps the full names)
const shortName = (name) => {
  const s = (displayName(name) || '').replace(/^[A-Za-z]{2,5}:\s*/, '')
  const cut = s.search(/\s\d{2,3}(\/\d{2,3})?(-\d{2,3})?\s?kV|\s\(|,|:\s/)
  return (cut > 3 ? s.slice(0, cut) : s).trim()
}
// shorter still, for a narrow label: the first place a line names ('Urquhart – Toolebeck' -> 'Urquhart')
const placeName = (name) => shortName(name).split(/\s+[-–]\s+|(?<=[a-z])[-–](?=[A-Z])/)[0].trim()
// a project number without its utility prefix, to tell two near-identical filed names apart ('DESC-6809E' -> '6809E')
const idTail = (id) => String(id || '').replace(/^[A-Za-z]+-/, '')
const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const canHover = () => typeof window === 'undefined' || !window.matchMedia || window.matchMedia('(hover: hover)').matches
// the utilities whose side of a pair has a derived start (no start date in its filing), e.g. ['DESC']
const derivedOf = (w) => ['a', 'b'].filter((k) => w?.starts_as?.[k] === 'derived').map((k) => utilityShort(w[`${k}_utility`]))
const derivedWords = (d) => (d.length === 2 ? 'both starts derived' : `${d[0]} start derived`)
// days between two build windows, the way the list says it
const gapWords = (days) => {
  if (days == null) return 'timing unknown'
  if (days === 0) return 'build windows meet end to start'
  if (days < 60) return `${days} days apart`
  if (days < 730) return `${Math.round(days / 30.44)} months apart`
  return `${(days / 365.25).toFixed(1)} years apart`
}

// text width for fitting a shared row's two names into its label (canvas measure, the label's own font)
let measureCtx
function textWidth(text, font) {
  if (measureCtx === undefined) measureCtx = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null
  if (!measureCtx) return text.length * 6.4
  measureCtx.font = font
  return measureCtx.measureText(text).width
}
// a name's forms, longest first: the short name, the first place it names, then its first two words and first word
function nameForms(name) {
  const place = placeName(name)
  const words = place.split(/\s+/)
  return [...new Set([shortName(name), place, words.slice(0, 2).join(' '), words[0]].filter(Boolean))]
}
// the most telling pair of name forms that fits `px` (the most text kept); when none fits, the shortest pair
function fitNames(a, b, px, font) {
  const A = nameForms(a).map((t) => [t, textWidth(t, font)])
  const B = nameForms(b).map((t) => [t, textWidth(t, font)])
  const sep = textWidth(' × ', font) + 8
  let best = null
  for (const [x, wx] of A) {
    for (const [y, wy] of B) {
      const fits = wx + sep + wy <= px
      // the most text, but balanced: both lines named beats one in full and the other cut to a word
      const score = fits ? x.length + y.length - Math.abs(x.length - y.length) / 2 : -(wx + wy)
      if (!best || (fits && !best.fits) || (fits === best.fits && score > best.score)) best = { names: [x, y], fits, score }
    }
  }
  return best ? best.names : [shortName(a), shortName(b)]
}

// the engine's calendar at the settings on screen, re-asked when they settle (a slider drag sends one request)
function useCalendarData(client, params) {
  const key = params ? JSON.stringify(params) : null
  const [st, setSt] = useState({ status: 'loading', data: null })
  const [tries, setTries] = useState(0)
  const had = useRef(false)
  useEffect(() => {
    if (!client || !key) return undefined
    let live = true
    const t = setTimeout(
      () => {
        setSt((cur) => ({ ...cur, status: cur.data ? 'refreshing' : 'loading' }))
        client.calendar(JSON.parse(key)).then(
          (data) => {
            if (!live) return
            had.current = true
            setSt({ status: 'ready', data })
          },
          (error) => live && setSt((cur) => ({ status: 'error', error, data: cur.data })),
        )
      },
      had.current ? 220 : 0,
    )
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [client, key, tries])
  const retry = useCallback(() => setTries((n) => n + 1), [])
  return { ...st, retry }
}

// the element's width, measured before paint (so the first frame already picks the Gantt or the list); `el` from a
// callback ref, so a re-mounted element is measured too
function useWidth(el) {
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    if (!el) return undefined
    const measure = () => el.clientWidth && setW(el.clientWidth)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  return w
}

// The rows, top to bottom: side a's utilities (DESC), the band of shared windows, side b's (Georgia's sponsors).
function buildLayout(data, scope, order) {
  const dated = data.projects.filter((p) => p.start)
  const shown = scope === 'all' ? dated : dated.filter((p) => p.pairs.length)
  const byDate = (a, b) => a.start.localeCompare(b.start) || a.end.localeCompare(b.end) || a.id.localeCompare(b.id)
  const byRank = (a, b) => (a.best_rank ?? 1e9) - (b.best_rank ?? 1e9) || byDate(a, b)
  const cmp = order === 'rank' ? byRank : byDate
  const sides = { a: [], b: [] }
  for (const u of UTILITIES) {
    const rows = shown.filter((p) => p.utility === u.id).sort(cmp)
    if (rows.length) sides[rows[0].side].push({ utility: u.id, rows })
  }
  const shared = [...data.shared].sort(order === 'rank' ? (a, b) => a.rank - b.rank : (a, b) => a.start.localeCompare(b.start) || a.rank - b.rank)
  // same-station pairs whose build windows don't overlap lead the band (they lead the ranking too): named, with the gap
  const stations = [...(data.stations || [])].sort((a, b) => a.rank - b.rank)
  // the shared windows that can still be acted on (open now or still ahead), soonest first, for the band's heading
  const live = shared.filter((w) => w.ahead !== 'past').sort((a, b) => a.start.localeCompare(b.start))
  const items = []
  const pos = {}
  let y = 0
  let i = 0
  const group = (grp) => {
    items.push({ kind: 'group', key: `g-${grp.utility}`, y, h: GROUP_H, utility: grp.utility, count: grp.rows.length })
    y += GROUP_H
    for (const p of grp.rows) {
      items.push({ kind: 'project', key: p.id, y, h: ROW_H, p, i: i++ })
      pos[p.id] = y
      y += ROW_H
    }
  }
  sides.a.forEach(group)
  y += GAP_H
  items.push({ kind: 'band', key: 'band', y, h: BAND_H, count: shared.length, live, stations: stations.length })
  y += BAND_H
  stations.forEach((s, k) => {
    items.push({ kind: 'station', key: `s-${s.id}`, y, h: STATION_H, s, i: k })
    pos[s.id] = y
    y += STATION_H
  })
  shared.forEach((w, k) => {
    items.push({ kind: 'shared', key: `w-${w.id}`, y, h: SHARED_H, w, i: k + stations.length })
    pos[w.id] = y
    y += SHARED_H
  })
  if (!shared.length) {
    items.push({ kind: 'none', key: 'none', y, h: SHARED_H })
    y += SHARED_H
  }
  y += GAP_H
  sides.b.forEach(group)
  return { items, height: y + 8, pos, shown, shared, stations, live }
}

export default function CalendarView() {
  const g = useGridlock()
  const cal = useCalendarData(g.client, g.engineParams)
  const [root, rootRef] = useState(null)
  const width = useWidth(root)
  const [scope, setScope] = useState('pairs')
  const [order, setOrder] = useState('date')
  const [zoom, setZoom] = useState(null) // {from, to} years; null = the whole span
  const data = cal.data
  const full = data?.range
  const from = Math.max(full?.from ?? 0, zoom?.from ?? full?.from ?? 0)
  const to = Math.max(from, Math.min(full?.to ?? 0, zoom?.to ?? full?.to ?? 0))
  const layout = useMemo(() => (data ? buildLayout(data, scope, order) : null), [data, scope, order])
  // the calendar opens with its head at the top of the rail (once it has rows, so there is room to scroll)
  const ready = !!data
  const scrolled = useRef(false)
  useEffect(() => {
    if (!ready || !root || scrolled.current) return
    scrolled.current = true
    const smooth = !reducedMotion()
    const t = setTimeout(() => root.closest('.gl-ranked')?.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' }), 60)
    return () => clearTimeout(t)
  }, [ready, root])

  if (!g.engineParams) {
    return (
      <div className="gl-cal" ref={rootRef}>
        <EmptyState title="Nothing to compare">Switch on DESC and at least one Georgia utility under Filters.</EmptyState>
      </div>
    )
  }
  return (
    <div className={`gl-cal${cal.status === 'refreshing' ? ' gl-cal--stale' : ''}`} ref={rootRef} aria-busy={cal.status !== 'ready' || undefined}>
      {!data && cal.status === 'loading' && <Loading label="Laying out the build windows…" />}
      {cal.status === 'error' && <ErrorBanner error={cal.error} onRetry={cal.retry} />}
      {data && !full && <EmptyState title="No dates to show">None of the projects compared here has an in-service date in its filing.</EmptyState>}
      {data && layout && full && (
        <>
          <Toolbar
            data={data}
            scope={scope}
            setScope={setScope}
            order={order}
            setOrder={setOrder}
            from={from}
            to={to}
            full={full}
            setZoom={setZoom}
            params={g.engineParams}
          />
          <Legend today={width < GANTT_MIN} />
          {width >= GANTT_MIN ? (
            <Gantt data={data} layout={layout} from={from} to={to} width={width} />
          ) : (
            <YearList data={data} layout={layout} from={from} to={to} order={order} />
          )}
          <p className="gl-cal__note">
            {data.note} {data.window_rule}.
          </p>
        </>
      )}
    </div>
  )
}

function Toolbar({ data, scope, setScope, order, setOrder, from, to, full, setZoom, params }) {
  const years = []
  for (let y = full.from; y <= full.to; y++) years.push(y)
  const thisYear = year(data.today)
  const zoomed = from !== full.from || to !== full.to
  const fromId = useId()
  const toId = useId()
  // the calendar file: by default only what hasn't ended (most shared windows here passed, as filed); all on request
  const inScope = data.projects.filter((p) => p.start && (scope === 'all' || p.pairs.length))
  const nAll = data.shared.length + inScope.length
  const nUp = data.shared.filter((w) => w.ahead !== 'past').length + inScope.filter((p) => p.now !== 'past').length
  return (
    <div className="gl-cal__tools">
      <div className="gl-sort" role="group" aria-label="Which projects">
        {[
          ['pairs', 'In flagged pairs', data.counts.in_pairs],
          ['all', 'All compared', data.counts.dated],
        ].map(([id, label, n]) => (
          <button key={id} type="button" className={scope === id ? 'is-on' : ''} aria-pressed={scope === id} onClick={() => setScope(id)}>
            {label} <span className="gl-cal__n">{fmtInt(n)}</span>
          </button>
        ))}
      </div>
      <div className="gl-sort" role="group" aria-label="Order the rows">
        {[
          ['date', 'By date'],
          ['rank', 'By rank'],
        ].map(([id, label]) => (
          <button key={id} type="button" className={order === id ? 'is-on' : ''} aria-pressed={order === id} onClick={() => setOrder(id)}>
            {label}
          </button>
        ))}
      </div>
      <div className="gl-cal__years" role="group" aria-label="Years shown">
        <label htmlFor={fromId}>Years</label>
        <select id={fromId} value={from} onChange={(e) => setZoom({ from: Number(e.target.value), to: Math.max(Number(e.target.value), to) })} aria-label="From year">
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
        <span aria-hidden="true">–</span>
        <select id={toId} value={to} onChange={(e) => setZoom({ from: Math.min(from, Number(e.target.value)), to: Number(e.target.value) })} aria-label="To year">
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
        {zoomed ? (
          <button type="button" className="gl-link" onClick={() => setZoom(null)}>
            All years
          </button>
        ) : (
          thisYear > full.from &&
          thisYear <= full.to && (
            <button type="button" className="gl-link" onClick={() => setZoom({ from: thisYear - 1, to: full.to })}>
              From {thisYear - 1}
            </button>
          )
        )}
      </div>
      <div className="gl-cal__dl">
        {nUp > 0 && (
          <a
            className="gl-tool"
            href={downloadUrl('calendar.ics', params, { scope, upcoming: 'true' })}
            download
            title="The shared windows and build windows here that haven't ended (as filed), as a calendar file (.ics) for Outlook, Google or Apple Calendar"
          >
            <CalIcon />
            Add upcoming to calendar <span className="gl-cal__n">{fmtInt(nUp)}</span>
          </a>
        )}
        <a
          className="gl-link gl-cal__all"
          href={downloadUrl('calendar.ics', params, { scope })}
          download
          title="Every shared window and build window shown here, past ones included, as a calendar file (.ics)"
        >
          {nUp > 0 ? `or all ${fmtInt(nAll)}` : `Add all ${fmtInt(nAll)} to calendar`}
        </a>
        <a className="gl-tool" href={downloadUrl('export.xlsx', params)} download title="The Excel workbook in Sperry's table format, with a calendar sheet">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10" />
          </svg>
          Excel
        </a>
      </div>
    </div>
  )
}

function CalIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2.5 4.5h11v9h-11zM2.5 7.5h11M5.5 2.5v3M10.5 2.5v3" />
    </svg>
  )
}

// `today`: the list by year marks today with a tick (the Gantt's axis labels its Today line itself)
function Legend({ today }) {
  return (
    <ul className="gl-cal__legend" aria-label="Key">
      <li>
        <span className="gl-cal__key gl-cal__key--filed" aria-hidden="true" />
        Build window, as filed
      </li>
      <li>
        <span className="gl-cal__key gl-cal__key--derived" aria-hidden="true" />
        Start derived
      </li>
      <li>
        <span className="gl-cal__key gl-cal__key--ms" aria-hidden="true" />
        In service
      </li>
      <li>
        <span className="gl-cal__key gl-cal__key--win" aria-hidden="true" />
        Shared window
      </li>
      <li>
        <span className="gl-cal__key gl-cal__key--win gl-cal__key--winder" aria-hidden="true" />
        Shared, a start derived
      </li>
      <li>
        <span className="gl-cal__key gl-cal__key--gap" aria-hidden="true" />
        Same station, no shared window
      </li>
      {today && (
        <li>
          <span className="gl-cal__key gl-cal__key--today" aria-hidden="true" />
          Today
        </li>
      )}
    </ul>
  )
}

// ------------------------------------------------------------------------------------------------ the Gantt (wide)

function Gantt({ data, layout, from, to, width }) {
  const g = useGridlock()
  const lab = labelWidth(width)
  const trackW = Math.max(1, width - lab - PAD_R)
  const t0 = Date.UTC(from, 0, 1)
  const t1 = Date.UTC(to + 1, 0, 1)
  const fx = useCallback((iso, end = false) => (ts(iso) + (end ? DAY : 0) - t0) / (t1 - t0), [t0, t1])
  const pById = useMemo(() => Object.fromEntries(data.projects.map((p) => [p.id, p])), [data.projects])
  const wById = useMemo(() => Object.fromEntries(data.shared.map((w) => [w.id, w])), [data.shared])
  const sById = useMemo(() => Object.fromEntries((data.stations || []).map((s) => [s.id, s])), [data.stations])
  const oById = useMemo(() => Object.fromEntries(g.overlaps.map((o) => [o.id, o])), [g.overlaps])
  // the shared rows' label font, for fitting their two names (read once from the page)
  const [font] = useState(() => `12px ${typeof document !== 'undefined' ? getComputedStyle(document.body).fontFamily : 'system-ui'}`)
  const [hoverable] = useState(canHover)
  // room for a shared row's two names: the label less its padding, rank, separator and (on touch) the calendar link
  const namesPx = lab - 66 - (hoverable ? 0 : 28)
  const [hot, setHot] = useState(null) // what the pointer (or keyboard focus) is on here: {kind: 'project' | 'shared', id}
  const [picked, setPicked] = useState(null) // the project whose card is open
  const pickedFrom = useRef(null)
  const bodyRef = useRef(null)
  // the first view grows the bars in; later changes (sort, zoom, settings) glide instead
  const [intro] = useState(() => !reducedMotion())

  // what is lit: what the pointer is on here, else on the map or the list (the shared hover), else the open card or pair
  const ext = g.hover ? { kind: g.hover.kind === 'overlap' ? 'shared' : 'project', id: g.hover.id } : null
  const focus = hot || ext || (picked ? { kind: 'project', id: picked } : null) || (g.draft ? { kind: 'shared', id: g.draft.id } : null)
  const fKind = focus?.kind
  const fId = focus?.id
  const lit = useMemo(() => {
    if (!fId) return null
    const projects = new Set()
    const rel = new Set()
    const wins = new Set()
    if (fKind === 'shared') {
      for (const id of fId.split('~')) projects.add(id)
      wins.add(fId)
    } else {
      projects.add(fId)
      for (const pid of pById[fId]?.pairs || []) {
        if (wById[pid]) wins.add(pid)
        for (const id of pid.split('~')) if (id !== fId) rel.add(id)
      }
    }
    return { projects, rel, wins, pair: fKind === 'shared' ? fId : null }
  }, [fKind, fId, pById, wById])

  const mapHover = (kind, id) => g.setHover(id ? { kind: kind === 'shared' ? 'overlap' : 'project', id } : null)
  // the tip goes under its row when the row sits just below the sticky year axis (above it, the axis would cover it)
  const axisRef = useRef(null)
  const tipBelow = (el) => {
    const top = el?.getBoundingClientRect().top
    const axis = axisRef.current?.getBoundingClientRect().bottom
    return top != null && axis != null && top - axis < 96
  }
  const enter = (kind, id) => (e) => {
    if (e.pointerType === 'touch') return
    setHot({ kind, id, below: tipBelow(e.currentTarget) })
    mapHover(kind, id)
  }
  const leave = () => {
    setHot(null)
    mapHover('project', picked)
  }
  const focusIn = (kind, id) => (e) => {
    if (!e.currentTarget.matches(':focus-visible')) return
    setHot({ kind, id, below: tipBelow(e.currentTarget) })
    mapHover(kind, id)
  }

  const openCard = (id, el) => {
    pickedFrom.current = el
    setPicked((cur) => (cur === id ? null : id))
    const b = boundsOf(projectPoints(g.byId[id]))
    if (b) g.mapApi.current?.ensureVisible(b, { card: false })
    mapHover('project', id)
  }
  const closeCard = useCallback(() => {
    setPicked(null)
    g.setHover(null)
    pickedFrom.current?.focus({ preventScroll: true })
  }, [g])
  const openPair = (id) => {
    const o = oById[id]
    if (o) g.openDraft(o)
  }
  useEffect(() => {
    if (!picked) return undefined
    const onKey = (e) => e.key === 'Escape' && closeCard()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [picked, closeCard])

  // year lines and labels: every year when a year is at least 44 px wide, else every other; quarters when roomy
  const pxPerYear = trackW / (to - from + 1)
  const every = pxPerYear >= 44 ? 1 : pxPerYear >= 22 ? 2 : 4
  const ticks = []
  for (let y = from; y <= to; y++) ticks.push({ y, x: (Date.UTC(y, 0, 1) - t0) / (t1 - t0), label: (y - from) % every === 0 })
  const quarters = pxPerYear >= 150
  const today = fx(data.today)
  const showToday = today >= 0 && today <= 1

  // the connector: the lit pair's shared months, drawn from one project's bar to the other's through its own row
  const conn = (() => {
    const w = lit?.pair && wById[lit.pair]
    if (!w || layout.pos[w.a] == null || layout.pos[w.b] == null) return null
    const top = Math.min(layout.pos[w.a], layout.pos[w.b], layout.pos[w.id] ?? Infinity)
    const bottom = Math.max(layout.pos[w.a], layout.pos[w.b]) + ROW_H
    const x0 = Math.max(0, fx(w.start))
    const x1 = Math.min(1, fx(w.end, true))
    if (x1 <= x0) return null
    return { top: top + 4, height: bottom - top - 8, left: x0, width: x1 - x0, id: w.id }
  })()
  // a same-station pair's gap, lit: a drop from the first window's end to the pair's row, and from there to the
  // second window's start (the dashed gap in the row is the time between them)
  const drops = (() => {
    const s = lit?.pair && sById[lit.pair]
    if (!s?.gap_from || layout.pos[s.id] == null || layout.pos[s.a] == null || layout.pos[s.b] == null) return null
    const second = s.first === s.a ? s.b : s.a
    const mid = layout.pos[s.id] + STATION_H / 2
    return [
      [fx(s.gap_from, true), layout.pos[s.first] + ROW_H / 2],
      [fx(s.gap_to), layout.pos[second] + ROW_H / 2],
    ]
      .filter(([x]) => x >= 0 && x <= 1)
      .map(([x, y]) => ({ key: `${s.id}-${x}`, left: x, top: Math.min(y, mid) + 6, height: Math.abs(y - mid) - 12 }))
      .filter((d) => d.height > 0)
  })()

  const pct = (v) => `${(v * 100).toFixed(3)}%`
  const clamp01 = (v) => Math.max(0, Math.min(1, v))
  const card = picked ? layout.items.find((it) => it.kind === 'project' && it.p.id === picked) : null
  const tipItem = hot
    ? layout.items.find((it) =>
        hot.kind === 'shared' ? (it.kind === 'shared' && it.w.id === hot.id) || (it.kind === 'station' && it.s.id === hot.id) : it.kind === 'project' && it.p.id === hot.id,
      )
    : null

  return (
    <div
      className={`gl-cal__gantt${intro ? ' gl-cal__gantt--intro' : ''}`}
      style={{ '--lab': `${lab}px`, '--pr': `${PAD_R}px` }}
      role="group"
      aria-label={`Coordination calendar, ${from} to ${to}`}
    >
      <div className="gl-cal__axis" aria-hidden="true" ref={axisRef}>
        <div className="gl-cal__axislab">{from === to ? from : `${from}–${to}`}</div>
        <div className="gl-cal__axistrack">
          {ticks.map((t) =>
            t.label ? (
              <span key={t.y} className="gl-cal__year" style={{ left: pct(t.x) }}>
                {t.y}
              </span>
            ) : null,
          )}
          {showToday && (
            <span className="gl-cal__todaytag" style={{ left: pct(today) }}>
              Today
            </span>
          )}
        </div>
      </div>
      <div className="gl-cal__body" ref={bodyRef} style={{ height: layout.height }}>
        <div className="gl-cal__under" aria-hidden="true">
          {ticks.map((t) => (
            <span key={t.y} className={`gl-cal__vline${t.label ? '' : ' gl-cal__vline--minor'}`} style={{ left: pct(t.x) }} />
          ))}
          {quarters &&
            ticks.flatMap((t) =>
              [3, 6, 9].map((m) => (
                <span key={`${t.y}-${m}`} className="gl-cal__vline gl-cal__vline--q" style={{ left: pct((Date.UTC(t.y, m, 1) - t0) / (t1 - t0)) }} />
              )),
            )}
          {conn && (
            <span
              key={conn.id}
              className="gl-cal__conn"
              style={{ top: conn.top, height: conn.height, left: pct(conn.left), width: pct(conn.width) }}
            />
          )}
          {drops?.map((d) => (
            <span key={d.key} className="gl-cal__drop" style={{ top: d.top, height: d.height, left: pct(d.left) }} />
          ))}
        </div>

        {layout.items.map((it) => {
          if (it.kind === 'group') {
            return (
              <div key={it.key} className="gl-cal__group" style={{ top: it.y, height: it.h }}>
                <h3 className="gl-cal__grouph">
                  <span className={`gl-swatch gl-swatch--${toneOf(it.utility)}`} aria-hidden="true" />
                  {UTILITIES.find((u) => u.id === it.utility)?.name || it.utility}
                  <span className="gl-cal__groupn">
                    {fmtInt(it.count)} project{it.count === 1 ? '' : 's'}
                  </span>
                </h3>
              </div>
            )
          }
          if (it.kind === 'band') {
            const nOpen = it.live.filter((w) => w.ahead === 'open').length
            const nAhead = it.live.length - nOpen
            const status = [nOpen && `${fmtInt(nOpen)} open now`, nAhead && `${fmtInt(nAhead)} still ahead`].filter(Boolean).join(', ') || 'all passed, as filed'
            return (
              <div key={it.key} className="gl-cal__band" style={{ top: it.y, height: it.h }}>
                <div className="gl-cal__bandtop">
                  <h3 className="gl-cal__bandh">
                    Shared build windows{' '}
                    <span className="gl-cal__groupn">
                      {fmtInt(it.count)} · {status}
                    </span>
                  </h3>
                  {it.live.slice(0, 3).map((w) => (
                    <button
                      key={w.id}
                      type="button"
                      className={`gl-cal__live gl-cal__live--${w.ahead}`}
                      onClick={() => openPair(w.id)}
                      onPointerEnter={enter('shared', w.id)}
                      onPointerLeave={leave}
                      title={`Open pair ${w.rank}: its shared build window, ${span(w.start, w.end)}`}
                    >
                      #{w.rank} · {span(w.start, w.end)}
                      {w.ahead === 'open' ? ' · open now' : ''}
                    </button>
                  ))}
                </div>
                <p className="gl-cal__bandp">The months both projects of a pair are building. Solid: both dates as filed. Hatched: a start derived.</p>
              </div>
            )
          }
          if (it.kind === 'station') {
            const s = it.s
            const on = lit?.wins.has(s.id)
            const has = !!(s.gap_from && s.gap_to)
            const x0 = has ? fx(s.gap_from, true) : 0
            const x1 = has ? fx(s.gap_to) : 0
            const c0 = clamp01(x0)
            const c1 = clamp01(x1)
            const wide = (c1 - c0) * trackW
            const gap = gapWords(s.gap_days)
            // the gap's words, longest first, each tried inside the dashed gap, then after it, then before it
            const texts = [`no shared window · ${gap}`, gap]
            const tw = (t) => textWidth(t, `10px ${font.slice(5)}`) * 1.05 + 14
            let words = null
            let beside = null
            if (has) {
              const afterRoom = (1 - c1) * trackW - 9
              const beforeRoom = c0 * trackW - 6
              for (const t of texts) {
                if (wide >= tw(t) + 8) words = t
                else if (afterRoom >= tw(t)) beside = { t, at: 'after' }
                else if (beforeRoom >= tw(t)) beside = { t, at: 'before' }
                if (words || beside) break
              }
            }
            const o = oById[s.id]
            const st = o ? stationSentence(o) : null
            return (
              <div
                key={it.key}
                className={`gl-cal__row gl-cal__row--shared gl-cal__row--station${on ? ' is-lit' : ''}${lit && !on ? ' is-quiet' : ''}${g.draft?.id === s.id ? ' is-open' : ''}`}
                style={{ top: it.y, height: it.h, '--i': it.i }}
              >
                <div className="gl-cal__lab">
                  <span className="gl-cal__rank">#{s.rank}</span>
                  <span className="gl-cal__stack">
                    <span className="gl-cal__name" title={st ? `${st.lead}${st.rest}` : s.station}>
                      {s.station}
                    </span>
                    <span className="gl-cal__sub" title={s.sperry ? "Sperry's own number for this pair in their worked example" : undefined}>
                      same station{s.sperry ? ` · Sperry's ${s.sperry}` : ''}
                    </span>
                  </span>
                  <a
                    className="gl-cal__ics"
                    href={downloadUrl('calendar.ics', g.engineParams, { pair: s.id })}
                    download
                    aria-label={`Add pair ${s.rank}'s two build windows at ${s.station} to your calendar (.ics)`}
                    title="Add both build windows to your calendar (.ics): the station, both projects and their sources"
                  >
                    <CalIcon />
                  </a>
                </div>
                <div className="gl-cal__track">
                  {has && c1 > c0 ? (
                    <button
                      type="button"
                      className={`gl-cal__gap${x0 < 0 ? ' is-cut-l' : ''}${x1 > 1 ? ' is-cut-r' : ''}`}
                      style={{ left: pct(c0), width: pct(c1 - c0) }}
                      data-pair={s.id}
                      onClick={() => openPair(s.id)}
                      onPointerEnter={enter('shared', s.id)}
                      onPointerLeave={leave}
                      onFocus={focusIn('shared', s.id)}
                      onBlur={leave}
                      aria-label={`Pair ${s.rank}, same station at ${s.station}: no shared build window, the two are ${gap} (${monthYear(s.gap_from)} to ${monthYear(s.gap_to)}). Opens the pair.`}
                    >
                      {words && <span className="gl-cal__gaplab">{words}</span>}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="gl-cal__gapnone"
                      data-pair={s.id}
                      onClick={() => openPair(s.id)}
                      onPointerEnter={enter('shared', s.id)}
                      onPointerLeave={leave}
                      onFocus={focusIn('shared', s.id)}
                      onBlur={leave}
                    >
                      {has ? gap : 'timing unknown'} · open the pair
                    </button>
                  )}
                  {has && c1 > c0 && beside && (
                    <span
                      className={`gl-cal__dtag gl-cal__dtag--${beside.at}`}
                      style={beside.at === 'after' ? { left: pct(c1) } : { right: pct(1 - c0) }}
                      aria-hidden="true"
                    >
                      {beside.t}
                    </span>
                  )}
                </div>
              </div>
            )
          }
          if (it.kind === 'none') {
            return (
              <p key={it.key} className="gl-cal__none" style={{ top: it.y, height: it.h }}>
                No flagged pair&apos;s build windows overlap at these settings.
              </p>
            )
          }
          if (it.kind === 'shared') {
            const w = it.w
            const x0 = fx(w.start)
            const x1 = fx(w.end, true)
            const on = lit?.wins.has(w.id)
            const out = x1 <= 0 || x0 >= 1
            const a = pById[w.a]
            const b = pById[w.b]
            const wide = (clamp01(x1) - clamp01(x0)) * trackW
            const derived = derivedOf(w)
            // two filed names that read alike (DESC-6809E / -6809G) keep their project number, as in the list
            const dupA = g.nearDup?.has(w.a)
            const dupB = g.nearDup?.has(w.b)
            const extra = (dupA ? textWidth(idTail(w.a), font) + 6 : 0) + (dupB ? textWidth(idTail(w.b), font) + 6 : 0)
            const [na, nb] = w.station ? [null, null] : fitNames(a?.name, b?.name, namesPx - extra, font)
            const full = `${utilityShort(w.a_utility)}: ${displayName(a?.name)} (${w.a}) × ${utilityShort(w.b_utility)}: ${displayName(b?.name)} (${w.b})`
            return (
              <div
                key={it.key}
                className={`gl-cal__row gl-cal__row--shared${on ? ' is-lit' : ''}${lit && !on ? ' is-quiet' : ''}${g.draft?.id === w.id ? ' is-open' : ''}`}
                style={{ top: it.y, height: it.h, '--i': it.i }}
              >
                <div className="gl-cal__lab">
                  <span className="gl-cal__rank">#{w.rank}</span>
                  {w.station ? (
                    <span className="gl-cal__name" title={full}>
                      {w.station}
                    </span>
                  ) : (
                    <span className="gl-cal__two" title={full}>
                      <span className="gl-cal__name">{na}</span>
                      {dupA && <span className="gl-cal__id">{idTail(w.a)}</span>}
                      <span className="gl-cal__x" aria-hidden="true">
                        ×
                      </span>
                      <span className="gl-cal__name">{nb}</span>
                      {dupB && <span className="gl-cal__id">{idTail(w.b)}</span>}
                    </span>
                  )}
                  {w.station && <span className="gl-cal__tag">same station</span>}
                  <a
                    className="gl-cal__ics"
                    href={downloadUrl('calendar.ics', g.engineParams, { pair: w.id })}
                    download
                    aria-label={`Add pair ${w.rank}'s shared build window to your calendar (.ics)`}
                    title={`Add this shared window to your calendar (.ics): both projects and their sources; ${derived.length ? `dates as filed, ${derivedWords(derived)} (said so in the event)` : 'dates as filed'}`}
                  >
                    <CalIcon />
                  </a>
                </div>
                <div className="gl-cal__track">
                  {out ? (
                    <span className={`gl-cal__edge gl-cal__edge--${x1 <= 0 ? 'l' : 'r'}`} aria-hidden="true">
                      {x1 <= 0 ? '‹' : '›'} {year(w.start)}
                    </span>
                  ) : (
                    <button
                      type="button"
                      className={`gl-cal__win gl-cal__win--${w.ahead}${derived.length ? ' gl-cal__win--derived' : ''}${x0 < 0 ? ' is-cut-l' : ''}${x1 > 1 ? ' is-cut-r' : ''}`}
                      style={{ left: pct(clamp01(x0)), width: pct(clamp01(x1) - clamp01(x0)) }}
                      data-pair={w.id}
                      onClick={() => openPair(w.id)}
                      onPointerEnter={enter('shared', w.id)}
                      onPointerLeave={leave}
                      onFocus={focusIn('shared', w.id)}
                      onBlur={leave}
                      aria-label={`Pair ${w.rank}${w.station ? ` at ${w.station}` : ''}: shared build window ${span(w.start, w.end)}, ${months(w.months)}, ${derived.length ? `${derivedWords(derived)}, ${w.ahead === 'past' ? 'passed' : NOW_WORD[w.ahead]}` : `dates as filed, ${NOW_WORD[w.ahead]}`}. Opens the pair.`}
                    >
                      {wide >= 44 && <span className="gl-cal__winlab">{Math.round(w.months)} mo</span>}
                    </button>
                  )}
                </div>
              </div>
            )
          }
          // a project
          const p = it.p
          const x0 = fx(p.start)
          const x1 = fx(p.end, true)
          const out = x1 <= 0 || x0 >= 1
          const on = lit?.projects.has(p.id)
          const rel = lit?.rel.has(p.id)
          const dim = !p.pairs.length
          const derived = p.start_as === 'derived'
          const tone = toneOf(p.utility)
          // "start derived" is said beside the bar (after it, else before it), never across the Today line: past it
          // when the bar ends just before today, else over it on a plate; in the label only when neither side has room
          const TAG = 84
          const room = TAG + 8
          const bx0 = clamp01(x0) * trackW
          const bx1 = clamp01(x1) * trackW
          const tx = showToday ? today * trackW : null
          const hits = (l, r) => tx != null && tx >= l - 3 && tx <= r + 3
          let tagAt = null
          let tagX = null
          if (derived && !out) {
            const afterOk = trackW - bx1 >= room
            const beforeOk = bx0 >= room
            if (afterOk && !hits(bx1 + 9, bx1 + 9 + TAG)) tagAt = 'after'
            else if (beforeOk && !hits(bx0 - 6 - TAG, bx0 - 6)) tagAt = 'before'
            else if (tx != null && tx > bx1 && trackW - tx >= TAG + 12) {
              tagAt = 'after'
              tagX = today
            } else tagAt = afterOk ? 'after' : beforeOk ? 'before' : 'label'
          }
          const tagPlate = tagAt && tagAt !== 'label' && tagX == null && (tagAt === 'after' ? hits(bx1 + 9, bx1 + 9 + TAG) : hits(bx0 - 6 - TAG, bx0 - 6))
          const dup = g.nearDup?.has(p.id)
          return (
            <div
              key={it.key}
              className={`gl-cal__row gl-cal__row--${tone}${on ? ' is-lit' : ''}${rel ? ' is-rel' : ''}${lit && !on && !rel ? ' is-quiet' : ''}${dim ? ' is-dim' : ''}${picked === p.id ? ' is-picked' : ''}`}
              style={{ top: it.y, height: it.h, '--i': it.i }}
            >
              <div className="gl-cal__lab">
                <span className="gl-cal__name" title={`${displayName(p.name)} (${p.id})`}>
                  {displayName(p.name)}
                </span>
                {dup && <span className="gl-cal__id">{idTail(p.id)}</span>}
                {tagAt === 'label' && <span className="gl-cal__tag">start derived</span>}
              </div>
              <div className="gl-cal__track">
                {out ? (
                  <span className={`gl-cal__edge gl-cal__edge--${x1 <= 0 ? 'l' : 'r'}`} aria-hidden="true">
                    {x1 <= 0 ? `‹ ${year(p.end)}` : `${year(p.start)} ›`}
                  </span>
                ) : (
                  <>
                    <button
                      type="button"
                      className={`gl-cal__bar${derived ? ' gl-cal__bar--derived' : ''}${x0 < 0 ? ' is-cut-l' : ''}${x1 > 1 ? ' is-cut-r' : ''}`}
                      style={{ left: pct(clamp01(x0)), width: pct(clamp01(x1) - clamp01(x0)) }}
                      aria-expanded={picked === p.id}
                      aria-label={`${utilityShort(p.utility)}: ${displayName(p.name)}. Build window ${span(p.start, p.end)}${derived ? ', start derived' : ', as filed'}; in service ${fmtDate(p.in_service)}. In ${p.pairs.length} flagged pair${p.pairs.length === 1 ? '' : 's'}.`}
                      onClick={(e) => openCard(p.id, e.currentTarget)}
                      onPointerEnter={enter('project', p.id)}
                      onPointerLeave={leave}
                      onFocus={focusIn('project', p.id)}
                      onBlur={leave}
                    >
                      <span className="gl-cal__fill" />
                    </button>
                    {x1 <= 1 && <span className="gl-cal__ms" style={{ left: pct(x1) }} aria-hidden="true" />}
                    {p.pairs.map((pid) => {
                      const w = wById[pid]
                      if (!w) return null
                      const s0 = clamp01(fx(w.start))
                      const s1 = clamp01(fx(w.end, true))
                      return s1 > s0 ? (
                        <span
                          key={pid}
                          className={`gl-cal__sm${lit?.wins.has(pid) && lit.pair === pid ? ' is-on' : ''}`}
                          style={{ left: pct(s0), width: pct(s1 - s0) }}
                          aria-hidden="true"
                        />
                      ) : null
                    })}
                    {tagAt === 'after' && (
                      <span
                        className={`gl-cal__dtag gl-cal__dtag--after${tagX != null ? ' gl-cal__dtag--today' : ''}${tagPlate ? ' gl-cal__dtag--plate' : ''}`}
                        style={{ left: pct(tagX ?? clamp01(x1)) }}
                        aria-hidden="true"
                      >
                        start derived
                      </span>
                    )}
                    {tagAt === 'before' && (
                      <span className={`gl-cal__dtag gl-cal__dtag--before${tagPlate ? ' gl-cal__dtag--plate' : ''}`} style={{ right: pct(1 - clamp01(x0)) }} aria-hidden="true">
                        start derived
                      </span>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        })}

        <div className="gl-cal__over" aria-hidden="true">
          {showToday && <span className="gl-cal__today" style={{ left: pct(today) }} />}
        </div>
        {tipItem && !(picked && tipItem.kind === 'project' && tipItem.p.id === picked) && (
          <Tip item={tipItem} fx={fx} lab={lab} trackW={trackW} below={hot.below} overlap={tipItem.kind === 'station' ? oById[tipItem.s.id] : null} />
        )}
        {card && (
          <BarCard
            key={card.p.id}
            item={card}
            total={layout.height}
            left={Math.min(width - 360 - 8, Math.max(8, lab + clamp01(fx(card.p.start)) * trackW))}
            pById={pById}
            oById={oById}
            onClose={closeCard}
            onPair={openPair}
          />
        )}
      </div>
    </div>
  )
}

function Tip({ item, fx, lab, trackW, overlap, below: underAxis }) {
  const shared = item.kind === 'shared'
  const station = item.kind === 'station'
  const s = shared ? item.w : station ? item.s : item.p
  const [t0, t1] = station ? (s.gap_from ? [fx(s.gap_from, true), fx(s.gap_to)] : [0, 1]) : [fx(s.start), fx(s.end, true)]
  const mid = Math.max(0, Math.min(1, (Math.max(0, t0) + Math.min(1, t1)) / 2))
  const x = Math.max(lab + 20, Math.min(lab + trackW - 20, lab + mid * trackW))
  // above the row, or under it near the top or just under the sticky axis (which would cover it); inside the track
  const below = item.y < 70 || !!underAxis
  const derivedFor = shared ? derivedOf(s) : []
  const st = station && overlap ? stationSentence(overlap) : null
  return (
    <div
      className={`gl-cal__tip${below ? ' gl-cal__tip--below' : ''}${station ? ' gl-cal__tip--wrap' : ''}`}
      role="presentation"
      style={{ top: below ? item.y + item.h : item.y, left: x, '--shift': `${Math.round(Math.max(0, Math.min(1, (x - lab) / trackW)) * 100)}%` }}
    >
      {station ? (
        <>
          <strong>
            #{s.rank} · same station: {s.station}
            {s.sperry ? ` · Sperry's ${s.sperry}` : ''}
          </strong>
          <span>
            No shared window: the build windows are {gapWords(s.gap_days)}
            {s.gap_from ? ` (${monthYear(s.gap_from)} to ${monthYear(s.gap_to)})` : ''}
          </span>
          {st && <span className="gl-cal__tipsub">{st.rest.replace(/^:\s*/, '').replace(/^./, (c) => c.toUpperCase())}.</span>}
          <span className="gl-cal__tipsub">Click to open the pair.</span>
        </>
      ) : shared ? (
        <>
          <strong>
            #{s.rank} · shared {span(s.start, s.end)}
          </strong>
          <span>
            {months(s.months)}, {derivedFor.length && s.ahead === 'past' ? 'passed' : NOW_WORD[s.ahead]}
            {s.station ? ` · at ${s.station}` : ''}
          </span>
          <span className="gl-cal__tipsub">
            {derivedFor.length ? `${derivedFor.join(' and ')}: start derived. ` : 'Both as filed. '}Click to open the pair.
          </span>
        </>
      ) : (
        <>
          <strong>
            {utilityShort(s.utility)} · {span(s.start, s.end)}
          </strong>
          <span>
            {s.start_as === 'derived' ? 'Start derived' : 'As filed'}; in service {fmtDate(s.in_service)}
          </span>
          <span className="gl-cal__tipsub">
            {s.pairs.length ? `In ${s.pairs.length} flagged pair${s.pairs.length === 1 ? '' : 's'}, ${s.shared} sharing build months` : 'In no flagged pair'}
          </span>
        </>
      )}
    </div>
  )
}

// A project's card: its build window and where it came from, and the flagged pairs it is in (each opens its sheet).
function BarCard({ item, total, left, pById, oById, onClose, onPair }) {
  const g = useGridlock()
  const p = item.p
  const full = g.byId[p.id]
  const headRef = useRef(null)
  const below = item.y + item.h + 6
  const up = below + 260 > total && item.y > 280
  useEffect(() => {
    headRef.current?.focus({ preventScroll: true })
  }, [])
  const pairs = p.pairs.map((id) => oById[id]).filter(Boolean)
  const src = p.source || {}
  const headId = useId()
  return (
    <section
      className={`gl-cal__card${up ? ' gl-cal__card--up' : ''}`}
      style={up ? { bottom: total - item.y + 6, left } : { top: below, left }}
      aria-labelledby={headId}
    >
      <div className="gl-cal__cardtop">
        <p className="gl-cal__eyebrow">
          <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
          {[utilityShort(p.utility), KIND_LABEL[p.kind], fmtKv(p.kv)].filter(Boolean).join(' · ')}
        </p>
        <button type="button" className="gl-close" onClick={onClose} aria-label="Close the card" title="Close">
          ×
        </button>
      </div>
      <h3 id={headId} ref={headRef} tabIndex={-1} className="gl-cal__cardh">
        {displayName(p.name)} <span className="gl-cal__id">{p.id}</span>
      </h3>
      <dl className="gl-cal__dl2">
        <dt>Build window</dt>
        <dd>
          {span(p.start, p.end)}
          <span className={`gl-cal__as${p.start_as === 'derived' ? ' is-derived' : ''}`}>{p.start_as === 'derived' ? 'start derived' : 'as filed'}</span>
          <span className="gl-cal__basis">{p.basis}</span>
        </dd>
        <dt>In service</dt>
        <dd>{fmtDate(p.in_service)}, as filed</dd>
        {p.status && (
          <>
            <dt>Status</dt>
            <dd>{p.status}, as filed</dd>
          </>
        )}
        <dt>Source</dt>
        <dd>
          {src.url ? (
            <a href={src.url} target="_blank" rel="noreferrer">
              {src.id === 'desc' ? 'DESC filing' : src.id === 'ga_irp' ? 'Georgia 2025 IRP, Vol. 3' : 'The filing'}
              {src.page ? `, page ${src.page}` : ''}
            </a>
          ) : (
            src.title || 'The public filing'
          )}
          {src.detail_page ? ` (detail page ${src.detail_page})` : ''}
        </dd>
      </dl>
      <h4 className="gl-cal__cardh4">{pairs.length ? `In ${pairs.length} flagged pair${pairs.length === 1 ? '' : 's'}` : 'In no flagged pair at these settings'}</h4>
      {pairs.length > 0 && (
        <ul className="gl-cal__pairs">
          {pairs.slice(0, MAX_PAIRS_IN_CARD).map((o) => {
            const other = o.a === p.id ? o.b : o.a
            const q = pById[other] || g.byId[other]
            const when = whenOf(o)
            return (
              <li key={o.id}>
                <button type="button" onClick={() => onPair(o.id)}>
                  <span className="gl-cal__rank">#{o.rank}</span>
                  <span className="gl-cal__pairname">
                    <span className={`gl-swatch gl-swatch--${toneOf(q?.utility)}`} aria-hidden="true" />
                    <span className="gl-cal__ell">{displayName(q?.name) || other}</span>
                  </span>
                  <span className={`gl-cal__pairwhen gl-when--${when.tone}`}>{o.shared_station ? `same station · ${when.row}` : when.row}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {pairs.length > MAX_PAIRS_IN_CARD && <p className="gl-fine">And {pairs.length - MAX_PAIRS_IN_CARD} more in the list.</p>}
      <div className="gl-cal__cardfoot">
        {full && (
          <button type="button" className="gl-link" onClick={() => g.openProject(p.id, { fly: true })}>
            Where this came from
          </button>
        )}
      </div>
    </section>
  )
}

// ------------------------------------------------------------------------------------------------ a list by year (narrow)

function YearList({ data, layout, from, to, order }) {
  const g = useGridlock()
  const [open, setOpen] = useState(null)
  const oById = useMemo(() => Object.fromEntries(g.overlaps.map((o) => [o.id, o])), [g.overlaps])
  const pById = useMemo(() => Object.fromEntries(data.projects.map((p) => [p.id, p])), [data.projects])
  const t0 = Date.UTC(from, 0, 1)
  const t1 = Date.UTC(to + 1, 0, 1)
  const fx = (iso, end = false) => Math.max(0, Math.min(1, (ts(iso) + (end ? DAY : 0) - t0) / (t1 - t0)))
  const today = (ts(data.today) - t0) / (t1 - t0)
  const years = useMemo(() => {
    const inRange = (s, e) => year(e) >= from && year(s) <= to
    const by = new Map()
    const add = (y, e) => by.set(y, [...(by.get(y) || []), e])
    for (const w of layout.shared) if (inRange(w.start, w.end)) add(year(w.start), { kind: 'shared', key: `w-${w.id}`, w })
    for (const p of layout.shown) if (inRange(p.start, p.end)) add(year(p.start), { kind: 'project', key: p.id, p })
    const rank = (e) => (e.kind === 'shared' ? e.w.rank : (e.p.best_rank ?? 1e9))
    const start = (e) => (e.kind === 'shared' ? e.w.start : e.p.start)
    for (const list of by.values()) {
      list.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'shared' ? -1 : 1) || (order === 'rank' ? rank(a) - rank(b) : 0) || start(a).localeCompare(start(b)))
    }
    return [...by.entries()].sort((a, b) => a[0] - b[0])
  }, [layout, from, to, order])
  const thisYear = year(data.today)
  const stations = layout.stations || []
  if (!years.length && !stations.length) return <p className="gl-fine gl-cal__pad">Nothing in these years; widen the years above.</p>
  const live = layout.live || []
  return (
    <ol className="gl-cal-yl">
      <li className="gl-cal-yl__sum">
        <p>
          {fmtInt(layout.shared.length)} shared build window{layout.shared.length === 1 ? '' : 's'},{' '}
          {live.length ? `${fmtInt(live.length)} not yet ended:` : 'all passed, as filed.'}
        </p>
        {live.slice(0, 3).map((w) => (
          <button key={w.id} type="button" className={`gl-cal__live gl-cal__live--${w.ahead}`} onClick={() => oById[w.id] && g.openDraft(oById[w.id])}>
            #{w.rank} · {span(w.start, w.end)}
            {w.ahead === 'open' ? ' · open now' : ''}
          </button>
        ))}
      </li>
      {stations.length > 0 && (
        <li className="gl-cal-yl__year">
          <h3 className="gl-cal-yl__h">
            Same station
            <span className="gl-cal-yl__count">no shared window</span>
          </h3>
          <ul className="gl-cal-yl__list">
            {stations.map((s) => (
              <li key={s.id} className="gl-cal-yl__item gl-cal-yl__item--shared gl-cal-yl__item--station">
                <button type="button" className="gl-cal-yl__main" data-pair={s.id} onClick={() => oById[s.id] && g.openDraft(oById[s.id])}>
                  <span className="gl-cal-yl__eyebrow">
                    #{s.rank} · same station{s.sperry ? ` · Sperry's ${s.sperry}` : ''}
                  </span>
                  <span className="gl-cal-yl__name">
                    {s.station}: {displayName(pById[s.a]?.name)} × {displayName(pById[s.b]?.name)}
                  </span>
                  <span className="gl-cal-yl__meta">
                    No shared window · build windows {gapWords(s.gap_days)}
                    {s.gap_from ? ` (${monthYear(s.gap_from)} to ${monthYear(s.gap_to)})` : ''}
                  </span>
                  {s.gap_from && <Mini s={s.gap_from} e={s.gap_to} kind="gap" fx={fx} today={today} />}
                </button>
                <a
                  className="gl-cal__ics gl-cal-yl__ics"
                  href={downloadUrl('calendar.ics', g.engineParams, { pair: s.id })}
                  download
                  aria-label={`Add pair ${s.rank}'s two build windows at ${s.station} to your calendar (.ics)`}
                >
                  <CalIcon />
                </a>
              </li>
            ))}
          </ul>
        </li>
      )}
      {years.map(([y, list]) => (
        <li key={y} className="gl-cal-yl__year">
          <h3 className="gl-cal-yl__h">
            {y}
            {y === thisYear && <span className="gl-cal-yl__this">this year</span>}
            <span className="gl-cal-yl__count">{list.length} starting</span>
          </h3>
          <ul className="gl-cal-yl__list">
            {list.map((e) => {
              if (e.kind === 'shared') {
                const w = e.w
                const a = pById[w.a]
                const b = pById[w.b]
                const derived = derivedOf(w)
                return (
                  <li key={e.key} className={`gl-cal-yl__item gl-cal-yl__item--shared gl-cal-yl__item--${w.ahead}${derived.length ? ' gl-cal-yl__item--derived' : ''}`}>
                    <button type="button" className="gl-cal-yl__main" data-pair={w.id} onClick={() => oById[w.id] && g.openDraft(oById[w.id])}>
                      <span className="gl-cal-yl__eyebrow">
                        #{w.rank} · shared build window{w.station ? ` · same station: ${w.station}` : ''}
                      </span>
                      <span className="gl-cal-yl__name">
                        {displayName(a?.name)} × {displayName(b?.name)}
                      </span>
                      <span className="gl-cal-yl__meta">
                        {span(w.start, w.end)} · {months(w.months)} · {derived.length ? `${w.ahead === 'past' ? 'passed' : NOW_WORD[w.ahead]} · ${derivedWords(derived)}` : NOW_WORD[w.ahead]}
                      </span>
                      <Mini s={w.start} e={w.end} kind="win" fx={fx} today={today} />
                    </button>
                    <a
                      className="gl-cal__ics gl-cal-yl__ics"
                      href={downloadUrl('calendar.ics', g.engineParams, { pair: w.id })}
                      download
                      aria-label={`Add pair ${w.rank}'s shared build window to your calendar (.ics)`}
                    >
                      <CalIcon />
                    </a>
                  </li>
                )
              }
              const p = e.p
              const isOpen = open === p.id
              const pairs = p.pairs.map((id) => oById[id]).filter(Boolean)
              return (
                <li key={e.key} className={`gl-cal-yl__item${p.pairs.length ? '' : ' is-dim'}`}>
                  <button type="button" className="gl-cal-yl__main" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : p.id)}>
                    <span className="gl-cal-yl__eyebrow">
                      <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
                      {utilityShort(p.utility)} · build window{p.start_as === 'derived' ? ' · start derived' : ''}
                    </span>
                    <span className="gl-cal-yl__name">{displayName(p.name)}</span>
                    <span className="gl-cal-yl__meta">
                      {span(p.start, p.end)} · in service {fmtDate(p.in_service)}
                      {p.pairs.length ? ` · ${p.pairs.length} pair${p.pairs.length === 1 ? '' : 's'}` : ''}
                    </span>
                    <Mini s={p.start} e={p.end} kind={p.start_as === 'derived' ? 'derived' : toneOf(p.utility)} fx={fx} today={today} />
                  </button>
                  {isOpen && (
                    <div className="gl-cal-yl__more">
                      <p className="gl-fine">{p.basis}</p>
                      {pairs.length > 0 && (
                        <ul className="gl-cal__pairs">
                          {pairs.slice(0, MAX_PAIRS_IN_CARD).map((o) => {
                            const other = o.a === p.id ? o.b : o.a
                            return (
                              <li key={o.id}>
                                <button type="button" onClick={() => g.openDraft(o)}>
                                  <span className="gl-cal__rank">#{o.rank}</span>
                                  <span className="gl-cal__pairname">
                                    <span className="gl-cal__ell">{displayName(pById[other]?.name) || other}</span>
                                  </span>
                                  <span className={`gl-cal__pairwhen gl-when--${whenOf(o).tone}`}>
                                    {o.shared_station ? `same station · ${whenOf(o).row}` : whenOf(o).row}
                                  </span>
                                </button>
                              </li>
                            )
                          })}
                        </ul>
                      )}
                      <button type="button" className="gl-link" onClick={() => g.openProject(p.id, { fly: true })}>
                        Where this came from
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </li>
      ))}
    </ol>
  )
}

// a window on a thin line of the years shown, with today marked: the list's small stand-in for the Gantt's bars
function Mini({ s, e, kind, fx, today }) {
  return (
    <span className="gl-cal-yl__mini" aria-hidden="true">
      <span className={`gl-cal-yl__seg gl-cal-yl__seg--${kind}`} style={{ left: `${fx(s) * 100}%`, width: `${Math.max(0.6, (fx(e, true) - fx(s)) * 100)}%` }} />
      {today >= 0 && today <= 1 && <span className="gl-cal-yl__now" style={{ left: `${today * 100}%` }} />}
    </span>
  )
}
