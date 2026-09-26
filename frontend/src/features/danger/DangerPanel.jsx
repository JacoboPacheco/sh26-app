// FEATURE: danger zones — the panel (owned by the danger track).
// Contract: default export DangerPanel() — a section for the Data center panel (it reads the size,
// load level, region and firm/flexible from useOverload(), like the rest of that panel).
//
// "Show danger zones" answers the inverse of dropping one campus: at this size, where would it hit
// the most people? The backend runs the real cascade at one substation per town; the list ranks the
// towns, DangerLayer lights them on the map, and a click on either drops the campus there so the
// cascade can be run and watched.
import { useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, EmptyState, ErrorBanner, Loading } from '../../ui'
import './danger.css'
import { SIZE_MAX, SIZE_MIN } from './dangerApi'
import { compact, isPlacedAt, setDangerHover, setDangerOn, useDangerUi, useDangerZones, useResetOff } from './dangerStore'
import { usePlaceZone } from './usePlaceZone'

const SHOW = 8 // rows before "Show all"

export default function DangerPanel() {
  const { mw, loadFactor, firm, region, grid } = useOverload()
  const { on } = useDangerUi()
  const dz = useDangerZones()
  useResetOff()
  const size = dz.size
  const level = Math.round(loadFactor * 100)
  const national = region === 'US'
  const sizeNote =
    Math.round(mw) === size
      ? null
      : mw > SIZE_MAX
        ? `Shown for ${fmt(SIZE_MAX)} MW, the largest size checked.`
        : mw < SIZE_MIN
          ? `Shown for ${fmt(SIZE_MIN)} MW, the smallest size checked.`
          : `Shown for ${fmt(size)} MW (sizes are checked in 50 MW steps).`

  return (
    <section className="stack dz" aria-labelledby="dz-h">
      <div className="dz-head">
        <h3 className="panel-h" id="dz-h">
          Danger zones
        </h3>
        <Button variant="secondary" aria-pressed={on} onClick={() => setDangerOn((v) => !v)} disabled={national}>
          <span className="dz-toggle__dot" aria-hidden="true" />
          Show danger zones
        </Button>
      </div>
      <p className="dz-lede">
        Where a {firm ? 'firm ' : ''}
        <strong>{fmt(size)} MW</strong> campus would set off the biggest blackouts in this model
        {level !== 100 ? `, at ${level} % of normal demand` : ''}.
        {sizeNote && <span className="dz-size-note"> {sizeNote}</span>}
      </p>
      {national ? (
        <p className="muted">Open a state on the map to find its danger zones.</p>
      ) : (
        on && <div aria-live="polite">{<Body dz={dz} where={grid?.meta?.region_name || 'this state'} level={level} />}</div>
      )}
    </section>
  )
}

function Body({ dz, where, level }) {
  const { site, cascade, cascading, startCascade } = useOverload()
  const { hover } = useDangerUi()
  const placeZone = usePlaceZone()
  const [all, setAll] = useState(false)

  if (dz.status === 'error') return <ErrorBanner error={dz.error} onRetry={dz.retry} />
  const d = dz.data
  if (!d) return <Loading label={`Running the cascade at every town in ${where}… the first time can take a few seconds.`} />
  if (d.already_failing) {
    return (
      <p className="dz-warn" role="note">
        At {level} % of normal demand this model&apos;s grid fails with no campus at all: ~{compact(d.baseline_people_hit)} people hit (estimate). Set
        the clock back to normal demand to see where a campus does the most harm.
      </p>
    )
  }

  const zones = d.zones
  const total = d.zone_count ?? zones.length
  const shown = all ? zones : zones.slice(0, SHOW)
  const size = dz.stale ? null : d.mw
  let status
  if (dz.stale) status = <Loading label={`Updating for ${fmt(dz.size)} MW…`} />
  else if (d.partial) status = <Loading label={`Checked ${d.checked} of the ${d.likely} likeliest towns so far, still checking…`} />
  else
    status = (
      <p className="dz-sum">
        {total ? `${fmt(total)} ${total === 1 ? 'town sets' : 'towns set'} off a blackout` : 'No town sets off a blackout'}
        {d.calm ? `; ${fmt(d.calm)} take it with no line over its limit.` : '.'}
      </p>
    )

  return (
    <div className={`stack dz-body${dz.stale ? ' dz-body--stale' : ''}`}>
      {status}
      {zones.length === 0 ? (
        !d.partial && (
          <EmptyState title="No danger zones at this size">Every town takes a {fmt(d.mw)} MW campus without anyone losing power in this model.</EmptyState>
        )
      ) : (
        <ol className="dz-list">
          {shown.map((z, i) => {
            const placed = isPlacedAt(site, z)
            return (
              <li key={z.id} className={`dz-item${hover === z.id ? ' dz-item--hover' : ''}${placed ? ' dz-item--placed' : ''}`}>
                <button
                  type="button"
                  className="dz-row"
                  onClick={() => placeZone(z, size)}
                  onMouseEnter={() => setDangerHover(z.id)}
                  onMouseLeave={() => setDangerHover(null)}
                  onFocus={() => setDangerHover(z.id)}
                  onBlur={() => setDangerHover(null)}
                  aria-label={`${z.area}: about ${compact(z.people_hit)} people hit (estimate), ${z.steps} ${z.steps === 1 ? 'step' : 'steps'}. Put the campus here.`}
                >
                  <span className="dz-rank" aria-hidden="true">
                    {i + 1}
                  </span>
                  <span className="dz-row__text">
                    <strong>{z.area}</strong>
                    <span className="dz-row__meta">
                      {z.steps} {z.steps === 1 ? 'step' : 'steps'}
                      {z.outcome === 'islanded' ? ' · ends in a blackout' : ' · the grid settles'}
                    </span>
                  </span>
                  <span className="dz-row__n">
                    ~{compact(z.people_hit)}
                    <small>people hit</small>
                  </span>
                </button>
                {placed && !cascade && (
                  <div className="dz-run">
                    <span className="dz-run__label">Campus placed here</span>
                    <Button busy={cascading} onClick={() => startCascade()}>
                      {cascading ? 'Running…' : 'Run the cascade'}
                    </Button>
                  </div>
                )}
              </li>
            )
          })}
        </ol>
      )}
      {zones.length > SHOW && (
        <div className="row">
          <button type="button" className="dz-link" aria-expanded={all} onClick={() => setAll((v) => !v)}>
            {all ? 'Show fewer' : `Show all ${zones.length}`}
          </button>
        </div>
      )}
      <p className="dz-note">
        People hit (estimates): everyone whose power ran through a failed line or went out, each counted once. {d.note}.
      </p>
    </div>
  )
}
