// The Strengthen page's inspector: what is selected (a plan package from the table, a Gemini bundle, a weak point
// or a site on the map), as its project record: where it is, the rating before and after, the cost range, what it
// unlocks, the blackout it prevents, who found it. "Try it" places the campus at an unlocked site on the demo page
// with only the upgrades that site needs (the engine then re-runs it there).
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button } from '../../ui'
import AiBadge from '../ai/AiBadge'
import { money, moneyRange } from '../cost/money'
import { leadOf, shortMoney, whereOf } from './budget'
import { compact, select, showBundle, stepFixing, unlockedUpTo, upgradesFor } from './unlockStore'

const PARTS = 5
const TRY = 3
const plain = (p) => p.short || p.label?.replace(/^the /, '') || `#${p.branch_id}`

function tryIt(o, r, site, upgrades) {
  o.setTrip([])
  o.setExtraSites([])
  o.setUpgrades(upgrades)
  o.setMw(r.mw)
  o.setMode('campus')
  o.place(site.lat, site.lon)
}

function Parts({ projects }) {
  const shown = projects.slice(0, PARTS)
  return (
    <ul className="st-card__parts">
      {shown.map((p) => (
        <li key={p.id}>
          <span className="st-card__part">{plain(p)}</span>
          <span className="st-card__meta">
            {fmt(p.kv)} kV {p.kind} · {fmt(p.rating_before_mva)} → <strong>{fmt(p.rating_after_mva)}</strong> MVA
            {p.rate_est && <span className="cp-est"> (rating estimated)</span>} ·{' '}
            {p.cost.high > 0
              ? moneyRange(p.cost.low, p.cost.high)
              : p.rating_before_mva > p.rating_original_mva + 0.5
                ? 'no added cost: an earlier step rebuilt it'
                : 'no added cost'}
            {p.weak_point ? ` · weak point ${p.weak_point}` : ''}
          </span>
        </li>
      ))}
      {projects.length > PARTS && <li className="st-card__meta">and {fmt(projects.length - PARTS)} more in this package</li>}
    </ul>
  )
}

function TryButtons({ r, sites, upgradesOf }) {
  const o = useOverload()
  if (!sites.length) return null
  return (
    <div className="st-card__try">
      {sites.slice(0, TRY).map((s) => (
        <Button key={s.id} variant="secondary" onClick={() => tryIt(o, r, s, upgradesOf(s))}>
          Try it at {s.area}
        </Button>
      ))}
      <span className="st-card__hint">Opens Watch it fail with the campus there and only the upgrades that site needs.</span>
    </div>
  )
}

export default function UpgradeCard({ r, selected, bundle, target, budget, onBudget, onPickStep, className = '' }) {
  if (!r) return null
  const close = () => {
    select(null)
    if (bundle != null) showBundle(bundle)
  }
  let body = null
  if (bundle != null && r.ai?.bundles?.[bundle]) {
    const b = r.ai.bundles[bundle]
    const same = b.engine_same_cost || {}
    body = (
      <>
        <p className="st-card__kicker">
          Gemini bundle <AiBadge by="gemini" verified />
        </p>
        <h3 className="st-card__h">{b.name}</h3>
        {b.why && <p className="st-card__why">Gemini&apos;s reason: {b.why}</p>}
        <Parts projects={b.projects} />
        <dl className="st-card__facts">
          <div>
            <dt>Cost</dt>
            <dd>{moneyRange(b.cost.low, b.cost.high)}</dd>
          </div>
          <div>
            <dt>Unlocks</dt>
            <dd>
              +{fmt(b.more_sites)} {b.more_sites === 1 ? 'site' : 'sites'} ({b.verified === b.more_sites ? 'every one re-run by the engine' : `${fmt(b.verified)} re-run`})
            </dd>
          </div>
          <div>
            <dt>The plan, same money</dt>
            <dd>
              +{fmt(same.more_sites || 0)} sites for {money(same.cost_high || 0)}: {b.beats_engine ? 'Gemini gets more' : 'the plan stays ahead'}
            </dd>
          </div>
        </dl>
        <TryButtons r={r} sites={b.newly || []} upgradesOf={(s) => Object.fromEntries(Object.entries(b.apply).filter(([id]) => (s.needs || []).includes(Number(id))))} />
      </>
    )
  } else if (selected?.type === 'step') {
    const st = r.steps[selected.id - 1]
    if (st) {
      const lead = leadOf(st.projects)
      const biggest = st.newly.find((s) => s.hit0 > 0)
      const inBudget = st.n <= target
      body = (
        <>
          <p className="st-card__kicker">
            Step {st.n} of {fmt(r.steps.length)} <AiBadge by="engine" />
          </p>
          <h3 className="st-card__h">
            {plain(lead)}
            {st.projects.length > 1 && <span className="st-card__more"> and {fmt(st.projects.length - 1)} more</span>}
          </h3>
          <p className="st-card__where">{whereOf(st.projects)}</p>
          <Parts projects={st.projects} />
          <dl className="st-card__facts">
            <div>
              <dt>Cost</dt>
              <dd>
                {moneyRange(st.cost.low, st.cost.high)} <span className="st-card__meta">({shortMoney(st.cum_cost.high)} with every step before it)</span>
              </dd>
            </div>
            <div>
              <dt>Unlocks</dt>
              <dd>
                +{fmt(st.newly_count)} {st.newly_count === 1 ? 'site' : 'sites'} for a {fmt(r.mw)} MW campus:{' '}
                {st.newly
                  .map((s) => s.area)
                  .slice(0, 6)
                  .join(', ')}
                {st.newly.length > 6 ? ` +${st.newly.length - 6}` : ''}
              </dd>
            </div>
            {biggest && (
              <div>
                <dt>Prevents</dt>
                <dd>
                  the blackout a campus at {biggest.area} would set off today: ~{compact(biggest.hit0)} people hit (estimate)
                </dd>
              </div>
            )}
            <div>
              <dt>Strain</dt>
              <dd>
                line overloads across the {fmt(r.sites_total)} tested sites {fmt(st.overloads_before)} → <strong className="st-good">{fmt(st.overloads_left)}</strong>
              </dd>
            </div>
            <div>
              <dt>Checked</dt>
              <dd>
                {st.verified === st.newly_count
                  ? 'every site it unlocks re-run through the cascade engine with every line in service (N-0): nothing trips, no one loses power'
                  : `${fmt(st.verified)} of ${fmt(st.newly_count)} sites re-run through the cascade engine (N-0) so far`}
              </dd>
            </div>
          </dl>
          {!inBudget && (
            <p className="st-card__budget">
              Past your {shortMoney(budget)} budget.{' '}
              <button type="button" className="st-link" onClick={() => onBudget(st.cum_cost.high)}>
                Raise it to {shortMoney(st.cum_cost.high)}
              </button>
            </p>
          )}
          <TryButtons r={r} sites={st.newly} upgradesOf={(s) => upgradesFor(r, s, st.n)} />
        </>
      )
    }
  } else if (selected?.type === 'point') {
    const p = r.points.find((x) => x.branch_id === selected.id)
    if (p) {
      const fix = stepFixing(r, p.branch_id)
      const up = fix?.projects.find((x) => x.branch_id === p.branch_id)
      body = (
        <>
          <p className="st-card__kicker">
            Weak point {p.rank} of {fmt(r.points.length)} <AiBadge by="engine" />
          </p>
          <h3 className="st-card__h">{plain(p)}</h3>
          <p className="st-card__where">
            {p.where} · {fmt(p.kv)} kV {p.kind} · rated {fmt(p.rating_mva)} MVA
            {p.rate_est && <span className="cp-est"> (rating estimated)</span>}
          </p>
          <p className="st-card__why">{p.reason}</p>
          {fix ? (
            <p className="st-card__fix">
              The plan raises it at step {fix.n}
              {up ? ` to ${fmt(up.rating_after_mva)} MVA (${moneyRange(up.cost.low, up.cost.high)})` : ''}.{' '}
              <button type="button" className="st-link" onClick={() => onPickStep(fix)}>
                Show step {fix.n}
              </button>
            </p>
          ) : (
            <p className="st-card__fix">No step of the plan raises it: the sites it blocks need other lines, new lines or generation first.</p>
          )}
        </>
      )
    }
  } else if (selected?.type === 'site') {
    const s = r.sites.find((x) => x.id === selected.id)
    if (s) {
      const un = unlockedUpTo(r, r.steps.length).find((x) => x.id === s.id)
      body = (
        <>
          <p className="st-card__kicker">
            Site <AiBadge by="engine" />
          </p>
          <h3 className="st-card__h">{s.area}</h3>
          <p className="st-card__why">
            {s.ok0
              ? `Takes a ${fmt(r.mw)} MW campus today with no line over its limit.`
              : s.short
                ? `The model's generators can't supply a ${fmt(r.mw)} MW campus here: it needs new generation, not line upgrades.`
                : `Today a ${fmt(r.mw)} MW campus here overloads ${fmt(s.over0)} ${s.over0 === 1 ? 'line' : 'lines'}${s.hit0 ? ` and would hit ~${compact(s.hit0)} people (estimate)` : ''}.`}
          </p>
          {un ? (
            <p className="st-card__fix">
              Step {un.step} of the plan lets it connect with no line over its limit.{' '}
              <button type="button" className="st-link" onClick={() => onPickStep(r.steps[un.step - 1])}>
                Show step {un.step}
              </button>
            </p>
          ) : (
            !s.ok0 && (
              <p className="st-card__fix">
                The plan doesn&apos;t clear it{s.hit_after != null ? `; with the whole plan a campus here would hit ~${compact(s.hit_after)} people (estimate)` : ''}.
              </p>
            )
          )}
          {un && <TryButtons r={r} sites={[un]} upgradesOf={(x) => upgradesFor(r, x, un.step)} />}
        </>
      )
    }
  }
  if (!body) return null
  return (
    <aside className={`st-card ${className}`} aria-label="Selected" aria-live="polite">
      <button type="button" className="st-card__x" onClick={close} aria-label="Close">
        ×
      </button>
      {body}
    </aside>
  )
}
