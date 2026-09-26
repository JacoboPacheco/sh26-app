// The AI boom, year by year: a full-width time-lapse 2026 -> 2035 of the reported data-center campuses in one state,
// all connected together on the state's SYNTHETIC grid model (backend/timelapse.py). Play steps a year every 1.5 s:
// campuses arrive as rings in their reported year, the lines warm, the strain charts build, and the counter compares
// the GW reported with what the Strengthen study says the grid carries. Pause and scrub at any time; with reduced
// motion each year simply replaces the last. Nothing plays sound.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../../geo'
import useCountUp from '../../shell/useCountUp'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import { OverChart, PeakChart } from './StrainChart'
import TimelapseMap from './TimelapseMap'
import { DEFAULT_STATUSES, STATUS_LABELS, loadTimelapse, moneyText, mwText, reducedMotion } from './timelapseApi'
import './timelapse.css'

const STEP_MS = 1500
const ARRIVALS_SHOWN = 6

export default function Timelapse({ onClose }) {
  const o = useOverload()
  const { grid, region, setMode } = o
  const [statuses, setStatuses] = useState(DEFAULT_STATUSES)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [wait, setWait] = useState(null)
  const [growthOn, setGrowthOn] = useState(true)
  const [idx, setIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const autoplayed = useRef(false)
  const headRef = useRef(null)

  // fetch (and poll) the time-lapse for this state and these statuses
  useEffect(() => {
    let alive = true
    loadTimelapse(region, statuses, () => alive, (r) => setWait(r))
      .then((r) => {
        if (!r || !alive) return
        setData(r)
        setWait(null)
        setError(null)
        setIdx((i) => Math.min(i, r.frames.length - 1))
        if (!autoplayed.current) {
          autoplayed.current = true
          setIdx(0)
          setPlaying(true)
        }
      })
      .catch((e) => alive && setError(e))
    return () => {
      alive = false
    }
  }, [region, statuses, attempt])

  const hasGrowth = !!data?.growth
  const useGrowth = hasGrowth && growthOn
  const frames = useMemo(() => (data ? (useGrowth ? data.growth.frames : data.frames) : []), [data, useGrowth])
  const pctRows = data ? (useGrowth ? data.lines.pct_growth : data.lines.pct) : null
  const last = frames.length - 1
  const frame = frames[Math.min(idx, Math.max(last, 0))]

  // play: one year every STEP_MS; it stops by itself on the last year
  const running = playing && idx < last
  useEffect(() => {
    if (!running) return undefined
    const t = setInterval(() => setIdx((i) => Math.min(i + 1, last)), STEP_MS)
    return () => clearInterval(t)
  }, [running, last])

  // the sheet starts under the top bar as it is actually drawn (it can wrap to more rows on a phone)
  const sheetRef = useRef(null)
  useLayoutEffect(() => {
    const place = () => {
      const bar = document.querySelector('.appbar')
      if (!bar || !sheetRef.current) return
      const r = bar.getBoundingClientRect()
      sheetRef.current.style.top = `${Math.round(Math.max(r.bottom, r.top + bar.scrollHeight))}px`
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [])

  // Escape closes; the heading takes focus when the sheet opens
  useEffect(() => {
    headRef.current?.focus()
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const newIds = useMemo(() => new Set(frame?.new || []), [frame])
  const stateName = data?.region_name || grid?.meta?.region_name || 'this state'

  function play() {
    if (idx >= last) {
      setIdx(0)
      setPlaying(true)
    } else setPlaying(!running)
  }
  function retry() {
    setError(null)
    setAttempt((a) => a + 1)
  }
  function toggleStatus(s) {
    setError(null)
    setStatuses((cur) => {
      const next = cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]
      return next.length ? ['operating', 'under construction', 'announced', 'paused/canceled'].filter((x) => next.includes(x)) : cur
    })
  }
  function toStrengthen() {
    onClose()
    setMode('unlock')
  }

  return (
    <div className="tl" role="dialog" aria-modal="true" aria-labelledby="tl-title" ref={sheetRef}>
      <header className="tl-head">
        <div className="tl-head__text">
          <p className="tl-eyebrow">The AI boom, year by year · {stateName}</p>
          <h2 id="tl-title" className="tl-title" tabIndex={-1} ref={headRef}>
            Reported AI data centers arrive, year by year, on a synthetic model of {stateName}&apos;s grid
          </h2>
          <p className="tl-frame">{data?.frame || 'Campuses of the reported sizes at the reported places, tested on a synthetic grid model. Statuses and years as reported.'}</p>
        </div>
        <Button variant="secondary" onClick={onClose}>
          Back to the map
        </Button>
      </header>

      {!data ? (
        <div className="tl-wait">
          {error ? (
            <ErrorBanner error={error} onRetry={retry} />
          ) : (
            <Loading label={wait ? `Testing every year on ${stateName}'s model… (about ${wait.estimate_s} s)` : 'Loading the time-lapse…'} />
          )}
        </div>
      ) : (
        <div className="tl-body">
          <section className="tl-stage" aria-label="Map and controls">
            <div className="tl-mapwrap">
              <TimelapseMap grid={grid} region={region} data={data} frame={frame} pcts={pctRows?.[idx]} prevPcts={idx > 0 ? pctRows?.[idx - 1] : null} newIds={newIds} />
              <YearStamp frame={frame} growth={useGrowth} />
              <MapKey hot={data.hot_pct} />
            </div>
            <Controls
              frames={frames}
              idx={idx}
              setIdx={(i) => {
                setPlaying(false)
                setIdx(i)
              }}
              playing={running}
              onPlay={play}
            />
            <div className="tl-options">
              {hasGrowth ? (
                <label className="tl-check">
                  <input type="checkbox" checked={growthOn} onChange={(e) => setGrowthOn(e.target.checked)} />
                  <span>
                    Grow the model&apos;s own load with the published forecast{' '}
                    <span className="muted">
                      ({data.growth.area}, {fmt(data.growth.mw['2026'])} → {fmt(data.growth.mw['2035'])} MW by 2035, as published)
                    </span>
                  </span>
                </label>
              ) : (
                <p className="muted tl-note">{data.growth_note}</p>
              )}
              <fieldset className="tl-statuses">
                <legend>Statuses that count (as reported)</legend>
                {data.status_options.map((s) => (
                  <label key={s.id} className="tl-check tl-check--chip">
                    <input type="checkbox" checked={statuses.includes(s.id)} onChange={() => toggleStatus(s.id)} disabled={statuses.includes(s.id) && statuses.length === 1} />
                    <span>
                      {STATUS_LABELS[s.id] || s.id} <span className="muted">· {s.count}</span>
                    </span>
                  </label>
                ))}
                {wait && <span className="muted tl-note">Updating…</span>}
                {error && <ErrorBanner error={error} onRetry={retry} />}
              </fieldset>
            </div>
          </section>

          <aside className="tl-read">
            {/* one short announcement per year for screen readers (the counter counts up silently) */}
            <p className="tl-sr" aria-live="polite" aria-atomic="true">
              {yearAnnouncement(frame)}
            </p>
            {idx === last && last > 0 && <Closing data={data} frames={frames} useGrowth={useGrowth} onStrengthen={toStrengthen} />}
            <Counter data={data} frame={frame} />
            <StrainNow frame={frame} hot={data.hot_pct} />
            <div className="tl-charts">
              <PeakChart frames={frames} idx={idx} />
              <OverChart frames={frames} idx={idx} growth={useGrowth} />
            </div>
            <Arrivals data={data} frame={frame} playing={running} />
          </aside>

          <section className="tl-folds" aria-label="Details">
            <LeftOut data={data} />
            <ByYear frames={frames} />
            <Method data={data} useGrowth={useGrowth} />
          </section>
        </div>
      )}
    </div>
  )
}

// The year and its headline, said once per year (the aria-live region above the readout).
function yearAnnouncement(frame) {
  if (!frame) return ''
  if (!frame.year) return 'The grid alone, before the campuses.'
  const s = frame.strain
  return `${frame.year}: ${mwText(frame.mw)} of reported campuses online on the model. Busiest element at ${Math.round(s.peak_pct)} % of its rating; ${s.over} over their rating.`
}

// "2 under construction, 2 announced": the statuses of the campuses online by this year, as reported.
function statusMix(campuses, year) {
  const counts = {}
  for (const c of campuses) if (c.first_year <= year) counts[c.status] = (counts[c.status] || 0) + 1
  return Object.keys(STATUS_LABELS)
    .filter((s) => counts[s])
    .map((s) => `${counts[s]} ${statusText(s)}`)
    .join(', ')
}

function YearStamp({ frame, growth }) {
  const up = growth && frame?.year && frame.load_factor > 1.0001 ? ` · the model's load +${((frame.load_factor - 1) * 100).toFixed(1)} % (forecast)` : ''
  return (
    <div className="tl-stamp" aria-hidden="true">
      <span key={frame?.year ?? 'now'} className="tl-stamp__year">
        {frame?.year ?? 'Now'}
      </span>
      <span className="tl-stamp__sub">
        {frame?.year ? `${frame.campuses} ${frame.campuses === 1 ? 'campus' : 'campuses'} online, as reported${up}` : 'The grid alone, before the campuses'}
      </span>
    </div>
  )
}

function MapKey({ hot }) {
  return (
    <ul className="tl-key" aria-label="Map key">
      <li>
        <span className="tl-key__sw tl-key__sw--warm" /> 60 %+ of rating
      </li>
      <li>
        <span className="tl-key__sw tl-key__sw--strain" /> 80 %+ (strain; the counts use {hot} %+)
      </li>
      <li>
        <span className="tl-key__sw tl-key__sw--over" /> over its rating
      </li>
      <li>
        <span className="tl-key__ring" /> reported campus (size)
      </li>
    </ul>
  )
}

function Controls({ frames, idx, setIdx, playing, onPlay }) {
  const last = frames.length - 1
  const label = (f) => (f.year == null ? 'Now' : String(f.year))
  // an end label next to the year you are on would touch it on a phone ("Now2026"): the CSS hides it there
  const tickClass = (f, k) =>
    [k === idx && 'tl-tick--on', f.new?.length && 'tl-tick--arrive', (k === 0 || k === last) && Math.abs(k - idx) === 1 && 'tl-tick--near'].filter(Boolean).join(' ')
  return (
    <div className="tl-controls">
      <Button onClick={onPlay} aria-pressed={playing}>
        {playing ? 'Pause' : idx >= last ? 'Play again' : idx === 0 ? 'Play' : 'Resume'}
      </Button>
      <div className="tl-scrub">
        <label htmlFor="tl-year" className="tl-scrub__label">
          Year
        </label>
        <input
          id="tl-year"
          type="range"
          min={0}
          max={last}
          step={1}
          value={idx}
          aria-valuetext={frames[idx]?.year ? String(frames[idx].year) : 'The grid alone'}
          onChange={(e) => setIdx(Number(e.target.value))}
        />
        <ol className="tl-ticks" aria-hidden="true">
          {frames.map((f, k) => (
            <li key={k} className={tickClass(f, k) || undefined}>
              <button type="button" tabIndex={-1} onClick={() => setIdx(k)}>
                {k === 0 || k === last || k === idx ? label(f) : `’${String(f.year).slice(2)}`}
              </button>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

function Counter({ data, frame }) {
  const cap = data.capacity || {}
  const mw = useCountUp(frame?.mw || 0, reducedMotion() ? 1 : 600)
  const ready = cap.status === 'ready'
  const finalMw = data.frames[data.frames.length - 1].mw
  const scale = Math.max(ready ? cap.with_upgrades_mw * 1.12 : 0, finalMw * 1.06, 1000)
  const pct = (v) => `${Math.min(100, (v / scale) * 100).toFixed(2)}%`
  const under = ready ? Math.min(mw, cap.today_mw) : mw
  const over = ready ? Math.max(0, mw - cap.today_mw) : 0
  return (
    <section className="tl-counter" aria-label="Reported gigawatts against what the grid carries">
      <p className="tl-counter__label">Reported AI campuses online on the model</p>
      <p className="tl-counter__n">{mwText(mw)}</p>
      <p className="tl-counter__sub">
        {frame?.year
          ? frame.campuses
            ? `${frame.campuses} ${frame.campuses === 1 ? 'campus' : 'campuses'} by ${frame.year} · status as reported: ${statusMix(data.campuses, frame.year)}`
            : `No reported campus online by ${frame.year}`
          : 'The grid alone, before the campuses'}
      </p>
      <div className="tl-meter" role="img" aria-label={ready ? `${mwText(frame?.mw || 0)} reported; the synthetic grid carries ${mwText(cap.today_mw)} today and ${mwText(cap.with_upgrades_mw)} with the Strengthen upgrades` : `${mwText(frame?.mw || 0)} reported`}>
        <span className="tl-meter__fill" style={{ width: pct(under) }} />
        {over > 0 && <span className="tl-meter__fill tl-meter__fill--over" style={{ left: pct(under), width: pct(over) }} />}
        {ready && (
          <>
            <span className="tl-meter__mark" style={{ left: pct(cap.today_mw) }} />
            <span className="tl-meter__mark tl-meter__mark--fix" style={{ left: pct(cap.with_upgrades_mw) }} />
          </>
        )}
      </div>
      {ready ? (
        <dl className="tl-cap">
          <div>
            <dt>The synthetic grid carries today</dt>
            <dd>{mwText(cap.today_mw)}</dd>
          </div>
          <div className="tl-cap__fix">
            <dt>With the Strengthen upgrades ({moneyText(cap.cost_high)}, high end)</dt>
            <dd>{mwText(cap.with_upgrades_mw)}</dd>
          </div>
        </dl>
      ) : (
        <p className="muted tl-note">{cap.note}</p>
      )}
      {ready && <p className="tl-note muted">{cap.caveat}</p>}
    </section>
  )
}

function StrainNow({ frame, hot }) {
  const s = frame.strain
  const top = frame.busiest?.[0]
  return (
    <section className="tl-strain" aria-label="Grid strain this year">
      <dl className="tl-figs">
        <div className={s.peak_pct >= 100 ? 'tl-fig tl-fig--over' : s.peak_pct >= 80 ? 'tl-fig tl-fig--strain' : 'tl-fig'}>
          <dt>Busiest element</dt>
          <dd>{Math.round(s.peak_pct)} %</dd>
        </div>
        <div className={s.over > 0 ? 'tl-fig tl-fig--over' : 'tl-fig'}>
          <dt>Over their rating</dt>
          <dd>{s.over}</dd>
        </div>
        <div className={s.hot > 0 ? 'tl-fig tl-fig--strain' : 'tl-fig'}>
          <dt>At {hot} %+</dt>
          <dd>{s.hot}</dd>
        </div>
        <div className={s.over_mw > 0 ? 'tl-fig tl-fig--over' : 'tl-fig'}>
          <dt>Flow above ratings</dt>
          <dd>{mwText(s.over_mw)}</dd>
        </div>
      </dl>
      {top && (
        <p className="tl-busiest">
          Busiest: {top.label} ({top.kv} kV) at {Math.round(top.pct)} % of its rating.
        </p>
      )}
      {s.unserved_mw > 0 && <p className="tl-busiest">The model&apos;s plants can&apos;t supply {mwText(s.unserved_mw)} of the load this year.</p>}
    </section>
  )
}

function statusText(s) {
  return STATUS_LABELS[s] ? STATUS_LABELS[s].toLowerCase() : s
}
function Arrivals({ data, frame, playing }) {
  if (!frame?.year)
    return (
      <p className="tl-arrivals__none muted">
        {playing ? 'The grid alone first; the reported campuses arrive from 2026, year by year.' : 'Press Play: each year the reported campuses come online on the model.'}
      </p>
    )
  const byId = new Map(data.campuses.map((c) => [c.id, c]))
  const list = (frame.new || []).map((id) => byId.get(id)).filter(Boolean)
  if (!list.length) return <p className="tl-arrivals__none muted">No reported arrivals in {frame.year}.</p>
  return (
    <section className="tl-arrivals" aria-label={`Arriving in ${frame.year}`}>
      <h3 className="tl-h">Arriving in {frame.year}, as reported</h3>
      <ul>
        {list.slice(0, ARRIVALS_SHOWN).map((c) => {
          const now = c.arrivals.filter((a) => a.year === frame.year).reduce((s, a) => s + a.mw, 0)
          const src = c.sources[0]
          return (
            <li key={c.id} className="tl-campus">
              <p className="tl-campus__name">{c.name}</p>
              <p className="tl-campus__meta">
                {c.company} · {c.city}
                {c.county ? `, ${c.county} County` : ''}
              </p>
              <p className="tl-campus__mw">
                <strong>{mwText(now)}</strong>
                {now < c.mw - 0.5 ? ` of a reported ${mwText(c.mw)}` : ' reported'} · {statusText(c.status)} · reported year “{c.year_text}”
              </p>
              {c.higher_end && (
                <p className="tl-campus__flag">Higher end: the report dates a first phase or a range without its size, so the full reported size counts from {c.first_year}.</p>
              )}
              {src && (
                <p className="tl-campus__src">
                  As reported by{' '}
                  <a href={src.url} target="_blank" rel="noreferrer">
                    {src.title}
                  </a>
                  {c.sources.length > 1 ? ` +${c.sources.length - 1} more` : ''}
                </p>
              )}
            </li>
          )
        })}
      </ul>
      {list.length > ARRIVALS_SHOWN && <p className="muted tl-note">and {list.length - ARRIVALS_SHOWN} more (listed under Numbers by year and on the map)</p>}
    </section>
  )
}

const overText = (n) => `${n} ${n === 1 ? 'line or transformer runs' : 'lines and transformers run'} over ${n === 1 ? 'its' : 'their'} rating`

function Closing({ data, frames, useGrowth, onStrengthen }) {
  const cap = data.capacity || {}
  // what the campuses do on their own: the model's load at its snapshot (data.frames); the growth sentence comes after
  const end = data.frames[data.frames.length - 1]
  const shown = frames[frames.length - 1]
  const name = data.region_name
  const first = data.frames.find((f) => f.year && f.strain.over > 0)
  const over = end.strain.over
  const lead = `By ${end.year}, ${mwText(end.mw)} of reported campuses are online on the model`
  let line
  if (!end.mw) line = `No reported campus in ${name} has a reported online year between 2026 and 2035.`
  else if (cap.status !== 'ready') line = `${lead}${first ? `, and from ${first.year} lines run over their rating (${over} by ${end.year})` : ''}.`
  else if (end.mw <= cap.today_mw && over > 0)
    line = `${lead}: less than the ${mwText(cap.today_mw)} the Strengthen study finds it carries at the sites it picks, yet at the reported places ${overText(over)}. Where campuses connect matters as much as how much.`
  else if (end.mw <= cap.today_mw) line = `${lead}, within the ${mwText(cap.today_mw)} it carries today.`
  else if (end.mw <= cap.with_upgrades_mw)
    line = `${lead}: more than the ${mwText(cap.today_mw)} it carries today, within the ${mwText(cap.with_upgrades_mw)} it carries with ${moneyText(cap.cost_high)} of upgrades.`
  else line = `${lead}: more than the ${mwText(cap.with_upgrades_mw)} it carries even with ${moneyText(cap.cost_high)} of upgrades.`
  // with the forecast on, say what it adds and what the load growth alone (no campus) puts over
  let growth = null
  if (end.mw && useGrowth && shown?.alone && (shown.strain.over !== over || shown.alone.over > 0)) {
    const alone = shown.alone.over
    growth = `With the published load growth on top, ${overText(shown.strain.over)}${alone > 0 ? `; the load growth alone, without any campus, puts ${alone} over` : ''}.`
  }
  return (
    <section className="tl-closing">
      <p>{line}</p>
      {growth && <p className="tl-closing__growth">{growth}</p>}
      <Button onClick={onStrengthen}>What it takes: Strengthen the grid</Button>
    </section>
  )
}

function LeftOut({ data }) {
  const L = data.left_out
  const groups = [
    ['no_year', 'No reported online year'],
    ['later', 'Reported for after 2035'],
    ['off_model', 'Off the model'],
    ['status', 'Status not selected (as reported)'],
    ['no_size', 'No reported size'],
  ]
  const partial = data.campuses.filter((c) => c.no_year_mw > 0.5)
  const n = groups.reduce((s, [k]) => s + (L[k]?.length || 0), 0)
  const a = data.accounting
  return (
    <details className="tl-fold">
      <summary>
        Left out: {n} {n === 1 ? 'campus' : 'campuses'}
        {partial.length ? ` and part of ${partial.length} more` : ''} · {mwText(a.catalog_mw - a.placed_mw)} of {mwText(a.catalog_mw)} in the catalog
      </summary>
      {groups.map(([k, title]) =>
        L[k]?.length ? (
          <div key={k} className="tl-fold__group">
            <h4>
              {title} ({L[k].length})
            </h4>
            <ul>
              {L[k].map((c) => (
                <li key={c.id}>
                  <strong>{c.name}</strong> · {mwText(c.mw || 0)} · {statusText(c.status)} · “{c.year_text || 'no year'}” — {c.why}
                  {c.sources[0] && (
                    <>
                      {' '}
                      (
                      <a href={c.sources[0].url} target="_blank" rel="noreferrer">
                        source
                      </a>
                      )
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
      {partial.length > 0 && (
        <div className="tl-fold__group">
          <h4>Part of a campus with no reported year ({partial.length})</h4>
          <ul>
            {partial.map((c) => (
              <li key={c.id}>
                <strong>{c.name}</strong>: {c.read}
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  )
}

function ByYear({ frames }) {
  return (
    <details className="tl-fold">
      <summary>Numbers by year</summary>
      <div className="tl-table-wrap">
        <table className="tl-table">
          <caption className="muted">Every campus online so far, connected at once (one steady-state DC power flow per year)</caption>
          <thead>
            <tr>
              <th scope="col">Year</th>
              <th scope="col">Reported online</th>
              <th scope="col">Busiest %</th>
              <th scope="col">Over</th>
              <th scope="col">At 90 %+</th>
              <th scope="col">Flow above ratings</th>
            </tr>
          </thead>
          <tbody>
            {frames.map((f, k) => (
              <tr key={k}>
                <th scope="row">{f.year ?? 'Grid alone'}</th>
                <td>{mwText(f.mw)}</td>
                <td>{f.strain.peak_pct}</td>
                <td>{f.strain.over}</td>
                <td>{f.strain.hot}</td>
                <td>{mwText(f.strain.over_mw)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function Method({ data, useGrowth }) {
  const cap = data.capacity || {}
  const higher = data.campuses.filter((c) => c.higher_end)
  return (
    <details className="tl-fold">
      <summary>How this is built</summary>
      <p>{data.method}</p>
      {higher.length > 0 && (
        <p>
          Counted at the full reported size from the first reported year (the higher end):{' '}
          {higher.map((c) => `${c.name}, ${mwText(c.placed_mw)} from ${c.first_year} (“${c.year_text}”)`).join('; ')}.
        </p>
      )}
      <p>{data.frame}</p>
      {data.growth ? (
        <p>
          Load growth{useGrowth ? ' (on)' : ' (off)'}: {data.growth.measure}, {data.growth.area}, from{' '}
          <a href={data.growth.source.url} target="_blank" rel="noreferrer">
            {data.growth.source.title}
          </a>
          . {data.growth.note}
        </p>
      ) : (
        <p>{data.growth_note}</p>
      )}
      {cap.status === 'ready' && (
        <p>
          {cap.source}. {cap.caveat}
        </p>
      )}
      <p className="muted">
        Campuses: the project&apos;s sourced catalog ({data.catalog.file}, compiled {data.catalog.generated}). {data.note} Grid: Breakthrough Energy Sciences U.S. Test
        System, from Texas A&amp;M ACTIVSg synthetic grids (CC-BY 4.0).
      </p>
    </details>
  )
}
