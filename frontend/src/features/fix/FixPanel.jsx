// FEATURE: Fix it + best sites (owned by the fix track).
// Contract: default export FixPanel() — the left panel while mode === 'fix'.
//
// Two answers after the problem: (1) the smallest set of line upgrades (in MVA added) that clears
// every overload in the current case, applied with one click so the map turns calm; (2) a ranked
// screening table of the towns whose substations take a campus this size with no line overloaded.
import { useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Badge, Button, EmptyState, ErrorBanner, Loading } from '../../ui'
import { useLossRate } from '../impact/caseCost'
import './fix.css'
import './flip.css'
import { describeFix, flipKey, flipSide, plantsOut, runWithFix, showWith, useBestFix, useFlip, withFix } from './flipCase'
import { isApplied, prettyName, runFix, setHoverSite, shownSites, useBestSites, useFix, useHoverSite } from './fixStore'

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
  const { site, extraSites, trip, loadFactor, caseBody, result, solving, whatifError, upgrades, setUpgrades, setMode, focus, subPos, cascade, cascading } = o
  const flip = useFlip()
  const rate = useLossRate() // the cost of the outage the results column shows, for the before/after line
  // the briefing report of this case (cached; Florida's is warm, elsewhere only once a cascade ran): the same
  // upgrades are one of its verified fixes, with their cost (the high end)
  const hasCase = !!(site || extraSites.length || trip.length || loadFactor !== 1.0)
  const fix = useFix(caseBody)
  const { report } = useBestFix(caseBody, !!fix.data && (!!cascade || o.region === 'FL'))
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
    // one click: the same case runs again with these upgrades and the results column shows the before/after
    // (features/fix/flip.js, the same path as the panel's "Run it again with the fix")
    const up = { upgrades: fix.data.apply }
    const target = flipKey(withFix(caseBody, up))
    const same = (report?.fixes || []).find((f) => f.apply && f.verdict === 'holds' && flipKey(withFix(caseBody, f.apply)) === target)
    const runIt = () => {
      if (flip.fix && flipSide(flip, caseBody) === 'base' && target === flip.fix.key) return showWith(o)
      const described = same && describeFix(same, Number(report?.case?.mw) || 0)
      // `fixit`: the search's own numbers per element (MVA, loading before and after), for What the fix changes
      runWithFix(
        o,
        { ...(described || {}), apply: up, words: described?.words || fixWords(fix.data), by: 'engine', verdict: fix.data.calm ? 'holds' : 'partly', upgrades: fix.data.upgrades.map((u) => u.id), fixit: fix.data.upgrades, origin: 'fixit' },
        { base: caseBody, rate, report },
      )
    }
    body = (
      <FixResult
        fix={fix.data}
        applied={applied}
        over={over}
        solving={solving}
        ran={!!cascade}
        running={cascading && flip.status === 'running'}
        plantCase={plantsOut(cascade)}
        onRun={runIt}
        onApply={() => setUpgrades(fix.data.apply)}
        onRemove={() => setUpgrades({})}
      />
    )
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

// "Raise the North Fort Myers 6 transformer and 1 line" (the Fix it search's upgrades, biggest first)
function fixWords(fix) {
  const ups = [...(fix.upgrades || [])].sort((a, b) => (b.new_mva - b.old_mva) - (a.new_mva - a.old_mva))
  if (!ups.length) return 'The smallest fix'
  const name = (u) => (u.transformer ? `the ${prettyName(u.from_name)} transformer` : `the ${prettyName(u.from_name)} to ${prettyName(u.to_name)} line`)
  const rest = ups.slice(1)
  const nx = rest.filter((u) => u.transformer).length
  const nl = rest.length - nx
  const more = []
  if (nx) more.push(`${nx} ${ups[0].transformer ? 'more ' : ''}${nx === 1 ? 'transformer' : 'transformers'}`)
  if (nl) more.push(`${nl} ${ups[0].transformer ? '' : 'more '}${nl === 1 ? 'line' : 'lines'}`)
  return `Raise ${name(ups[0])}${more.length ? ` and ${more.join(' and ')}` : ''}`
}

function FixResult({ fix, applied, over, solving, ran, running, plantCase, onRun, onApply, onRemove }) {
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
          {plantCase ? (
            // a plant outage is on the map (Plants tab): these upgrades are for the case with every plant running,
            // so running them here would compare a different case
            <p className="muted">
              Ratings only: no new lines. They are for the case with every plant running: bring the plant back in the Plants tab to run it
              again with them.
            </p>
          ) : (
            <>
              <p className="muted">Ratings only: no new lines. One click runs the same case again with them.</p>
              <div className="flip">
                <Button busy={running || solving} onClick={onRun}>
                  {ran ? 'Run it again with this fix' : 'Run the cascade with this fix'}
                </Button>
              </div>
            </>
          )}
          <div className="row">
            <button type="button" className="fix-link" onClick={onApply}>
              Apply the upgrades without running it
            </button>
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

// ------------------------------------------------------------------ (2) Best sites: a ranked screening table
// One row per town, facts from one solve with the campus there: connect voltage, room (headroom), the busiest
// line it loads up, and a plain 0-100 score whose formula the backend sends (shown under the table). A row
// click (or its town button, for the keyboard) places the campus there. When nothing fits, the roomiest towns
// with the smallest fix, and the Score column becomes the upgrades each needs.
function BestSites() {
  const { mw, loadFactor, place, setMode, focus, region } = useOverload()
  const best = useBestSites(mw, loadFactor, true, region)
  const hover = useHoverSite()
  const { list, fits } = shownSites(best.data)
  const level = Math.round(loadFactor * 100)
  const at = level !== 100 ? ` at ${level} % of normal demand` : ''
  // lines some state models already run over their limit with no campus at all (South Carolina, Mississippi;
  // at a heat wave more): the screen skips them, so say "no NEW line" and name them
  const pre = best.data?.strain_alone?.over || 0
  const preNote = pre ? ` (${fmt(pre)} ${pre === 1 ? 'line is' : 'lines are'} already over with no campus)` : ''

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
          {fits ? (
            <p className="muted fix-intro">
              {best.data.screened ? `${fmt(best.data.screened)} towns screened` : 'Towns screened'} with a full power-flow solve{at}; every listed site takes{' '}
              {fmt(mw)} MW with {pre ? 'no new line' : 'no line'} over its limit{preNote}. One substation per town, ranked by score.
            </p>
          ) : (
            <p className="fix-intro">
              No substation takes {fmt(mw)} MW before {pre ? 'another' : 'a'} line overloads{at}{preNote}. The most any one takes is <strong>{fmt(best.data.max_headroom_mw)} MW</strong>. Where
              the smallest upgrades would carry it:
            </p>
          )}
          <SitesTable list={list} fits={fits} mw={mw} hover={hover} onPick={putHere} />
          {!fits && list.some((s) => !s.fix_calm) && <p className="fix-how">* Still over after re-rating: it needs a new line.</p>}
          <SharedLimit shared={best.data.shared_limit} />
          {fits && best.data.score_formula && (
            <p className="fix-how">
              <strong>How the score works.</strong> {best.data.score_formula}
            </p>
          )}
          <StrainLine data={best.data} list={list} hover={hover} />
          <p className="fix-how">Click a row to put the campus there.{' '}
            {list.length > 1 && (
              <button type="button" className="fix-link" onClick={() => focus(list.map((s) => [s.lon, s.lat]))}>
                Show them on the map
              </button>
            )}
          </p>
        </>
      )}
    </section>
  )
}

function SitesTable({ list, fits, mw, hover, onPick }) {
  return (
    <table className={`fix-table${fits ? '' : ' fix-table--closest'}`}>
      <caption className="fix-sr">
        {fits ? `Ranked sites for a ${fmt(mw)} MW campus` : `The roomiest towns and the upgrades a ${fmt(mw)} MW campus needs there`}
      </caption>
      <thead>
        <tr>
          <th scope="col" className="fix-c-rank">
            <span className="fix-sr">Rank</span>
            <span aria-hidden="true">#</span>
          </th>
          <th scope="col" className="fix-c-town">
            Town
          </th>
          <th scope="col">kV</th>
          <th scope="col" title="Headroom: the MW this substation takes before any line goes over its limit">
            Room MW
          </th>
          <th scope="col" title="The most loaded line the campus adds flow to, as a share of its rating">
            Busiest line
          </th>
          <th scope="col">{fits ? 'Score' : 'Fix MVA'}</th>
        </tr>
      </thead>
      <tbody>
        {list.map((s) => {
          const hot = s.busiest_pct >= 90
          return (
            <tr
              key={s.sub}
              className={`fix-row${hover === s.sub ? ' fix-row--on' : ''}`}
              onClick={() => onPick(s)}
              onMouseEnter={() => setHoverSite(s.sub)}
              onMouseLeave={() => setHoverSite(null)}
              title={`${prettyName(s.name)} · ${fmt(s.kv)} kV${s.limiting ? ` · limited by ${s.limiting.label}` : ''}`}
            >
              <td className="fix-c-rank">{s.rank}</td>
              <th scope="row" className="fix-c-town">
                <button
                  type="button"
                  className="fix-row__btn"
                  onClick={(e) => {
                    e.stopPropagation()
                    onPick(s)
                  }}
                  onFocus={() => setHoverSite(s.sub)}
                  onBlur={() => setHoverSite(null)}
                  aria-label={`Put the ${fmt(mw)} MW campus at ${s.town}`}
                >
                  {s.town}
                </button>
              </th>
              <td>{fmt(s.kv)}</td>
              <td>
                {s.headroom_at_least ? '≥ ' : ''}
                {fmt(Math.round(s.headroom_mw))}
              </td>
              <td className={hot ? 'fix-c-hot' : ''}>{s.busiest_pct != null ? `${Math.round(s.busiest_pct)} %` : '–'}</td>
              <td className="fix-c-score">
                {fits ? (
                  <>
                    <span>{s.score ?? '–'}</span>
                    {s.score != null && (
                      <span className="fix-bar" aria-hidden="true">
                        <span style={{ transform: `scaleX(${s.score / 100})` }} />
                      </span>
                    )}
                  </>
                ) : (
                  <span className="fix-c-up" title={s.fix_calm ? undefined : 'Still over after the upgrades: it needs a new line'}>
                    +{fmt(s.fix_mva)}
                    {s.fix_calm ? '' : '*'}
                  </span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// The grid's strain with the campus at the hovered (else the first) site, against the grid alone.
function StrainLine({ data, list, hover }) {
  const s = list.find((x) => x.sub === hover) || list[0]
  const a = s?.strain_after
  const z = data.strain_alone
  if (!a || !z) return null
  const hotPct = data.hot_pct ?? 90
  return (
    <p className="fix-strain" aria-live="polite">
      <span className="fix-strain__k">Strain with the campus at {s.town}</span>
      {s.limiting && (
        <span>
          Limiting element: {s.limiting.label} at <strong>{Math.round(s.busiest_pct)} %</strong>.
        </span>
      )}
      <span>
        Busiest line on the grid <strong>{Math.round(a.peak_pct)} %</strong> of its rating, <strong>{fmt(a.hot)}</strong> {a.hot === 1 ? 'line' : 'lines'} at{' '}
        {hotPct} %+{a.over ? `, ${fmt(a.over)} over` : ''} (grid alone: {Math.round(z.peak_pct)} %, {fmt(z.hot)} hot{z.over ? `, ${fmt(z.over)} over` : ''}).
      </span>
    </p>
  )
}

// Why many rows look alike: the same line or transformer limits them all.
function SharedLimit({ shared }) {
  if (!shared || shared.count < 3) return null
  return (
    <p className="fix-how">
      {shared.count === shared.of ? `All ${shared.of}` : `${shared.count} of these ${shared.of}`} sites are limited by the same element, {shared.label}, which is why
      they rank alike.
    </p>
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
