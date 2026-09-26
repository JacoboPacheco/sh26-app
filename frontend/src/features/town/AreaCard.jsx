import { useEffect, useId } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner } from '../../ui'
import { conditionsOf, hoverCase, isOpen, levelText, openCase, peopleNum, useAreaProfile, useAreaUi } from './areaStore'
import './town.css'

// One area of the synthetic model: who lives on its lights (estimate), how big a data center fits at
// its best substation, what happens beyond that, and which stress tests leave it without power.
// Every test opens in the workspace with the exact case the backend ran, so the cascade replays it.
// The tests run under the workspace's conditions: its firm switch and its time of day.
export default function AreaCard({ slug, onClose }) {
  const o = useOverload()
  const c = conditionsOf(o)
  const p = useAreaProfile(o.region, slug, c)
  const { name: pickedName } = useAreaUi()
  const hId = useId()
  const d = p.data
  const name = d?.name || pickedName || 'This area'
  const regionName = d?.region_name || o.grid?.meta?.region_name || o.region

  useEffect(() => () => hoverCase(null), []) // a test hovered as the card closes leaves no marker behind
  const open = (x) => openCase(o, x)
  const opened = (k) => isOpen(o, k) // is this exact case what the workspace holds now?

  return (
    <article className="area-card" aria-labelledby={hId} aria-busy={p.status === 'loading'}>
      <header className="area-card__head">
        <div className="area-card__title">
          <h3 id={hId} className="area-card__name">
            {name}
          </h3>
          <p className="area-card__sub">
            {d ? `${fmt(d.subs)} ${d.subs === 1 ? 'substation' : 'substations'} in the synthetic ${regionName} model` : `Synthetic ${regionName} model`}
          </p>
        </div>
        {onClose && (
          <button type="button" className="area-card__close" onClick={onClose} aria-label={`Close ${name}`}>
            ×
          </button>
        )}
      </header>

      {p.status === 'loading' && <Progress regionName={regionName} />}
      {p.status === 'error' && <ErrorBanner error={p.error} onRetry={p.retry} />}

      {d && (
        <>
          {d.people >= 5 ? (
            <p className="area-card__people">
              <strong>About {peopleNum(d.people)}</strong> people live on these lights <span className="area-card__est">(estimate)</span>
            </p>
          ) : (
            <p className="area-card__people">
              No homes are served straight from these lights in the synthetic model{' '}
              <span className="area-card__est">(substations that switch power or connect plants)</span>
            </p>
          )}

          <Room d={d} opened={opened} onOpen={open} />
          <Exposure d={d} opened={opened} onOpen={open} />

          <p className="area-card__note">
            {d.note} Population: {d.population_source}.
          </p>
        </>
      )}
    </article>
  )
}

function Progress({ regionName }) {
  return (
    <div className="area-progress" role="status" aria-live="polite">
      <span>Running the stress tests on the synthetic {regionName} model…</span>
      <span className="area-progress__bar" aria-hidden="true" />
    </div>
  )
}

// "Open in the workspace" under a test while the workspace holds exactly its case (so the click has a
// visible answer, and the note goes away once the workspace changes).
function OpenedNote({ show }) {
  return (
    <p className="area-opened" role="status" aria-live="polite">
      {show ? 'Open in the workspace now. Run the cascade there to watch it.' : ''}
    </p>
  )
}

// ------------------------------------------------------------------ room for a data center
function Room({ d, opened, onOpen }) {
  const r = d.room
  const b = d.beyond
  const cond = { region: d.region, firm: d.exposure.firm, load_factor: d.exposure.load_factor }
  const tryIt = r && { id: 'room', label: `${fmt(r.mw)} MW at ${r.sub_name}`, case: { ...cond, lat: r.lat, lon: r.lon, mw: r.mw } }
  const cliff = b && { id: 'beyond', label: `1 GW at ${b.sub_name}`, case: b.case }
  return (
    <section className="area-sec" aria-label="Room for a data center">
      <h4 className="area-sec__h">Room for a data center</h4>
      {r ? (
        <p>
          {r.at_least ? (
            <>
              <strong>{fmt(r.mw)} MW or more</strong> fits at {r.sub_name} with no line over its limit.
            </>
          ) : (
            <>
              Up to <strong>{fmt(r.mw)} MW</strong> fits at {r.sub_name} before any line goes over its limit.
            </>
          )}{' '}
          <span className="area-card__est">Checked on the synthetic model.</span>
        </p>
      ) : (
        <p>No room here: a line near {d.name} is already at its limit in the synthetic model.</p>
      )}
      {tryIt && (
        <div className="area-sec__act">
          <Button variant="secondary" onClick={() => onOpen(tryIt)}>
            Try {fmt(r.mw)} MW here
          </Button>
          <OpenedNote show={opened(tryIt.case)} />
        </div>
      )}
      {b && (
        <div className="area-cliff">
          <p>
            <CliffText d={d} b={b} />
          </p>
          <div className="area-sec__act">
            <Button variant="secondary" onClick={() => onOpen(cliff)}>
              Open the 1 GW case
            </Button>
            <OpenedNote show={opened(cliff.case)} />
          </div>
        </div>
      )}
    </section>
  )
}

function CliffText({ d, b }) {
  const where = `At 1 GW at ${b.sub_name}`
  if (!b.total_people) return <>{where}, lines go over their limits but no one loses power in the model.</>
  const cutOff = b.site_cut_off ? ' cuts the campus off and' : ''
  const areas = b.areas_hit === 1 ? '1 area' : `${fmt(b.areas_hit)} areas`
  return (
    <>
      {where}, the cascade{cutOff} leaves <strong className="area-hit">about {peopleNum(b.total_people)} people</strong> without power in {areas}
      {b.hit ? `, about ${peopleNum(b.people)} of them in ${d.name}` : `; ${d.name} keeps power`}{' '}
      <span className="area-card__est">(estimates)</span>. <DoubleText b={b} firm={d.exposure.firm} />
    </>
  )
}

// The cliff, measured: the same test at twice the size. This answers "why doesn't a bigger campus
// black out more people?": past the room, the campus's own connection is usually what trips, which
// cuts the campus off, so the blackout stops depending on its size (or even shrinks).
function DoubleText({ b, firm }) {
  const d2 = b.double_people
  if (d2 == null) return null
  const d1 = b.total_people
  const two = `At ${fmt(b.double_mw / 1000)} GW`
  const est = <span className="area-card__est">(estimate)</span>
  const same = Math.abs(d2 - d1) <= Math.max(0.01 * d1, 50)
  const cutOff = b.site_cut_off && b.double_site_cut_off
  const service = firm ? '' : ' on flexible service'
  if (same && cutOff)
    return (
      <>
        {two}, the same people: the campus&apos;s own connection trips first and cuts it off, so a bigger campus doesn&apos;t reach further{service}.
      </>
    )
  if (d2 < d1 && b.double_site_cut_off && b.double_steps != null && b.double_steps < b.steps)
    return (
      <>
        {two}, fewer: about {peopleNum(d2)} people {est}. The bigger campus overloads its own connection sooner, so it is cut off before the
        failures spread as far.
      </>
    )
  if (d2 > d1 && !same)
    return (
      <>
        {two}, more: about {peopleNum(d2)} people {est}.
      </>
    )
  return (
    <>
      {two}: about {peopleNum(d2)} people {est}.
    </>
  )
}

// ------------------------------------------------------------------ stress tests
function Exposure({ d, opened, onOpen }) {
  const e = d.exposure
  const hits = e.cases.filter((x) => x.hit)
  const rest = e.cases.filter((x) => !x.hit)
  const catalog = e.cases.filter((x) => x.kind === 'catalog').length
  const hypo = e.cases.length - catalog
  const heat = { ...e.heat_wave, label: 'A heat wave alone, no data center' }
  // The 1 GW test above ("beyond") is part of the battery only when the area is one of the largest;
  // otherwise say so, so "0 of 17" doesn't read as a contradiction of the dark 1 GW sentence above.
  const ownBeyond = d.beyond?.hit && !e.cases.some((x) => x.own_area && x.kind === 'hypothetical')
  return (
    <section className="area-sec" aria-label="Stress tests">
      <h4 className="area-sec__h">Stress tests</h4>
      <p className="area-verdict">
        {d.name} lost power in{' '}
        <strong className={e.hits ? 'area-hit' : 'area-ok'}>
          {fmt(e.hits)} of {fmt(e.tested)}
        </strong>{' '}
        tests across {d.region_name} on the synthetic model.
        {ownBeyond && <> A 1 GW campus in {d.name} itself does darken it (above).</>}
      </p>
      <p className="area-card__est">
        Tested:{' '}
        {[catalog > 0 && `${fmt(catalog)} reported ${catalog === 1 ? 'campus' : 'campuses'} at ${catalog === 1 ? 'its' : 'their'} reported size`, hypo > 0 && `a hypothetical 1 GW campus in each of the ${fmt(hypo)} largest areas`]
          .filter(Boolean)
          .join(' and ')}
        ; {e.firm ? 'on firm service (the operator cuts other customers to keep a campus on)' : 'flexible (the grid may cut a campus off)'}, at {levelText(e.load_factor)}.
        <Untested skipped={e.skipped} regionName={d.region_name} />
      </p>
      {hits.length > 0 && (
        <ul className="area-cases" aria-label={`Tests that leave ${d.name} without power`}>
          {hits.map((x) => (
            <CaseRow key={x.id} x={x} area={d.name} opened={opened(x.case)} onOpen={onOpen} />
          ))}
        </ul>
      )}
      {rest.length > 0 && (
        <details className="area-more">
          <summary>
            {hits.length ? `The other ${fmt(rest.length)} tests` : `All ${fmt(rest.length)} tests`}: {d.name} kept power
          </summary>
          <ul className="area-cases">
            {rest.map((x) => (
              <CaseRow key={x.id} x={x} area={d.name} opened={opened(x.case)} onOpen={onOpen} />
            ))}
          </ul>
        </details>
      )}
      <ul className="area-cases area-cases--heat" aria-label="Heat wave">
        <CaseRow x={heat} area={d.name} opened={opened(heat.case)} onOpen={onOpen} />
      </ul>
    </section>
  )
}

// The reported campuses in the state that weren't tested, and why (counts only).
function Untested({ skipped, regionName }) {
  if (!skipped?.length) return null
  const n = (why) => skipped.filter((x) => x.why === why).length
  const parts = [
    n('outside') && `${fmt(n('outside'))} outside the area the synthetic model covers`,
    n('limit') && `${fmt(n('limit'))} past the largest 16`,
    n('invalid') && `${fmt(n('invalid'))} without a usable location or size`,
  ].filter(Boolean)
  return (
    <>
      {' '}
      Not tested: {fmt(skipped.length)} more reported {skipped.length === 1 ? 'campus' : 'campuses'} in {regionName} ({parts.join(', ')}).
    </>
  )
}

function caseMeta(x) {
  if (x.kind === 'catalog') {
    const size = x.capped ? `${fmt(x.reported_mw)} MW reported, tested at the model's ${fmt(x.mw)} MW cap` : `${fmt(x.reported_mw)} MW reported`
    return [size, x.place, x.status && `${x.status} (as reported)`].filter(Boolean).join(' · ')
  }
  if (x.kind === 'heat') return `Every load at ${levelText(x.load_factor)}`
  return `Connects at ${x.sub_name}`
}

function caseResult(x, area) {
  if (x.hit) {
    const part = x.share < 0.9 ? `${area} partly dark (${Math.max(1, Math.round(x.share * 100))} % of its load)` : `${area} dark`
    return `${part} · about ${peopleNum(x.people)} people here (estimate)`
  }
  if (x.briefly) return `${area} lost power during the cascade and had it back by the end`
  if (x.total_people > 0) return `${area} kept power · about ${peopleNum(x.total_people)} people lost it elsewhere (estimate)`
  return 'No one lost power in this test'
}

function CaseRow({ x, area, opened, onOpen }) {
  const title = x.kind === 'heat' ? 'A heat wave alone, no data center' : x.label
  return (
    <li
      className={`area-case${x.hit ? ' area-case--hit' : ''}`}
      onMouseEnter={() => hoverCase(x)}
      onMouseLeave={() => hoverCase(null)}
      onFocus={() => hoverCase(x)}
      onBlur={() => hoverCase(null)}
    >
      <div className="area-case__main">
        <span className="area-case__title">{title}</span>
        <span className="area-case__meta">{caseMeta(x)}</span>
        <span className="area-case__result">{caseResult(x, area)}</span>
        <OpenedNote show={opened} />
      </div>
      <div className="area-case__act">
        <Button variant="secondary" onClick={() => onOpen(x)} aria-label={`Open in the workspace: ${title}`}>
          Open
        </Button>
        {x.source && (
          <a className="area-case__src" href={x.source.url} target="_blank" rel="noopener noreferrer" title={x.source.title}>
            Source<span className="area-vh"> (as reported by {x.source.title}, opens in a new tab)</span>
          </a>
        )}
      </div>
    </li>
  )
}
