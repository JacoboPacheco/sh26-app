// FEATURE: Fix it + best sites (owned by the fix track).
// Contract: default export FixPanel() — the left panel while mode === 'fix'.
//
// Two answers after the problem: (1) the smallest set of line upgrades (in MVA added) that clears
// every overload in the current case, applied with one click so the map turns calm; (2) the towns
// whose substations can take a campus this size before any line overloads.
import { useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Badge, Button, EmptyState, ErrorBanner, Loading } from '../../ui'
import './fix.css'
import { isApplied, prettyName, runFix, setHoverSite, shownSites, useBestSites, useFix } from './fixStore'

export default function FixPanel() {
  return (
    <div className="stack panel-body fix">
      <FixSection />
      <BestSites />
    </div>
  )
}

// ------------------------------------------------------------------ (1) Fix it
function FixSection() {
  const o = useOverload()
  const { site, extraSites, trip, loadFactor, caseBody, result, solving, whatifError, upgrades, setUpgrades, setMode, focus, subPos } = o
  const hasCase = !!(site || extraSites.length || trip.length || loadFactor !== 1.0)
  const fix = useFix(caseBody)
  const nUp = Object.keys(upgrades).length
  // the what-if on screen was solved with the upgrades we have now (the header echoes them)
  const fresh = !!result && sameUpgrades(result.upgrades, upgrades)
  const over = fresh ? result.overloaded : null
  const applied = fix.status === 'done' && isApplied(fix.data.apply, upgrades)

  // find the fix, then frame the campus and every branch it upgrades (they can be far away:
  // a heat wave's bottleneck may be a transformer across the state)
  const find = async () => {
    const data = await runFix(caseBody)
    if (!data?.upgrades.length) return
    const pts = (result?.sites || []).map((s2) => [s2.sub_lon, s2.sub_lat])
    data.upgrades.forEach((u) => pts.push(subPos(u.from), subPos(u.to)))
    focus(padded(pts.filter(Boolean)))
  }

  let body
  if (!hasCase) {
    body = (
      <EmptyState
        title="Nothing to fix yet"
        action={
          <div className="row">
            <Button variant="secondary" onClick={() => setMode('campus')}>
              Go to Data center
            </Button>
          </div>
        }
      >
        Drop a data center on the map first, then come back for the smallest set of upgrades that keeps every line under its limit.
      </EmptyState>
    )
  } else if (whatifError) {
    body = <ErrorBanner error={whatifError} />
  } else if (fix.status === 'done') {
    // stays on screen while the what-if re-solves with (or without) the upgrades
    body = <FixResult fix={fix.data} applied={applied} over={over} solving={solving} onApply={() => setUpgrades(fix.data.apply)} onRemove={() => setUpgrades({})} />
  } else if (!over) {
    body = <Loading label={nUp ? 'Re-solving with the upgrades…' : 'Solving the grid…'} />
  } else if (over.length === 0) {
    body = (
      <div className="stack fix-calm">
        <p className="verdict verdict--ok">No line over limit.</p>
        {nUp > 0 ? (
          <>
            <p>
              {nUp} {nUp === 1 ? 'upgrade is' : 'upgrades are'} applied to this case.
            </p>
            <div className="row">
              <Button variant="secondary" onClick={() => setUpgrades({})}>
                Remove upgrades
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="muted">Nothing to fix here. Try a bigger campus or another site in Data center mode.</p>
            <div className="row">
              <Button variant="secondary" onClick={() => setMode('campus')}>
                Go to Data center
              </Button>
            </div>
          </>
        )}
      </div>
    )
  } else {
    body = (
      <div className="stack">
        <p className="verdict verdict--bad">{overLimitText(over)}</p>
        <p className="muted">
          Raise the most overloaded line one 50 MVA step at a time, re-solve, and repeat until nothing is over its limit.
        </p>
        <ErrorBanner error={fix.error} onRetry={find} />
        <div className="row">
          <Button busy={fix.status === 'loading' || solving} onClick={find}>
            {fix.status === 'loading' ? 'Finding the fix…' : 'Find the smallest fix'}
          </Button>
        </div>
        {nUp > 0 && (
          <p className="muted fix-note">
            {nUp} {nUp === 1 ? 'upgrade is' : 'upgrades are'} already applied; the fix builds on {nUp === 1 ? 'it' : 'them'}.{' '}
            <button type="button" className="fix-link" onClick={() => setUpgrades({})}>
              Remove {nUp === 1 ? 'it' : 'them'}
            </button>
          </p>
        )}
      </div>
    )
  }

  return (
    <section className="stack fix-sec" aria-labelledby="fix-h">
      <div className="fix-head">
        <h3 className="panel-h" id="fix-h">
          The smallest fix
        </h3>
        {hasCase && <CaseLine />}
      </div>
      <div aria-live="polite">{body}</div>
    </section>
  )
}

function CaseLine() {
  const { result, mw, site, extraSites, trip } = useOverload()
  const parts = []
  if (site) parts.push(`${fmt(mw)} MW at ${result?.sub_name ? prettyName(result.sub_name) : 'the site'}`)
  if (extraSites.length) parts.push(`${extraSites.length} more ${extraSites.length === 1 ? 'data center' : 'data centers'}`)
  if (trip.length) parts.push(`${trip.length} ${trip.length === 1 ? 'line' : 'lines'} knocked out`)
  if (!parts.length) return null
  return <p className="fix-case">{parts.join(' · ')}</p>
}

const SHOW_UPS = 6 // a big case can need dozens of upgrades; list the first few, the rest on request

function FixResult({ fix, applied, over, solving, onApply, onRemove }) {
  const [all, setAll] = useState(false)
  const n = fix.upgrades.length
  if (n === 0) {
    // the case was already calm when the fix ran (or nothing could be raised)
    return fix.calm ? <p className="verdict verdict--ok">No line over limit.</p> : <Unfixable fix={fix} />
  }
  const calmNow = applied && over?.length === 0
  const shown = all ? fix.upgrades : fix.upgrades.slice(0, SHOW_UPS)
  return (
    <div className="stack fix-result">
      <div className={`fix-total${calmNow ? ' fix-total--done' : ''}`}>
        <span className="fix-total__n">+{fmt(fix.added_mva)} MVA</span>
        <span className="fix-total__label">
          {n} {n === 1 ? 'upgrade' : 'upgrades'}{' '}
          {fix.calm ? `clear${n === 1 ? 's' : ''} ${whichOverloads(fix.over_before)}` : `ease ${fix.over_before} overloads, but not all of them`}
        </span>
      </div>
      <ol className="fix-ups">
        {shown.map((u) => (
          <li key={u.id} className="fix-up">
            <div className="fix-up__name">
              <span>{u.transformer ? prettyName(u.from_name) : `${prettyName(u.from_name)} → ${prettyName(u.to_name)}`}</span>
              {u.transformer && <Badge>Transformer</Badge>}
            </div>
            <div className="fix-up__meta">
              <span>
                {fmt(u.kv)} kV · {fmt(u.old_mva)} → <strong>{fmt(u.new_mva)} MVA</strong>
              </span>
              <span className="fix-up__pct">
                <span className="fix-pct--over">{Math.round(u.pct_before)} %</span> →{' '}
                <span className={u.pct_after > 100 ? 'fix-pct--over' : 'fix-pct--ok'}>{Math.round(u.pct_after)} %</span>
              </span>
            </div>
          </li>
        ))}
      </ol>
      {n > SHOW_UPS && (
        <div className="row">
          <button type="button" className="fix-link" aria-expanded={all} onClick={() => setAll((v) => !v)}>
            {all ? 'Show fewer' : `Show all ${n} upgrades`}
          </button>
        </div>
      )}
      {!fix.calm && <Unfixable fix={fix} />}
      {applied ? (
        <>
          {!over ? (
            <Loading label="Re-solving with the upgrades…" />
          ) : over.length === 0 ? (
            <p className="verdict verdict--ok">No line over limit.</p>
          ) : (
            <p className="verdict verdict--bad">{overLimitText(over)}</p>
          )}
          {over?.length === 0 && <p className="muted">Run the cascade now and nothing trips.</p>}
          <div className="row">
            <Button variant="secondary" onClick={onRemove}>
              Remove upgrades
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="muted">Ratings only: no new lines, no cost model. Applying them re-solves the grid.</p>
          <div className="row">
            <Button busy={solving} onClick={onApply}>
              Apply upgrades
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

// Why some lines are still over: the search's limit, and/or lines that would need more than 5x.
function Unfixable({ fix }) {
  const k = fix.remaining_count ?? fix.remaining.length
  const capped = fix.capped_count || 0
  return (
    <p className="fix-warn" role="note">
      {k} {k === 1 ? 'branch stays' : 'branches stay'} over limit.{' '}
      {fix.upgrade_cap_reached
        ? `The case already holds the most upgrades it can take (${fix.max_upgrades ?? 200}).`
        : fix.stopped_at_limit
          ? `The search stops after ${fix.max_rounds ?? 60} upgrades; a case this big needs new lines, not just higher ratings.`
          : `Re-rating tops out at 5× a line's rating, so ${capped === 1 || k === 1 ? 'it needs' : 'they need'} a new line.`}
    </p>
  )
}

// ------------------------------------------------------------------ (2) Best sites
function BestSites() {
  const { mw, loadFactor, place, setMode, focus, region } = useOverload()
  const best = useBestSites(mw, loadFactor, true, region)
  const { list, fits } = shownSites(best.data)
  const level = Math.round(loadFactor * 100)

  const putHere = (s) => {
    setHoverSite(null)
    place(s.lat, s.lon)
    setMode('campus')
  }

  return (
    <section className="stack fix-sec" aria-labelledby="best-h">
      <h3 className="panel-h" id="best-h">
        Best sites for {fmt(mw)} MW
      </h3>
      {best.status === 'error' ? (
        <ErrorBanner error={best.error} onRetry={best.retry} />
      ) : best.status !== 'done' ? (
        <Loading label="Ranking substations…" />
      ) : (
        <>
          {fits && list[0]?.headroom_at_least ? (
            <p className="muted">
              Each takes {fmt(mw)} MW with no line over its limit{level !== 100 ? ` at ${level} % of normal demand` : ''}, checked with a full solve.
              One substation per town, the coolest grid first.
            </p>
          ) : fits ? (
            <p className="muted">
              Most headroom before any line overloads, one substation per town{level !== 100 ? `, at ${level} % of normal demand` : ''}. Each is
              checked with a full solve; ties go to the site where the busiest line runs coolest.
            </p>
          ) : (
            <p>
              No substation takes {fmt(mw)} MW before a line overloads{level !== 100 ? ` at ${level} % of normal demand` : ''}. The most any one takes is{' '}
              <strong>{fmt(best.data.max_headroom_mw)} MW</strong>. Where the smallest upgrades would carry it:
            </p>
          )}
          <ol className={`fix-sites${fits ? '' : ' fix-sites--closest'}`}>
            {list.map((s) => (
              <li
                key={s.sub}
                className="fix-site"
                onMouseEnter={() => setHoverSite(s.sub)}
                onMouseLeave={() => setHoverSite(null)}
                onFocus={() => setHoverSite(s.sub)}
                onBlur={() => setHoverSite(null)}
              >
                <span className="fix-site__rank" aria-hidden="true">
                  {s.rank}
                </span>
                <div className="fix-site__text" title={`${prettyName(s.name)} · ${fmt(s.kv)} kV`}>
                  <strong>{s.town}</strong>
                  {!fits && s.fix_mva > 0 && (
                    <span className="fix-site__up">
                      +{fmt(s.fix_mva)} MVA of upgrades{s.fix_calm ? '' : ', and still over'}
                    </span>
                  )}
                  <span className="muted">
                    {s.headroom_at_least ? 'At least ' : ''}
                    {fmt(s.headroom_mw)} MW headroom
                  </span>
                  {fits && s.busiest_pct != null && <span className="muted">Busiest line then: {Math.round(s.busiest_pct)} %</span>}
                </div>
                <Button variant="secondary" onClick={() => putHere(s)} aria-label={`Put the ${fmt(mw)} MW campus at ${s.town}`}>
                  Put it here
                </Button>
              </li>
            ))}
          </ol>
          {list.length > 1 && (
            <div className="row">
              <Button variant="secondary" onClick={() => focus(list.map((s) => [s.lon, s.lat]))}>
                Show them on the map
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ helpers
// The map's focus frames points edge to edge, but panels float over the map's edges: widen the box
// (a third of its size, at least ~0.15°) so the upgrades land in the open middle.
function padded(pts) {
  if (pts.length < 2) return pts
  const lons = pts.map((p) => p[0])
  const lats = pts.map((p) => p[1])
  const [x0, x1, y0, y1] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)]
  const dx = Math.max((x1 - x0) / 3, 0.15)
  const dy = Math.max((y1 - y0) / 3, 0.15)
  return [...pts, [x0 - dx, y0 - dy], [x1 + dx, y1 + dy]]
}

const whichOverloads = (n) => (n === 1 ? 'the overload' : n === 2 ? 'both overloads' : `all ${n} overloads`)

function sameUpgrades(a, b) {
  const ea = Object.entries(a || {})
  const bb = b || {}
  return ea.length === Object.keys(bb).length && ea.every(([id, v]) => Math.abs(Number(bb[id]) - Number(v)) < 0.05)
}

// A branch with both ends in one substation is a transformer; count it apart from lines.
function overLimitText(overloaded) {
  const lines = overloaded.filter((b) => b.from !== b.to).length
  const xfmrs = overloaded.length - lines
  const parts = []
  if (lines) parts.push(`${lines} ${lines === 1 ? 'line' : 'lines'}`)
  if (xfmrs) parts.push(`${xfmrs} ${xfmrs === 1 ? 'transformer' : 'transformers'}`)
  return `${parts.join(' and ')} over limit`
}
