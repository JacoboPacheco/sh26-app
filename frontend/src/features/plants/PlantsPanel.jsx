// Plant Down: the supply side. "Whose lights go out" meets "whose plant keeps them on" — pick a
// plant, see which areas run on it, take it out and watch the same cascade the data center runs
// (with the current data center, heat level and storm). Or retire a whole fuel. The ranking
// answers the inverse: which single plants this state can least afford to lose.
//
// Composes PlantCard, FuelChips, RankingList and the trip result, for a "Plants" mode. The map
// half is PlantsLayer (a GridMap child). Tripping calls POST /api/plants/trip; the result plays on
// the map through the app store's showCascade(result) when the store has one, and is always
// summarized here. After a trip, "Run it again with the plant back" replays the same case with every
// plant running (POST /api/plants/restore, cached by the trip) and says what the plant was holding
// up; "Take it out again" replays the trip (no request). "Why did it fail?" asks the incident
// briefing for the root cause, which names the plant. Both only on a press (LAZY).
import { useEffect } from 'react'
import { useOverload } from '../../store'
import { Button, EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import FuelChips from './FuelChips'
import { approx, fmt, fuelLabel, plantName, pretty } from './fuels'
import PlantCard from './PlantCard'
import './plants.css'
import {
  caseWithRegion,
  ensurePlants,
  liveOverload,
  regionOf,
  removedIds,
  selectPlant,
  setPlants,
  startRestore,
  startTrip,
  startWhy,
  traceBody,
  tripKey,
  usePlants,
} from './plantsStore'
import RankingList from './RankingList'

export default function PlantsPanel() {
  const o = useOverload()
  useEffect(() => {
    liveOverload.current = o
  })
  const st = usePlants()
  const region = regionOf(o)
  useEffect(() => {
    if (region !== 'US') ensurePlants(region)
  }, [region])

  const list = st.region === region ? st.list : { status: 'loading', data: null, error: null }
  const data = list.data
  const regionName = data?.region_name || o?.grid?.meta?.region_name || (region === 'FL' ? 'Florida' : region)
  const plants = data?.plants || []
  const selected = plants.find((p) => p.id === st.selectedId) || null
  const removed = removedIds(st)
  const caseBody = caseWithRegion(o, region)
  const busy = st.trip.status === 'loading'
  const hasOut = st.outages.length > 0 || st.retireFuels.length > 0

  // Every change to what's out re-runs the outage on the current case. The app store plays the
  // result on the map (startCascade with the request as its pending cascade); the panel sums it up.
  function apply(outages, fuels) {
    setPlants({ outages, retireFuels: fuels })
    const live = liveOverload.current || o
    const pendingOurs = st.trip.status === 'loading' && live?.cascading
    const req = startTrip(caseWithRegion(live, region), outages, fuels)
    if (!req) {
      // every plant is back: take our cascade off the map (a plant trip's result carries removed_mw,
      // a plant-back replay plants_back), including one still on its way there
      if (live?.cascade?.removed_mw != null || live?.cascade?.plants_back || pendingOurs) live.clearCascade?.()
      return
    }
    if (typeof live?.startCascade === 'function') live.startCascade({}, req)
  }
  // the same case with every plant running, replayed on the map; the panel compares it with the trip
  function plantBack() {
    const live = liveOverload.current || o
    const req = startRestore(caseWithRegion(live, region), st.outages, st.retireFuels)
    if (req && typeof live?.startCascade === 'function') live.startCascade({}, req)
  }
  // the trip again, from the answer already here (no request)
  function plantOut() {
    const live = liveOverload.current || o
    if (st.trip.data && typeof live?.startCascade === 'function') live.startCascade({}, Promise.resolve(st.trip.data))
  }
  const why = () => startWhy(caseWithRegion(liveOverload.current || o, region), st.outages, st.retireFuels)
  const tripOne = (id) => apply([...new Set([...st.outages, id])], st.retireFuels)
  const restoreOne = (id) => apply(st.outages.filter((x) => x !== id), st.retireFuels)
  // Retiring a fuel folds that fuel's hand-tripped plants into it (one piece, not two), so
  // bringing the fuel back brings them back too.
  function toggleFuel(fuel) {
    if (st.retireFuels.includes(fuel)) return apply(st.outages, st.retireFuels.filter((f) => f !== fuel))
    const fuelOf = new Map(plants.map((p) => [p.id, p.fuel]))
    return apply(st.outages.filter((id) => fuelOf.get(id) !== fuel), [...st.retireFuels, fuel])
  }
  const restoreAll = () => apply([], [])
  const rerun = () => apply(st.outages, st.retireFuels)

  // pick from the list or the ranking: open its card and bring it into view
  function pick(id) {
    const p = plants.find((x) => x.id === id)
    selectPlant(id)
    if (p) o?.focus?.([[p.lon - 0.4, p.lat - 0.4], [p.lon + 0.4, p.lat + 0.4]], [p.lon, p.lat])
  }

  if (region === 'US') {
    return (
      <div className="stack panel-body pl-panel">
        <EmptyState title="Open a state to see its power plants">Click a state on the map: every plant in its model shows up here.</EmptyState>
      </div>
    )
  }
  if (list.status === 'error') {
    return (
      <div className="stack panel-body pl-panel">
        <ErrorBanner error={list.error} onRetry={() => ensurePlants(region, true)} />
      </div>
    )
  }
  if (!data) {
    return (
      <div className="stack panel-body pl-panel">
        <Loading label={`Loading ${regionName}'s power plants…`} />
      </div>
    )
  }

  const sorted = [...plants].sort((a, b) => b.pmax - a.pmax)
  const key = tripKey(caseBody, st.outages, st.retireFuels)
  const stale = st.trip.data && st.trip.key !== key
  // the app store is showing this very result (startCascade was handed our request)
  const onMap = !stale && !!st.trip.data && o?.cascade === st.trip.data
  // while it plays, the panel follows the map's counter instead of giving the ending away
  const playing = onMap && o?.playing ? { people: o.view?.peopleMax ?? 0, step: o.step ?? 0, total: st.trip.data.steps?.length || 0 } : null
  // the plant back: only the answer for exactly this case and these plants counts
  const back = st.restore.key === key ? st.restore : { status: 'idle', data: null, error: null }
  const backOnMap = !!back.data && o?.cascade === back.data
  const backPlaying = backOnMap && o?.playing ? { people: o.view?.peopleMax ?? 0, step: o.step ?? 0, total: back.data.steps?.length || 0 } : null
  const whyNow = st.why.key === key ? st.why : { status: 'idle', data: null, error: null }

  return (
    <div className="stack panel-body pl-panel">
      <p className="pl-lede">
        Every power plant in the model, sized by what it can make. Pick one to see which areas run on it, then take it out: the same
        cascade runs, with your data center, heat and storm.
      </p>
      <p className="pl-fine muted">
        {fmt(plants.length)} plants · {fmt(data.total_pmax)} MW of capacity for {fmt(data.total_load)} MW of load
      </p>

      <Field label="Find a plant" as="select" value={selected ? String(selected.id) : ''} onChange={(e) => e.target.value && pick(plants.find((p) => String(p.id) === e.target.value)?.id)}>
        <option value="">Largest first…</option>
        {sorted.map((p) => (
          <option key={p.id} value={String(p.id)}>
            {plantName(p)} · {fmt(p.pmax)} MW{removed.has(p.id) ? ' (out)' : ''}
          </option>
        ))}
      </Field>

      {selected ? (
        <PlantCard
          key={selected.id}
          plant={selected}
          region={region}
          traceCase={traceBody(caseBody, st)}
          totalLoad={data.total_load}
          out={removed.has(selected.id)}
          retiredByFuel={st.retireFuels.includes(selected.fuel)}
          busy={busy}
          onTrip={() => tripOne(selected.id)}
          onRestore={() => restoreOne(selected.id)}
          onClose={() => selectPlant(null)}
        />
      ) : (
        <EmptyState title="Click a plant on the map">Rings are power plants, sized by capacity. Or pick one from the list above.</EmptyState>
      )}

      {(hasOut || st.trip.status !== 'idle') && (
        <TripResult
          trip={st.trip}
          outages={st.outages}
          fuels={st.retireFuels}
          plants={plants}
          stale={stale}
          onMap={onMap}
          playing={playing}
          subById={o?.subById}
          onRestore={restoreOne}
          onFuel={toggleFuel}
          onRestoreAll={restoreAll}
          onRerun={rerun}
        >
          {st.trip.data && !stale && st.trip.status === 'done' && !playing && (
            <PlantBack
              trip={st.trip.data}
              back={back}
              backOnMap={backOnMap}
              backPlaying={backPlaying}
              why={whyNow}
              onBack={plantBack}
              onOut={plantOut}
              onWhy={why}
            />
          )}
        </TripResult>
      )}

      <FuelChips byFuel={data.by_fuel} retired={st.retireFuels} busy={busy} onToggle={toggleFuel} />

      <RankingList
        region={region}
        regionName={regionName}
        loadFactor={o?.loadFactor ?? 1}
        site={o?.site && o?.mw ? { lat: o.site.lat, lon: o.site.lon, mw: o.mw, firm: !!caseBody.firm } : null}
        siteLabel={o?.site && o?.mw ? `your ${fmt(o.mw)} MW data center${o.result?.sub_name ? ` at ${pretty(o.result.sub_name)}` : ''}` : null}
        selectedId={st.selectedId}
        onPick={(row) => {
          // a substation with several plants opens its largest (the one that carries the ranking)
          const own = plants.filter((p) => row.ids.includes(p.id)).sort((a, b) => b.pmax - a.pmax)
          if (own.length || row.ids.length) pick(own[0]?.id ?? row.ids[0])
        }}
      />

      <p className="pl-fine muted">
        Synthetic plants from the Breakthrough Energy / Texas A&amp;M test system (CC-BY 4.0), named after their substations; results
        describe the model, not any real plant or utility. People counts are estimates.
      </p>
    </div>
  )
}

// What taking those plants out does: the pieces that are out (each removable), then the outcome.
function TripResult({ trip, outages, fuels, plants, stale, onMap, playing, subById, onRestore, onFuel, onRestoreAll, onRerun, children }) {
  const byId = new Map(plants.map((p) => [p.id, p]))
  const d = trip.data
  const people = d ? (d.people ?? 0) : 0
  const trips = d ? (d.steps || []).filter((s) => s.action !== 'storm' && (s.tripped || []).length).length : 0
  const areas = d ? topAreas(d.affected, subById) : []
  const base = d?.baseline?.people || 0
  return (
    <section className="stack pl-result" aria-label="Plants out" aria-live="polite">
      <div className="pl-result__head">
        <h3 className="panel-h">Out of service</h3>
        {(outages.length > 0 || fuels.length > 0) && (
          <button type="button" className="pl-link" onClick={onRestoreAll} disabled={trip.status === 'loading'}>
            Put everything back
          </button>
        )}
      </div>
      <ul className="pl-pieces">
        {fuels.map((f) => (
          <li key={`f-${f}`} className="pl-piece">
            All {fuelLabel(f).toLowerCase()}
            <button type="button" className="pl-piece__x" onClick={() => onFuel(f)} aria-label={`Bring ${fuelLabel(f).toLowerCase()} back`}>
              ×
            </button>
          </li>
        ))}
        {outages.map((id) => {
          const p = byId.get(id)
          return (
            <li key={`p-${id}`} className="pl-piece">
              {p ? plantName(p) : `Plant ${id}`}
              <button type="button" className="pl-piece__x" onClick={() => onRestore(id)} aria-label={`Put ${p ? pretty(p.sub_name) : 'this plant'} back`}>
                ×
              </button>
            </li>
          )
        })}
      </ul>

      {trip.status === 'loading' && <Loading label="Taking it out and re-solving the grid…" />}
      <ErrorBanner error={trip.status === 'error' ? trip.error : null} onRetry={onRerun} />

      {d && trip.status !== 'loading' && playing && (
        <div className={`pl-verdict${playing.people > 0 ? ' pl-verdict--bad' : ' pl-verdict--live'}`}>
          <p className="pl-verdict__n">
            {playing.people > 0 ? (
              <>
                <strong>~{approx(playing.people)}</strong> people without power so far <span className="muted">(estimate)</span>
              </>
            ) : (
              <strong>No one in the dark yet</strong>
            )}
          </p>
          <p className="muted">
            {fmt(d.removed_mw)} MW of capacity out. Watch it spread on the map: step {fmt(Math.max(1, Math.min(playing.step, playing.total)))} of {fmt(playing.total)}.
          </p>
        </div>
      )}

      {d && trip.status !== 'loading' && !playing && (
        <div className={`pl-verdict${people > 0 ? ' pl-verdict--bad' : ''}`}>
          <p className="pl-verdict__n">
            {people > 0 ? (
              <>
                <strong>~{approx(people)}</strong> people without power <span className="muted">(estimate)</span>
              </>
            ) : (
              <strong>The grid holds</strong>
            )}
          </p>
          <p className="muted">{story(d, people, trips)}</p>
          {base > 0 && (
            <p className="pl-fine muted">
              With every plant running this case already darkens ~{approx(base)}; the outage adds ~{approx(d.added_people ?? Math.max(0, people - base))}.
            </p>
          )}
          {areas.length > 0 && <p className="pl-fine">Hardest hit: {areas.map((a) => a.area).join(', ')}</p>}
          {onMap && people > 0 && <p className="pl-fine muted">On the map: use the timeline below to replay it.</p>}
          {stale && (
            <div className="row">
              <span className="muted pl-fine">Your scenario changed since this ran.</span>
              <Button variant="secondary" onClick={onRerun}>
                Run it again
              </Button>
            </div>
          )}
        </div>
      )}
      {children}
    </section>
  )
}

// After a trip: the same case with the plant back (what it was holding up), and why it failed.
function PlantBack({ trip, back, backOnMap, backPlaying, why, onBack, onOut, onWhy }) {
  const many = (trip.removed?.length || 0) > 1
  const it = many ? 'the plants' : 'the plant'
  const It = many ? 'The plants' : 'The plant'
  const d = back.data
  const cmp = d?.compare
  const outPeople = trip.people ?? 0
  const trips = (trip.steps || []).filter((s) => (s.tripped || []).length).length
  const showWhy = outPeople > 0 || trips > 0
  return (
    <div className="stack pl-back">
      {back.status === 'loading' && <Loading label={`Running it again with ${it} back…`} />}
      <ErrorBanner error={back.status === 'error' ? back.error : null} onRetry={onBack} />

      {cmp && backPlaying && (
        <div className={`pl-verdict${backPlaying.people > 0 ? ' pl-verdict--bad' : ' pl-verdict--live'}`}>
          <p className="pl-verdict__n">
            {backPlaying.people > 0 ? (
              <>
                With {it} back: <strong>~{approx(backPlaying.people)}</strong> people without power so far <span className="muted">(estimate)</span>
              </>
            ) : (
              <>With {it} back: no one in the dark yet</>
            )}
          </p>
        </div>
      )}

      {cmp && !backPlaying && back.status !== 'loading' && (
        <div className="pl-compare" aria-label={`With ${it} back`}>
          <h4 className="pl-compare__h">With {it} back</h4>
          <dl className="pl-compare__rows">
            <div className="pl-compare__row">
              <dt>{It} out</dt>
              <dd className={cmp.out.people > 0 ? 'pl-n--bad' : 'pl-n--ok'}>{cmp.out.people > 0 ? `~${approx(cmp.out.people)}` : 'Holds'}</dd>
            </div>
            <div className="pl-compare__row">
              <dt>{It} running</dt>
              <dd className={cmp.back.people > 0 ? 'pl-n--bad' : 'pl-n--ok'}>{cmp.back.people > 0 ? `~${approx(cmp.back.people)}` : 'Holds'}</dd>
            </div>
          </dl>
          <p className="pl-fine muted">People without power, same case otherwise (estimates).</p>
          <p className="pl-compare__say">{heldSentence(cmp, it, It)}</p>
          {cmp.held_areas?.length > 0 && cmp.held_people > 0 && (
            <ul className="pl-held" aria-label={`Areas ${it} kept lit`}>
              {cmp.held_areas.slice(0, 5).map((a) => (
                <li key={a.area}>
                  <span>{a.area}</span>
                  <span className="pl-held__n">~{approx(a.people)}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="pl-fine muted">
            {backOnMap ? `On the map: the same case with ${it} running. Green: the areas ${many ? 'they keep' : 'it keeps'} lit.` : `Replay it with ${it} running to see the areas ${many ? 'they keep' : 'it keeps'} lit.`}
          </p>
        </div>
      )}

      <div className="row pl-back__actions">
        {backOnMap ? (
          <Button variant="secondary" onClick={onOut}>
            Take {many ? 'them' : 'it'} out again
          </Button>
        ) : (
          <Button variant="secondary" onClick={onBack} busy={back.status === 'loading'}>
            Run it again with {it} back
          </Button>
        )}
        {showWhy && (
          <Button variant="secondary" onClick={onWhy} busy={why.status === 'loading'} disabled={why.status === 'done'}>
            Why did it fail?
          </Button>
        )}
      </div>

      {why.status === 'loading' && <Loading label="Reading the incident report…" />}
      <ErrorBanner error={why.status === 'error' ? why.error : null} onRetry={onWhy} />
      {why.data && <WhyItFailed report={why.data} />}
    </div>
  )
}

function heldSentence(cmp, it, It) {
  if (cmp.held_people > 0) {
    const where = (cmp.held_areas || []).slice(0, 3).map((a) => a.area)
    return `${It} ${it === 'the plants' ? 'were' : 'was'} holding up ~${approx(cmp.held_people)} people${where.length ? `, in ${joinWords(where)} and more` : ''}.`
  }
  if (cmp.worse_with_plants) return `This case is no better with ${it} running: ~${approx(cmp.back.people)} people against ~${approx(cmp.out.people)}.`
  if (cmp.out.people > 0) return `Bringing ${it} back changes nothing here: the same people lose power either way.`
  return `The grid holds either way: the rest of the grid picks up the output.`
}

const joinWords = (xs) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)

// The incident briefing's own words: the root cause (it names the plant), and the best fix it verified.
function WhyItFailed({ report }) {
  const rc = report.root_cause || {}
  const best = Number.isInteger(report.best_fix) ? report.fixes?.[report.best_fix] : null
  return (
    <section className="pl-why" aria-label="Why it failed">
      <h4 className="pl-compare__h">Why it failed</h4>
      <p>{rc.sentence}</p>
      {best && best.verdict === 'holds' && (
        <p className="pl-why__fix">
          <span className="pl-why__tag">Verified fix</span> {best.action}
        </p>
      )}
      <p className="pl-fine muted">From the incident briefing: every number re-run by the engine on the synthetic model.</p>
    </section>
  )
}

// One plain sentence: what went out, what it did the moment it went, and how it ended.
function story(d, people, trips) {
  const n = d.removed?.length
  const what = `${fmt(d.removed_mw)} MW of capacity out${Number.isFinite(n) ? ` (${fmt(n)} ${n === 1 ? 'plant' : 'plants'}` : ''}${
    Number.isFinite(n) && d.removed_output_mw != null ? `, making ${fmt(d.removed_output_mw)} MW)` : Number.isFinite(n) ? ')' : ''
  }.`
  // (never more than the demand itself: an export tie can't be short of power it no longer has)
  const shortMw = Math.min(Number(d.supply?.short_mw) || 0, Number(d.supply?.demand_mw) || Infinity)
  const short = shortMw > 0 ? ` What's left can't meet demand: ${fmt(shortMw)} MW short, so load is cut.` : ''
  const over = d.initial?.overloaded
  const first = over > 0 ? ` The moment it goes, ${fmt(over)} ${over === 1 ? 'line overloads' : 'lines overload'}.` : ''
  const lines = trips ? `${fmt(trips)} ${trips === 1 ? 'line trips' : 'lines trip'} in turn${d.capped ? ' (the model stops counting there)' : ''}` : ''
  let end
  if (people > 0) end = `${lines ? `${lines}` : 'The rest of the grid can’t cover it'}${d.outcome === 'islanded' ? ' and part of the grid is cut off' : ''}.`
  else if (trips) end = `${lines}, but everyone keeps power.`
  else end = 'Other plants pick up the slack: no line overloads.'
  return `${what}${short}${first} ${end}`
}

// The areas that lost the most (sub id → MW lost, grouped by the town each substation is named for).
function topAreas(affected, subById, n = 4) {
  if (!affected || !subById) return []
  const by = new Map()
  Object.entries(affected).forEach(([id, mw]) => {
    const s = subById.get(Number(id))
    const area = s?.area || (s ? pretty(s.name).replace(/\s+\d+$/, '') : null)
    if (area) by.set(area, (by.get(area) || 0) + Number(mw || 0))
  })
  return [...by]
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([area, mw]) => ({ area, mw }))
}
