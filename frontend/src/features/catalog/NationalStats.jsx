import { useState } from 'react'
import { fmt } from '../../geo'
import { Button, ErrorBanner, Loading } from '../../ui'
import { setStateFilter, useCatalog } from './catalogApi'
import { compact, mwText } from './format'
import './catalog.css'

// The national scoreboard: every counted campus (unique; operating, under construction or announced)
// tested alone on its state's synthetic model. One headline, three supporting numbers, and the
// reported megawatts by state (click a state to filter the list).
const TOP = 8

export default function NationalStats({ onPickState }) {
  const { data, error, loading, progress, stateFilter, reload } = useCatalog()
  const [all, setAll] = useState(false)
  if (!data) {
    if (error) return <ErrorBanner error={error} onRetry={reload} />
    return <Loading label={loading ? 'Loading the national numbers…' : 'Loading…'} />
  }
  const t = data.totals
  const testing = progress?.status === 'computing' || !t.complete
  const states = all ? data.by_state : data.by_state.slice(0, TOP)
  const maxMw = Math.max(1, ...data.by_state.map((s) => s.mw))
  const pick = (code) => {
    const next = stateFilter === code ? '' : code
    setStateFilter(next)
    onPickState?.(next)
  }

  return (
    <section className="cat-stats stack" aria-label="National totals">
      {testing ? (
        <div className="cat-hero">
          <p className="cat-hero__label">Testing every campus on its state&apos;s synthetic model</p>
          <progress className="cat-progress" max={progress?.total || 1} value={progress?.done || 0} aria-label="Campuses tested">
            {progress?.done || 0} of {progress?.total || 0}
          </progress>
          <p className="muted" aria-live="polite">
            {progress?.done || 0} of {progress?.total || t.listed} tested
          </p>
        </div>
      ) : (
        <div className="cat-hero">
          <p className="cat-hero__value">
            {t.overloads} <span className="cat-hero__of">of {t.tested}</span>
          </p>
          <p className="cat-hero__label">
            real campuses push lines past their limits when each is tested alone on its state&apos;s synthetic grid model
          </p>
        </div>
      )}

      <dl className="cat-tiles">
        <div className="cat-tile">
          <dt>Reported size</dt>
          <dd>{mwText(t.mw)}</dd>
          <p className="cat-tile__sub">
            {t.campuses} campuses in {t.states} states
          </p>
        </div>
        <div className="cat-tile">
          <dt>People without power</dt>
          <dd>{testing ? '…' : compact(t.people)}</dd>
          <p className="cat-tile__sub">estimate, sum of each campus tested alone (flexible)</p>
        </div>
        <div className="cat-tile">
          <dt>Campuses cut off</dt>
          <dd>{testing ? '…' : t.site_cut_off}</dd>
          <p className="cat-tile__sub">its own lines trip in the model&apos;s cascade</p>
        </div>
      </dl>

      <div className="stack cat-states">
        <h4 className="cat-h">Reported megawatts by state</h4>
        <ul className="cat-bars">
          {states.map((s) => (
            <li key={s.state}>
              <button
                type="button"
                className={`cat-bar${stateFilter === s.state ? ' cat-bar--on' : ''}`}
                aria-pressed={stateFilter === s.state}
                onClick={() => pick(s.state)}
                title={`${s.name}: ${fmt(s.mw)} MW reported across ${s.campuses} ${s.campuses === 1 ? 'campus' : 'campuses'}; ${s.overloads} of ${s.tested} tested push lines past their limits${s.people ? `; about ${fmt(s.people)} people without power (estimate)` : ''}`}
              >
                <span className="cat-bar__name">{s.name}</span>
                <span className="cat-bar__track" aria-hidden="true">
                  <span className="cat-bar__fill" style={{ '--w': `${Math.max(1.5, (s.mw / maxMw) * 100)}%` }} />
                </span>
                <span className="cat-bar__value">{mwText(s.mw)}</span>
                <span className="cat-bar__sub">
                  {testing || !s.tested ? `${s.campuses} ${s.campuses === 1 ? 'campus' : 'campuses'}` : `${s.overloads} of ${s.tested} over limit`}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {data.by_state.length > TOP && (
          <Button variant="secondary" onClick={() => setAll((v) => !v)} aria-expanded={all}>
            {all ? 'Show the top states' : `Show all ${data.by_state.length} states`}
          </Button>
        )}
      </div>
      <p className="cat-note">
        Counts unique campuses that are operating, under construction or announced
        {t.not_counted?.paused_or_canceled ? `; ${t.not_counted.paused_or_canceled} paused or canceled are listed but not counted` : ''}
        {t.duplicates ? `; ${t.duplicates} duplicate listings merged` : ''}
        {t.untested ? `; ${t.untested} couldn't be tested (off their state model's grid)` : ''}. Sizes as reported; results are from synthetic grid
        models, not the real utilities. People from the Census population per MW of each state model&apos;s load.
      </p>
    </section>
  )
}
