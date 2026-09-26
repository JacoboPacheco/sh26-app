// FEATURE: Strengthen the grid (owned by the unlock track): the left panel while mode === 'unlock'.
//
// The pitch in one panel: the app LEARNS where the grid's structural weak points are by simulating a
// campus at every town (the full cascade engine), TESTS the cheapest upgrades that fix them (a greedy
// search priced with published figures), lets GEMINI propose other bundles that THE ENGINE VERIFIES,
// and SHOWS WHAT IT UNLOCKS: more sites that can host the campus, fewer and smaller blackouts, at what
// cost. Nothing runs until the button (LAZY); the map layer (UnlockLayer) draws the same study.
// Everything is an estimate on a SYNTHETIC grid model; costs carry their sources.
import { useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Badge, Button, ErrorBanner } from '../../ui'
import { money, moneyRange } from '../cost/money'
import UnlockChart from './UnlockChart'
import { SIZES } from './unlockApi'
import { compact, runStudy, select, setShown, setSize, showBundle, startPlay, stopPlay, unlockedUpTo, upgradesFor, useUnlock } from './unlockStore'
import './unlock.css'

const PHASES = [
  ['learn', 'Learn the weak points', 'a campus at every town, the full cascade at each'],
  ['plan', 'Test the cheapest fixes', 'upgrades priced from published figures, cheapest per site first'],
  ['verify', 'Verify with the engine', 'every unlocked site re-run through the cascade'],
  ['ai', 'AI proposes, the engine checks', 'Gemini suggests other bundles; only verified ones count'],
]
const STEP_ROWS = 8
const PREVENTED = 3

export default function UnlockPanel() {
  const o = useOverload()
  const u = useUnlock()
  const { region, loadFactor, grid } = o
  const national = region === 'US'
  const where = grid?.meta?.region_name || 'this state'
  const busy = u.status === 'starting' || u.status === 'queued' || u.status === 'running'
  const mine = u.region === region
  const result = mine && u.status === 'done' ? u.result : null
  const stale = result && (result.mw !== u.size || Math.abs(result.load_factor - loadFactor) > 0.001)
  const level = Math.round(loadFactor * 100)

  return (
    <div className="stack panel-body ul">
      <header className="ul-head">
        <h3 className="panel-h">Strengthen the grid</h3>
        <p className="ul-lede">
          Find the grid&apos;s weak points by simulation, test the cheapest fixes, and see how many more data centers they let connect. The physics engine
          re-runs every fix, including the AI&apos;s.
        </p>
      </header>

      <div className="ul-size" role="radiogroup" aria-labelledby="ul-size-h">
        <span className="ul-size__h" id="ul-size-h">
          Make room for a campus of
        </span>
        <div className="ul-chips">
          {SIZES.map((s) => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={u.size === s}
              className={`ul-chip${u.size === s ? ' ul-chip--on' : ''}`}
              onClick={() => setSize(s)}
              disabled={busy}
            >
              {fmt(s)} MW
            </button>
          ))}
        </div>
      </div>

      {national ? (
        <p className="muted">Open a state on the map first: the weak points are found on one state&apos;s grid model.</p>
      ) : (
        <div className="ul-run">
          <Button onClick={() => runStudy({ region, loadFactor })} busy={busy} disabled={busy} variant={result && !stale ? 'secondary' : 'primary'}>
            {busy ? 'Learning…' : result && !stale ? 'Run it again' : `Find the weak points in ${where}`}
          </Button>
          {level !== 100 && <span className="ul-level">at {level} % of normal demand</span>}
        </div>
      )}

      {mine && u.status === 'error' && <ErrorBanner error={u.error} onRetry={() => runStudy({ region, loadFactor })} />}
      {mine && busy && <Progress u={u} />}
      {mine && busy && u.partial?.points?.length > 0 && <Points points={u.partial.points} simulated />}
      {result && stale && (
        <p className="ul-stale">
          Showing the study for {fmt(result.mw)} MW
          {Math.round(result.load_factor * 100) !== 100 ? ` at ${Math.round(result.load_factor * 100)} % of demand` : ''}. Run it again for the new setting.
        </p>
      )}
      {result && <Result r={result} u={u} />}
    </div>
  )
}

// ------------------------------------------------------------------ while it learns
function Progress({ u }) {
  const p = u.progress || {}
  const at = Math.max(
    0,
    PHASES.findIndex(([id]) => id === p.phase),
  )
  const frac = p.total ? Math.min(1, p.done / p.total) : 0
  return (
    <div className="ul-progress" aria-live="polite">
      <ol className="ul-phases">
        {PHASES.map(([id, label, sub], i) => {
          const st = p.phase === 'queued' ? 'todo' : i < at ? 'done' : i === at ? 'now' : 'todo'
          return (
            <li key={id} className={`ul-phase ul-phase--${st}`}>
              <span className="ul-phase__mark" aria-hidden="true" />
              <span className="ul-phase__text">
                <strong>{label}</strong>
                <span>{sub}</span>
                {st === 'now' && (
                  <span className="ul-bar" aria-hidden="true">
                    <span style={{ transform: `scaleX(${frac})` }} />
                  </span>
                )}
              </span>
            </li>
          )
        })}
      </ol>
      <p className="ul-msg">{p.message || 'Working…'}</p>
    </div>
  )
}

// ------------------------------------------------------------------ the answer
function Result({ r, u }) {
  const o = useOverload()
  const n = Math.min(u.shown, r.steps.length)
  if (r.already_failing)
    return (
      <p className="ul-warn" role="note">
        At this load level the model&apos;s grid fails with no campus at all (~
        {compact(r.baseline_people_hit)} people hit, estimate). Set the clock back to normal demand to find the weak points a campus exposes.
      </p>
    )
  const where = r.region_name || 'this state'
  if (!r.steps.length)
    return (
      <div className="stack">
        <Headline r={r} n={0} />
        {r.before.short_sites >= r.sites_total ? (
          <p className="ul-warn" role="note">
            The model&apos;s generators in {where} can&apos;t supply a {fmt(r.mw)} MW campus anywhere: load is cut however strong the lines are. It needs new
            generation or imports, not line upgrades. Try a smaller size.
          </p>
        ) : (
          <p className="muted">
            No re-rating within five times a line&apos;s rating lets another site hold {fmt(r.mw)} MW: the blocked sites need new lines or generation, not
            upgrades.
          </p>
        )}
        <Points points={r.points} />
        <Learned r={r} />
      </div>
    )
  const flyTo = (projects) => {
    const pts = projects.flatMap((p) => [
      [p.from.lon, p.from.lat],
      [p.to.lon, p.to.lat],
    ])
    if (pts.length) o.focus(pts)
  }
  return (
    <div className="stack ul-result">
      <Headline r={r} n={n} />
      <div className="ul-controls">
        {u.playing ? (
          <Button variant="secondary" onClick={stopPlay}>
            Pause
          </Button>
        ) : (
          <Button variant="secondary" onClick={() => startPlay(n >= r.steps.length)}>
            {n >= r.steps.length ? 'Build it up again' : n ? 'Keep building' : 'Build it up'}
          </Button>
        )}
        <label className="ul-slider">
          <span>
            Steps shown: <strong>{n}</strong> of {r.steps.length}
          </span>
          <input type="range" min={0} max={r.steps.length} value={n} onChange={(e) => setShown(Number(e.target.value))} />
        </label>
      </div>
      <UnlockChart result={r} shown={n} bundle={u.bundle} onPick={setShown} onBundle={showBundle} />
      <Compare r={r} />
      <Prevented r={r} n={n} />
      <Ai r={r} u={u} flyTo={flyTo} />
      <Steps r={r} n={n} flyTo={flyTo} />
      <Points points={r.points} />
      <Learned r={r} />
      <Honest r={r} />
    </div>
  )
}

function Headline({ r, n }) {
  const st = n ? r.steps[n - 1] : null
  const more = st ? st.sites_ok - r.before.sites_ok : 0
  const all = n === r.steps.length && n > 0
  return (
    <section className="ul-hl" aria-live="polite">
      <div className="ul-hl__fig">
        <span className="ul-hl__n">{st ? `+${fmt(more)}` : fmt(r.before.sites_ok)}</span>
        <span className="ul-hl__cap">
          {st ? `more sites can host a ${fmt(r.mw)} MW campus` : `of ${fmt(r.sites_total)} sites can host a ${fmt(r.mw)} MW campus today`}
        </span>
      </div>
      {st ? (
        <p className="ul-hl__line">
          <strong>{fmt(st.cum_upgrades)}</strong> {st.cum_upgrades === 1 ? 'upgrade' : 'upgrades'}, <strong>{money(st.cum_cost.high)}</strong> (high end), let{' '}
          <strong>{fmt(more)}</strong> more {more === 1 ? 'site' : 'sites'} host {fmt(r.mw)} MW ({((more * r.mw) / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })} GW in all, each site tested on its
          own)
          {all ? '.' : `, ${n} of ${r.steps.length} steps.`}
        </p>
      ) : (
        <p className="ul-hl__line">
          {r.before.short_sites >= r.sites_total
            ? `A campus this size sheds load wherever it goes in this model; ${fmt(r.before.blackout_sites)} sites set off a blackout.`
            : `The rest overload a line; ${fmt(r.before.blackout_sites)} set off a blackout in the model.${r.steps.length ? ' Press Build it up to add the upgrades one at a time.' : ''}`}
        </p>
      )}
    </section>
  )
}

function Compare({ r }) {
  const b = r.before
  const a = r.after
  const rows = [
    ['Sites that can host it', fmt(b.sites_ok), fmt(a.sites_ok)],
    ['Sites that set off a blackout', fmt(b.blackout_sites), fmt(a.blackout_sites)],
    [
      'Biggest blackout, people hit (estimate)',
      b.worst.people_hit ? `${compact(b.worst.people_hit)} (${b.worst.area})` : 'none',
      a.worst.people_hit ? `${compact(a.worst.people_hit)} (${a.worst.area})` : 'none',
    ],
  ]
  return (
    <table className="ul-compare">
      <caption>
        Today and with the whole plan ({money(r.headline.cost_high)}, {fmt(r.headline.upgrades)} upgrades)
      </caption>
      <thead>
        <tr>
          <th scope="col">
            <span className="ul-sr">Measure</span>
          </th>
          <th scope="col">Today</th>
          <th scope="col">With the plan</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, x, y]) => (
          <tr key={k}>
            <th scope="row">{k}</th>
            <td>{x}</td>
            <td className="ul-after">{y}</td>
          </tr>
        ))}
      </tbody>
      {r.before.short_sites > 0 && (
        <tfoot>
          <tr>
            <td colSpan={3} className="ul-foot">
              {fmt(r.before.short_sites)} {r.before.short_sites === 1 ? 'site needs' : 'sites need'} more generation, not line upgrades: the model&apos;s
              generators can&apos;t supply a campus that size there.
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  )
}

function tryIt(o, r, site) {
  o.setTrip([])
  o.setExtraSites([])
  o.setUpgrades(upgradesFor(r, site, site.step))
  o.setMw(r.mw)
  o.setMode('campus')
  o.place(site.lat, site.lon)
}

function Prevented({ r, n }) {
  const o = useOverload()
  const u = useUnlock()
  const list = unlockedUpTo(r, n).filter((s) => s.hit0 > 0)
  const sel = u.selected?.type === 'site' ? r.sites.find((s) => s.id === u.selected.id) : null
  const selUnlocked = sel ? unlockedUpTo(r, r.steps.length).find((s) => s.id === sel.id) : null
  return (
    <section className="stack ul-prev" aria-labelledby="ul-prev-h">
      {sel && (
        <div className="ul-card" role="note" id="ul-sel-card">
          <p>
            <strong>{sel.area}</strong>
            {sel.ok0
              ? ` takes a ${fmt(r.mw)} MW campus today with no line over its limit.`
              : selUnlocked
                ? `: a ${fmt(r.mw)} MW campus there ${selUnlocked.hit0 ? `would hit ~${compact(selUnlocked.hit0)} people (estimate)` : 'overloads a line'}; step ${selUnlocked.step} of the plan lets it connect with no line over its limit.`
                : `: a ${fmt(r.mw)} MW campus there ${sel.hit0 ? `would hit ~${compact(sel.hit0)} people (estimate)` : 'overloads a line'}, and the plan doesn't fix it${sel.hit_after != null ? ` (with the plan: ~${compact(sel.hit_after)} people hit)` : ''}.`}
          </p>
          {selUnlocked && (
            <Button variant="secondary" onClick={() => tryIt(o, r, selUnlocked)}>
              Try it on the map
            </Button>
          )}
        </div>
      )}
      <h4 className="ul-h" id="ul-prev-h">
        Blackouts these upgrades prevent
      </h4>
      {list.length === 0 ? (
        <p className="muted">{n ? 'None of the sites unlocked so far set off a blackout.' : 'Add upgrades to see which blackouts they prevent.'}</p>
      ) : (
        <ul className="ul-prev__list">
          {list.slice(0, PREVENTED).map((s) => (
            <li key={s.id}>
              <span>
                <strong>{s.area}</strong>: a campus there would hit <strong>~{compact(s.hit0)}</strong> people (estimate). After step {s.step} it connects with
                no line over its limit.
              </span>
              <Button variant="secondary" onClick={() => tryIt(o, r, s)}>
                Try it
              </Button>
            </li>
          ))}
        </ul>
      )}
      {list.length > PREVENTED && <p className="ul-small">…and {fmt(list.length - PREVENTED)} more sites that would set off a blackout.</p>}
    </section>
  )
}

function Steps({ r, n, flyTo }) {
  const [all, setAll] = useState(false)
  const rows = all ? r.steps : r.steps.slice(0, STEP_ROWS)
  return (
    <section className="stack" aria-labelledby="ul-steps-h">
      <h4 className="ul-h" id="ul-steps-h">
        The plan, cheapest per site first
      </h4>
      <ol className="ul-steps">
        {rows.map((st) => {
          const lead = [...st.projects].sort((a, b) => (a.weak_point || 99) - (b.weak_point || 99) || b.cost.high - a.cost.high)[0]
          const wp = st.projects.filter((p) => p.weak_point).map((p) => p.weak_point)
          const towns = st.newly.map((s) => s.area)
          return (
            <li key={st.n} className={`ul-step${st.n <= n ? ' ul-step--on' : ''}`}>
              <button
                type="button"
                className="ul-step__btn"
                onClick={() => {
                  setShown(st.n)
                  select({ type: 'step', id: st.n })
                  flyTo(st.projects)
                }}
              >
                <span className="ul-step__n">{st.n}</span>
                <span className="ul-step__body">
                  <span className="ul-step__what">
                    {lead.name}
                    {st.projects.length > 1 && <span className="ul-step__more"> and {st.projects.length - 1} more</span>}
                  </span>
                  <span className="ul-step__meta">
                    {money(st.cost.high)} · +{st.newly_count} {st.newly_count === 1 ? 'site' : 'sites'}
                    {towns.length ? `: ${towns.slice(0, 3).join(', ')}${towns.length > 3 ? ` +${towns.length - 3}` : ''}` : ''}
                    {st.blackout_prevented_max ? ` · prevents a ~${compact(st.blackout_prevented_max)}-person blackout` : ''}
                  </span>
                  <span className="ul-tags">
                    <span className="ul-tag ul-tag--engine">engine</span>
                    {st.verified === st.newly_count ? (
                      <span className="ul-tag">verified</span>
                    ) : (
                      <span className="ul-tag">
                        {st.verified} of {st.newly_count} verified
                      </span>
                    )}
                    {wp.length > 0 && <span className="ul-tag ul-tag--wp">fixes weak point {wp.sort((a, b) => a - b).join(', ')}</span>}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
      {r.steps.length > STEP_ROWS && (
        <button type="button" className="ul-link" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer steps' : `Show all ${r.steps.length} steps`}
        </button>
      )}
    </section>
  )
}

function Ai({ r, u, flyTo }) {
  const ai = r.ai || {}
  const bundles = ai.bundles || []
  const status = {
    used: `Gemini proposed ${ai.asked} ${ai.asked === 1 ? 'bundle' : 'bundles'} from the weak points; the engine re-ran each one.`,
    none_verified: `Gemini proposed ${ai.asked} ${ai.asked === 1 ? 'bundle' : 'bundles'}; the engine re-ran each one and none let another site hold, so none is listed.`,
    not_configured: "Gemini isn't set up on this server, so the engine's plan stands alone.",
    offline: "Gemini didn't answer in time, so the engine's plan stands alone.",
    error: "Gemini's step failed, so the engine's plan stands alone.",
    skipped: 'There was nothing for Gemini to improve on.',
  }[ai.status]
  return (
    <section className="stack ul-ai" aria-labelledby="ul-ai-h">
      <h4 className="ul-h" id="ul-ai-h">
        AI proposes, the engine verifies
      </h4>
      <p className="ul-small">
        {status} {ai.status === 'offline' && <Badge tone="warn">AI offline</Badge>}
      </p>
      {bundles.map((b, i) => {
        const same = b.engine_same_cost
        return (
          <article key={b.name + i} className={`ul-bundle${u.bundle === i ? ' ul-bundle--on' : ''}`}>
            <div className="ul-bundle__top">
              <strong>{b.name}</strong>
              <span className="ul-tag ul-tag--ai">Gemini</span>
            </div>
            {b.why && <p className="ul-bundle__why">Its reason: {b.why}</p>}
            <p className="ul-bundle__num">
              {fmt(b.projects.length)} upgrades, {money(b.cost.high)}: <strong>+{fmt(b.more_sites)}</strong> {b.more_sites === 1 ? 'site' : 'sites'}
              {b.verified === b.more_sites ? ', every one re-run by the engine' : `, ${b.verified} re-run by the engine`}.{' '}
              {b.beats_engine
                ? `More than the engine's plan gets for the same money (+${same.more_sites}).`
                : `The engine's plan gets +${same.more_sites} for the same money, so it stays the plan.`}
            </p>
            <Button
              variant="secondary"
              aria-pressed={u.bundle === i}
              onClick={() => {
                showBundle(i)
                if (u.bundle !== i) flyTo(b.projects)
              }}
            >
              {u.bundle === i ? 'Back to the plan' : 'Show it on the map'}
            </Button>
          </article>
        )
      })}
      {ai.rejected?.length > 0 && (
        <details className="ul-details">
          <summary>
            {ai.rejected.length} {ai.rejected.length === 1 ? 'bundle' : 'bundles'} rejected by the engine
          </summary>
          <ul className="ul-small">
            {ai.rejected.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

function Points({ points, simulated = false }) {
  const o = useOverload()
  const u = useUnlock()
  const [all, setAll] = useState(false)
  const rows = all ? points : points.slice(0, 6)
  return (
    <section className="stack" aria-labelledby="ul-pts-h">
      <h4 className="ul-h" id="ul-pts-h">
        {simulated ? 'Weak points found so far' : 'The weak points it learned'}
      </h4>
      <p className="ul-small">The lines and transformers that block the most sites, fail first, and start the biggest blackouts in the simulations.</p>
      <ol className="ul-points">
        {rows.map((p) => (
          <li
            key={p.branch_id}
            id={`ul-pt-${p.branch_id}`}
            className={`ul-point${u.selected?.type === 'point' && u.selected.id === p.branch_id ? ' ul-point--on' : ''}`}
          >
            <button
              type="button"
              className="ul-point__btn"
              onClick={() => {
                select({ type: 'point', id: p.branch_id })
                o.focus(
                  [
                    [p.from.lon, p.from.lat],
                    [p.to.lon, p.to.lat],
                  ],
                  [p.mid[1], p.mid[0]],
                )
              }}
            >
              <span className="ul-point__rank">{p.rank}</span>
              <span className="ul-point__body">
                <span className="ul-point__name">{p.label.replace(/^the /, '')}</span>
                <span className="ul-point__meta">
                  {p.kind} · {fmt(p.kv)} kV · {fmt(p.rating_mva)} MVA
                </span>
                <span className="ul-point__why">{p.reason}</span>
              </span>
            </button>
          </li>
        ))}
      </ol>
      {points.length > 6 && (
        <button type="button" className="ul-link" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer' : `Show all ${points.length}`}
        </button>
      )}
    </section>
  )
}

function Learned({ r }) {
  const l = r.learned
  return (
    <p className="ul-learned">
      <strong>How it learned:</strong> simulated a {fmt(r.mw)} MW campus at {fmt(l.sites)} sites (one per town), ran the full cascade at {fmt(l.cascades)} of
      them and {fmt(l.verify_cascades)} more to check the plan ({fmt(l.solves)} power-flow solves), and tested {fmt(l.moves_tested)} upgrade options in{' '}
      {fmt(l.rounds)} rounds, in {Math.max(1, Math.round(l.seconds))} s
      {l.partial ? '; it ran out of time before checking everything, so some sites are unchecked' : ''}.
    </p>
  )
}

function Honest({ r }) {
  return (
    <div className="ul-honest">
      <p>
        {r.note} The whole plan: {moneyRange(r.headline.cost_low, r.headline.cost_high)}. {r.cost_basis}
      </p>
      <ul>
        {r.sources.map((s) => (
          <li key={s.url}>
            <a href={s.url} target="_blank" rel="noreferrer">
              {s.name}
            </a>
          </li>
        ))}
      </ul>
    </div>
  )
}
