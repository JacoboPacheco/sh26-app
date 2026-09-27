// FEATURE: a headline number opened up: its formula with the case's own inputs, each taken from a field the API already
// returns (never re-derived here). An input the API does not return is left out of the line, never guessed.
//   kind 'people_hit'    the cascade (POST /api/grid/cascade): people_hit, people_per_mw, population; the state model's
//                        load and the Census source from the grid's meta (GET /api/grid)
//   kind 'cost'          the case's cost estimate (POST /api/cost, or /api/cost/quick outside Florida): the blackout
//                        line's lost MW, hours, MWh, value of lost load and its low-high; the headline's high end
//   kind 'outage_hours'  the same estimate's headline: outage_hours, the people still out, outage_basis
//   kind 'capacity'      the Strengthen study's capacity (POST /api/unlock/start -> result.capacity): today, the steps'
//                        cumulative cost, the N-0 check and the N-1 screen's sentence
// Estimates on a synthetic grid model (Breakthrough Energy / Texas A&M), never a real utility's network.
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { money, moneyRange } from '../cost/money'
import { useSteadyCaseCost } from '../cost/steady'
import { outageText } from '../cost/figures'
import { finalHit } from '../impact/caseCost'

const num = (v, d = 0) => (v == null || !Number.isFinite(Number(v)) ? null : Number(v).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 }))

function Row({ k, children }) {
  return (
    <div className="hwk-f__row">
      <dt>{k}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function Shell({ name, value, eq, children, sources, more }) {
  return (
    <section className="hwk-f" aria-label={`How ${name.toLowerCase()} is computed`}>
      <p className="hwk-f__head">
        <span className="hwk-f__name">{name}</span>
        {value != null && <span className="hwk-f__value">{value}</span>}
      </p>
      <p className="hwk-f__eq">= {eq}</p>
      <dl className="hwk-f__rows">{children}</dl>
      {more && (
        <details className="hwk__method">
          <summary>Assumptions</summary>
          <p>{more}</p>
        </details>
      )}
      {sources?.length > 0 && (
        <ul className="hwk__sources">
          {sources.map((s) => (
            <li key={s.url || s.name}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function PeopleHit({ cascade, meta }) {
  if (!cascade) return <p className="hwk__muted">Run the cascade to see how the people hit are counted.</p>
  const hit = finalHit(cascade)
  const ppm = cascade.people_per_mw ?? meta?.people_per_mw
  const pop = cascade.population ?? meta?.population
  const load = meta?.load_mw
  const state = meta?.region_name
  return (
    <Shell name="People hit (estimate)" value={fmt(hit)} eq="the load of every area the failures reach (MW) × people per MW">
      {ppm != null && (
        <Row k="People per MW">
          <b>{num(ppm, 2)}</b>
          {pop != null && load != null ? (
            <>
              {' '}
              = {state ? `${state}'s ` : ''}
              {fmt(pop)} residents ÷ the model&apos;s {fmt(load)} MW of load
            </>
          ) : pop != null ? (
            <> (the state&apos;s {fmt(pop)} residents spread over the model&apos;s load)</>
          ) : null}
        </Row>
      )}
      <Row k="The areas">
        Everywhere a failed line&apos;s power was flowing on to, and everywhere that lost power: the whole load of each substation there, each counted once and never
        more than the state&apos;s population.
      </Row>
      {cascade.people > 0 && (
        <Row k="Still out at the end">
          <b>{fmt(cascade.people)}</b> people: the load still cut when it settles ({num(cascade.lost_mw, 1)} MW) × the same people per MW. A part of the people hit.
        </Row>
      )}
      <Row k="Source">{meta?.population_source || 'U.S. Census Bureau population estimates'}. Estimates on a synthetic grid model.</Row>
    </Shell>
  )
}

// the map case's shared estimate when no `cost` is passed (features/impact/caseCost.js: one request for every reader)
function useCost(cost) {
  const { det } = useSteadyCaseCost()
  return cost === undefined ? det : cost
}

function Cost({ cost: given }) {
  const cost = useCost(given)
  const h = cost?.headline
  if (!h) return <p className="hwk__muted">The cost estimate is not ready yet.</p>
  const b = cost.lines?.find((l) => l.key === 'blackout')
  if (h.kind !== 'blackout' || !b) {
    const up = cost.lines?.find((l) => l.key === 'upgrades')
    return (
      <Shell name={h.label || 'Cost'} value={money(h.cost_high)} eq={up?.formula || 'no one loses power in this case'} sources={up?.sources} more={up?.assumption} />
    )
  }
  const lost = cost.lost_mw
  const hours = h.outage_hours ?? cost.hours_out
  return (
    <Shell name="Cost of the outage (estimate)" value={money(h.cost_high)} eq="MW of customers dark × hours without power × the value of lost load" sources={b.sources} more={b.assumption}>
      {lost != null && hours != null && (
        <Row k="Energy not served">
          {num(lost)} MW × {num(hours, 1)} h{b.mwh != null && <> = <b>{num(b.mwh)} MWh</b></>}
        </Row>
      )}
      {b.voll_low != null && b.voll_high != null && (
        <Row k="Value of lost load">
          {money(b.voll_low)}–{money(b.voll_high)} per MWh (LBNL&apos;s interruption costs by customer class, 2024 dollars)
        </Row>
      )}
      <Row k="Result">
        <b>{moneyRange(b.low, b.high)}</b>; the panel shows the high end, {money(h.cost_high)}.
      </Row>
      <Row k="On the counter">The same total shared by the people hit, so the replay ends on it.</Row>
    </Shell>
  )
}

function Outage({ cost: given }) {
  const cost = useCost(given)
  const h = cost?.headline
  if (!h) return <p className="hwk__muted">The outage estimate is not ready yet.</p>
  const hours = h.outage_hours ?? cost.hours_out
  return (
    <Shell name="Time without power (estimate)" value={hours > 0 ? outageText(hours) : h.outage_label} eq="a rule of thumb on the size of the incident">
      {h.people != null && (
        <Row k="The incident">
          <b>{fmt(h.people)}</b> people still without power when it settles
        </Row>
      )}
      {hours != null && (
        <Row k="Hours">
          <b>{num(hours, 1)} h</b>
          {h.outage_estimated === false ? ' (as chosen)' : ''}
        </Row>
      )}
      {h.outage_basis && <Row k="The rule">{h.outage_basis}</Row>}
    </Shell>
  )
}

function Capacity({ capacity: c, target, flex }) {
  if (!c) return <p className="hwk__muted">The capacity study is not ready yet.</p>
  const m = flex ? c.flexible : c.firm
  if (!m) return null
  const steps = m.steps || []
  // the meter's headline by default: the campuses the page's default budget connects (capacity.headline_count, which the
  // N-1 screen carries), else the whole search
  const want = target ?? (flex ? null : c.n1?.campuses) ?? steps.length
  const n = Math.max(0, Math.min(want, steps.length))
  const spent = n > 0 ? steps[n - 1]?.cum_cost?.high : 0
  const n1 = c.n1
  return (
    <Shell
      name="Campuses at once"
      value={`${fmt(n)} × ${fmt(c.mw)} MW`}
      eq="campuses placed one at a time, each where it fits with every one before it"
      sources={c.sources}
    >
      <Row k="Today">
        <b>{fmt(m.today)}</b> fit with no upgrade: each an exact power-flow solve with every campus so far, no line or transformer past its rating
      </Row>
      {n > m.today && (
        <Row k="With upgrades">
          <b>{fmt(n)}</b> for {money(spent)} (high end): when none fits, the cheapest bundle of re-ratings at the {fmt(c.try_sites ?? 30)} sites closest to fitting
        </Row>
      )}
      {m.verified && (
        <Row k="N-0 check">
          The search&apos;s last set, all {fmt(m.verified.campuses)} at once, through the cascade engine with every line in:{' '}
          {m.verified.calm ? 'nothing trips, no one loses power' : 'something still trips'}
        </Row>
      )}
      {n1?.sentence ? <Row k="N-1 screen">{n1.sentence}</Row> : n1?.status === 'pending' ? <Row k="N-1 screen">Still running on the server.</Row> : null}
      {c.unit && <Row k="Unit">{c.unit}</Row>}
    </Shell>
  )
}

/** A headline number's formula with the case's own inputs. `kind`: 'people_hit' | 'cost' | 'outage_hours' | 'capacity'.
 *  Inputs: `cascade` (people_hit; defaults to the map's), `cost` (the /api/cost estimate for cost and outage_hours;
 *  defaults to the map case's shared estimate), `capacity` (+ `target`, `flex`), `meta` (the grid's meta; defaults to the map's). */
export default function Formula({ kind, cascade, cost, capacity, target, flex = false, meta }) {
  const o = useOverload()
  const m = meta || o?.grid?.meta || null
  if (kind === 'people_hit') return <PeopleHit cascade={cascade ?? o?.cascade} meta={m} />
  if (kind === 'cost') return <Cost cost={cost} />
  if (kind === 'outage_hours') return <Outage cost={cost} />
  if (kind === 'capacity') return <Capacity capacity={capacity} target={target} flex={flex} />
  return null
}
