// The capacity view's card: one campus of the plan (picked in the plan, the meter or on the map). Where it goes,
// every upgrade it takes (rating before and after, cost range and how it is priced), what stopped it, what was
// checked, and two buttons that hand the case to Watch it fail: these campuses WITH their upgrades (run the
// cascade: nothing trips) and the same campuses WITHOUT them (run it: it trips). Watch it fail takes 12 data
// centers at once (store.jsx MAX_POINTS), so a bigger plan tries its first 12.
// When it could connect (time to power, backend/leadtimes.py `tt`): the years, the slowest thing it waits for, the
// flexible plan's time when that is sooner, and what the sources say, with their links (typical, varies by utility).
// Opening it moves focus into it (Tab reaches the Try buttons first; Escape or × closes it and gives focus back
// to what opened it); on a phone, where the card sits under the map, it also scrolls into view.
import { useCallback, useEffect, useRef } from 'react'
import { fmt } from '../../geo'
import { MAX_POINTS, useOverload } from '../../store'
import { Button } from '../../ui'
import AiBadge from '../ai/AiBadge'
import { moneyRange } from '../cost/money'
import { shortMoney } from './budget'
import { count, firstRaised, ordinal, raisedAgain, shortName, sizeLabel, upgradesUpTo } from './capacity'
import { spanWords, ttpPlan, yearsLong } from './timeToPower'
import { reducedMotion, select } from './unlockStore'

const PARTS = 4

// the demo's case: the first campus as the main site, the rest as more data centers, with or without upgrades
function tryCampuses(o, m, n, withUpgrades, mw) {
  const k = Math.min(n, MAX_POINTS)
  const steps = m.steps.slice(0, k)
  const [main, ...more] = steps
  o.setFirm(false)
  o.setTrip([])
  o.setUpgrades(withUpgrades ? upgradesUpTo(m, k) : {})
  o.setExtraSites(more.map((st) => ({ id: `cap${st.site.id}`, metro: st.site.area, sub: st.site.id, lat: st.site.lat, lon: st.site.lon, mw })))
  o.setMw(mw)
  o.setMode('campus')
  o.place(main.site.lat, main.site.lon)
}

export default function CapacityCard({ r, m, n, flex, target, onBudget, tt = null }) {
  const o = useOverload()
  const ref = useRef(null)
  const opener = useRef(null)
  const st = m?.steps?.[n - 1]
  const close = useCallback(() => {
    select(null)
    const back = opener.current
    opener.current = null
    if (back?.isConnected) back.focus()
  }, [])

  // a campus opens: focus moves in (without scrolling the page); on a phone the card scrolls into view
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const from = document.activeElement
    if (from && from !== document.body && !el.contains(from)) opener.current = from
    el.focus({ preventScroll: true })
    let narrow = false
    try {
      narrow = window.matchMedia('(max-width: 860px)').matches
    } catch {
      narrow = false
    }
    if (narrow) el.scrollIntoView({ block: 'start', behavior: reducedMotion() ? 'auto' : 'smooth' })
  }, [n])
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !e.defaultPrevented) close()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [close])

  if (!st) return null
  const today = m.today
  const upTo = m.steps.slice(0, n)
  const own = st.projects
  const k = Math.min(n, MAX_POINTS)
  const paidBefore = upTo.some((s) => !s.free)
  const size = sizeLabel(r.mw)
  const share = r.capacity.flexible.share
  const mw = flex ? r.mw * share : r.mw // a flexible campus draws half at the afternoon peak: that is what the demo runs
  const inBudget = n <= target
  const v = m.verified
  const b = st.blocked_by
  const most = b && b.of > 0 && b.blocks * 2 >= b.of
  const first = firstRaised(m)

  return (
    <aside className="st-card cc" ref={ref} tabIndex={-1} aria-labelledby="cc-title">
      <p className="st-card__kicker">
        Campus {n} of {fmt(m.steps.length)} <AiBadge by="engine" />
      </p>
      <h3 className="st-card__h" id="cc-title">
        <span className="st-sr">Campus {n}: </span>
        {st.site.area}
      </h3>
      <p className="cc-sum">
        {n <= today
          ? `${n === 1 ? 'The first' : `With ${n - 1} before it, the ${ordinal(n)}`} ${flex ? 'flexible ' : ''}${size} campus fits today with no upgrade.`
          : `${fmt(n)} ${flex ? 'flexible ' : ''}campuses of ${size} connected at once, for ${moneyRange(st.cum_cost.low, st.cum_cost.high)} of upgrades in all.`}
      </p>

      <div className="cc-try">
        <Button onClick={() => tryCampuses(o, m, n, true, mw)}>
          Try {k === 1 ? 'this campus' : `these ${fmt(k)} campuses`} on Watch it fail
        </Button>
        {paidBefore && (
          <Button variant="secondary" onClick={() => tryCampuses(o, m, n, false, mw)}>
            …without the upgrades
          </Button>
        )}
        <p className="st-card__hint">
          {n > MAX_POINTS ? `Watch it fail takes ${MAX_POINTS} data centers at once: this tries the first ${MAX_POINTS}${paidBefore ? ' with their upgrades' : ''}. ` : ''}
          Then press Run the cascade{paidBefore ? ': with the upgrades nothing trips; without them, lines do.' : '.'}
          {flex ? ` Flexible campuses run at half size at the peak, so it tests them at ${fmt(mw)} MW each.` : ''}
        </p>
      </div>

      {!inBudget && (
        <p className="st-card__budget">
          Past your budget.{' '}
          <button type="button" className="st-link" onClick={() => onBudget(st.cum_cost.high)}>
            Raise it to {shortMoney(st.cum_cost.high)}
          </button>
        </p>
      )}

      {st.free ? (
        n > today && <p className="cc-note">This one needs nothing new: it fits with the upgrades made for the campuses before it.</p>
      ) : (
        <div className="cc-block">
          <p className="cc-k">
            What it takes <span className="cc-k__v">{moneyRange(st.cost.low, st.cost.high)}</span>
          </p>
          <ul className="st-card__parts">
            {own.slice(0, PARTS).map((p) => (
              <li key={p.id}>
                <span className="st-card__part">
                  {shortName(p)}
                  {raisedAgain(m, p, n) && <span className="cc-again"> raised again</span>}
                </span>
                <span className="st-card__meta">
                  {fmt(p.kv)} kV {p.kind}, {fmt(p.rating_before_mva)} → <strong>{fmt(p.rating_after_mva)}</strong> MVA, {moneyRange(p.cost.low, p.cost.high)}
                </span>
                {p.rate_est && <span className="cc-est">Rating {p.rate_note || 'estimated'}</span>}
                {p.cost.method && <span className="cc-how">{p.cost.method.charAt(0).toUpperCase() + p.cost.method.slice(1)}</span>}
                {raisedAgain(m, p, n) && (
                  <span className="cc-how">
                    First raised for campus {first.get(p.branch_id)}, from {fmt(p.rating_original_mva ?? p.rating_before_mva)} MVA; the whole plan takes it to{' '}
                    {fmt(lastRating(m, p.branch_id))} MVA in {count(raises(m, p.branch_id))} steps. Each step is priced from the rating before it, so together they cost the
                    same as building it once.
                  </span>
                )}
              </li>
            ))}
            {own.length > PARTS && <li className="st-card__meta">and {fmt(own.length - PARTS)} more</li>}
          </ul>
        </div>
      )}

      <TimeToPower tt={tt} m={m} n={n} flex={flex} />

      <dl className="st-card__facts cc-facts">
        {b && (
          <div>
            <dt>What stopped it</dt>
            <dd>
              The {shortName(b)}, {fmt(b.kv)} kV, reached its rating first at {b.blocks === b.of ? `all ${fmt(b.of)}` : `${fmt(b.blocks)} of the ${fmt(b.of)}`} sites closest
              to fitting{most ? '.' : '; at the others, other lines and transformers did.'}
              {b.rate_est && <span className="cc-est">Its rating is {b.rate_note || 'estimated'}: a different rating moves this limit.</span>}
            </dd>
          </div>
        )}
        {st.busiest_pct != null && (
          <div>
            <dt>Strain</dt>
            <dd>
              With {n === 1 ? 'it' : n === 2 ? 'both' : `all ${count(n)}`} connected, the busiest line or transformer runs at {st.busiest_pct.toLocaleString('en-US', { maximumFractionDigits: 1 })} % of its rating: none over.
            </dd>
          </div>
        )}
        <div>
          <dt>Checked</dt>
          <dd>
            N-0 (every line in service): no line or transformer over its rating with every campus connected
            {v?.calm ? `; the whole set of ${fmt(v.campuses)} with its upgrades also ran through the cascade engine and nothing trips` : ''}.
            {flex ? ' Flexible campuses are checked twice: at full size at 90 % of this load, and at half size at it.' : ''}{' '}
            <N1Line n1={r.capacity?.n1} flex={flex} n={n} />
          </dd>
        </div>
      </dl>

      <p className="cc-basis">{r.cost_basis}</p>
      <p className="cc-basis">
        Campuses are numbered in the order the engine connects them; {count(st.upgrades_total)} {st.upgrades_total === 1 ? 'line or transformer' : 'lines and transformers'} raised up
        to this one.
      </p>
      {/* last in the tab order (it sits in the corner): Tab from the card reaches the Try buttons first */}
      <button type="button" className="st-card__x" onClick={close} aria-label="Close">
        ×
      </button>
    </aside>
  )
}

// "When it could connect": campus n of the plan on screen (always on or flexible), from the time-to-power ranges.
function TimeToPower({ tt, m, n, flex }) {
  const plan = ttpPlan(tt, flex ? 'flexible' : 'firm', m.steps.length)
  const c = plan?.[n - 1]
  const it = c && tt.items[c.item]
  if (!it) return null
  const years = c.from_year === c.to_year ? `~${c.from_year}${c.plus ? '+' : ''}` : `${c.from_year}–${c.to_year}${c.plus ? '+' : ''}`
  const rest = c.waits_for.filter((k) => k !== c.item && k !== 'connect')
  // a wait with the very same range (a line doubled and new plants, both 3–5+ years) is "just as long", not shorter
  const same = (k) => tt.items[k].lo === it.lo && tt.items[k].hi === it.hi && !!tt.items[k].plus === !!it.plus
  const tied = rest.filter(same).map((k) => tt.items[k].label)
  const others = rest.filter((k) => !same(k)).map((k) => `${tt.items[k].label} (${spanWords(tt.items[k])})`)
  // what sets it: the first upgrade of that kind in the plan (and the campus it was made for), or the plants' line
  const lim = c.limit
  const whose = lim ? (lim.n === n ? 'this campus' : `campus ${lim.n}`) : ''
  const setBy = !lim
    ? ''
    : c.item === 'generation'
      ? lim.n > 1
        ? `: past the ${ordinal(lim.n - 1)} campus the power plants’ reserve runs out`
        : ': the power plants have no room for a campus of this size'
      : `: the ${lim.name}, raised${RAISED[c.item] || ''} for ${whose}`
  const why =
    c.item === 'connect'
      ? `No network upgrades to wait for: the pace is set by ${it.label} (${spanWords(it)}).`
      : `It waits longest for ${it.label} (${spanWords(it)})${setBy}.${tied.length ? ` It waits just as long for ${listWords(tied)}.` : ''}${others.length ? ` It also waits for ${listWords(others)}.` : ''}`
  // the other campus type's time for the same count, when flexible is the sooner one
  const firm = tt.firm?.campuses?.[n - 1]
  let alt = null
  if (!flex && c.flex_sooner && c.flex) {
    const fi = c.flex.item
    alt = `As flexible campuses, the ${ordinal(n)} could connect ${yearsLong(c.flex)}: sooner, ${fi === 'connect' ? 'with no network upgrades to wait for' : `since its longest wait is ${tt.items[fi].label}`}.`
  } else if (flex && firm?.flex_sooner) {
    alt = `As always-on campuses, the ${ordinal(n)} would connect ${yearsLong(firm)}: flexible is sooner.`
  }
  const ids = [...it.sources, ...(alt ? tt.flex_sources || [] : [])].filter((id, i, all) => all.indexOf(id) === i && tt.sources[id])
  return (
    <div className="cc-block cc-ttp">
      <p className="cc-k">
        When it could connect <span className="cc-k__v">{years}</span>
      </p>
      <p className="cc-ttp__why">
        {yearsLong(c).replace(/^about/, 'About')} if work started now. {why}
      </p>
      {alt && <p className="cc-ttp__alt">{alt}</p>}
      <p className="cc-how">
        {it.basis} Typical ranges that vary by utility, region and project: not a schedule for this or any real project.{' '}
        {ids.map((id, i) => (
          <span key={id}>
            {i > 0 && ' · '}
            <a href={tt.sources[id].url} target="_blank" rel="noreferrer" title={tt.sources[id].name}>
              {tt.sources[id].short || tt.sources[id].name}
            </a>
          </span>
        ))}
      </p>
    </div>
  )
}

// how far the line that sets the time was raised (backend/leadtimes.py _line_kind)
const RAISED = { line_doubled: ' past twice its rating', new_line: ' past four times its rating' }

// "a, b and c"
const listWords = (xs) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs.at(-1)}`)

// The single-outage screen of the always-on plan (capacity.n1), landing after the plan: the headline's campuses (the
// count the meter sells) and, when the search went further, its whole set. Its counts are single OUTAGES, not overloads
// (one outage can push several lines past their rating); a plant cut off that the others can't make up counts apart.
// `n` (a campus card): the whole set's clause only for a campus past the headline's count; without it, always.
export function N1Line({ n1, flex = false, n = null }) {
  if (!n1 || n1.status === 'skipped') return null
  const of = `${flex ? ' of the always-on plan' : ''}, with the headline’s ${n1.campuses ? `${fmt(n1.campuses)} ${n1.campuses === 1 ? 'campus' : 'campuses'}` : 'campuses'}`
  if (n1.status === 'pending') return <span className="cc-n1">Single-outage screen (N-1){of}: running…</span>
  if (n1.status !== 'done') return <span className="cc-n1">The single-outage screen (N-1) didn’t run for this study.</span>
  const w = n1.worst
  const f = n1.full && (n == null || flex || n > n1.campuses) ? n1.full : null // (a flexible campus's n counts another plan)
  const pct = fmt(n1.emergency_pct)
  const k = n1.new_overloads ?? n1.new
  return (
    <span className="cc-n1">
      Single-outage screen (N-1){of} and their upgrades: {fmt(k)} single {k === 1 ? 'outage pushes' : 'outages push'} a line or transformer past {pct} % of its rating where the
      model alone doesn’t, or {k === 1 ? 'pushes' : 'push'} it further (the model alone has {fmt(n1.baseline_violations)} such outages){shortWords(n1, '; and ')}
      {w ? `. The worst: losing the ${w.outage.short} puts the ${w.overloaded.short} at ${fmt(Math.round(w.pct))} % of its rating` : ''}.
      {f
        ? ` With all ${fmt(f.campuses)} campuses of the search: ${fmt(f.new_overloads ?? f.new)} such ${(f.new_overloads ?? f.new) === 1 ? 'outage' : 'outages'}${shortWords(f, ', and ')}.`
        : ''}
    </span>
  )
}

// the outages where a plant is cut off and the other plants can't make up its output (short of generation)
function shortWords(v, lead) {
  if (!v?.short) return ''
  const cut = v.short_shed ? ` (${fmt(v.short_shed)} of them cut customers, up to ${fmt(v.short_shed_max_mw)} MW)` : ' (the state would have to stop exporting)'
  return `${lead}${fmt(v.short)} more where the other plants can’t make up a plant the outage cuts off${cut}`
}

// the rating a line or transformer ends at in the whole plan (its last raise), and how many times it is raised
function lastRating(m, id) {
  let out = 0
  for (const st of m.steps) for (const p of st.projects) if (p.branch_id === id) out = p.rating_after_mva
  return out
}
const raises = (m, id) => m.steps.reduce((a, st) => a + st.projects.filter((p) => p.branch_id === id).length, 0)
