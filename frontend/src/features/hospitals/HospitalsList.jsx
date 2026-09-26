// Who is on backup power: the hospitals whose area lost most of its power in the case on screen
// ("4 hospitals on backup power (estimate)"), then those in areas with partial outages, by name.
// It follows the cascade replay step by step (the same status the map's crosses show). A name
// flies the map to that hospital and marks it. The assumption and the OpenStreetMap credit are
// always on screen.
import { useEffect, useId, useRef, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { useOverload } from '../../store'
import './hospitals.css'
import { pickHospital, plural, useHospitalStatus, usePickedHospital } from './hospitalsApi'

const SHOW = 6 // rows before "Show all"

export default function HospitalsList() {
  const o = useOverload()
  const st = useHospitalStatus()
  const picked = usePickedHospital()
  const headId = useId()
  const [all, setAll] = useState(false)

  // a new case or a new state starts collapsed
  const resetCount = o?.resetCount
  const seen = useRef(resetCount)
  useEffect(() => {
    if (resetCount === seen.current) return
    seen.current = resetCount
    setAll(false)
  }, [resetCount])

  if (!o || o.region === 'US') return null

  const regionName = o.grid?.meta?.region_name || st.data?.region_name || 'this state'
  const show = (h) => {
    pickHospital({ region: st.region, id: h.id })
    o.focus([[h.lon, h.lat]], [h.lon, h.lat])
  }

  let body
  if (st.error) body = <ErrorBanner error={st.error} onRetry={st.retry} />
  else if (!st.ready) body = <Loading label="Loading hospitals…" />
  else if (!st.counts.total) body = <p className="muted">No hospitals in {regionName} in the OpenStreetMap extract.</p>
  else {
    const { backup, strained, counts } = st
    const rows = [...backup, ...strained]
    const shown = all ? rows : rows.slice(0, SHOW)
    body = (
      <>
        {rows.length === 0 ? (
          <p className="hx-calm" aria-live="polite">
            None of the {plural(counts.in_model, 'hospital', 'hospitals')} this model reaches has lost grid power.
          </p>
        ) : (
          <div className="hx-summary" aria-live="polite">
            <p className="hx-headline">
              <span className={`hx-n${counts.backup ? ' hx-n--bad' : ''}`}>{counts.backup.toLocaleString('en-US')}</span>{' '}
              {counts.backup === 1 ? 'hospital' : 'hospitals'} on backup power (estimate)
              {st.peakBackup > counts.backup && <span className="hx-peak"> · {st.peakBackup.toLocaleString('en-US')} at its worst</span>}
            </p>
            {counts.strained > 0 && (
              <p className="muted hx-sub">
                {counts.backup ? `${plural(counts.strained, 'more', 'more')} in` : `${plural(counts.strained, 'hospital', 'hospitals')} in`} areas with
                partial outages.
              </p>
            )}
          </div>
        )}
        {rows.length > 0 && (
          <ul className="hx-rows">
            {shown.map((h) => {
              const on = picked?.region === st.region && picked.id === h.id
              return (
                <li key={h.id}>
                  <button
                    type="button"
                    className={`hx-row${on ? ' hx-row--on' : ''}`}
                    aria-pressed={on}
                    onClick={() => show(h)}
                    aria-label={`${h.name}, ${h.area}: ${h.status === 'backup' ? 'on backup power' : 'partial outages'} (estimate). Show it on the map.`}
                  >
                    <span className={`hx-dot hx-dot--${h.status}`} aria-hidden="true" />
                    <span className="hx-row__name">{h.name}</span>
                    <span className="hx-row__meta">
                      {h.area} · {h.status === 'backup' ? 'backup' : 'partial'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
        {rows.length > SHOW && (
          <button type="button" className="hx-more" onClick={() => setAll((v) => !v)} aria-expanded={all}>
            {all ? 'Show fewer' : `Show all ${rows.length.toLocaleString('en-US')}`}
          </button>
        )}
        <p className="hx-note">
          A hospital counts as on backup power when the substation nearest it in the synthetic model lost at least{' '}
          {Math.round((st.data.backup_share || 0.6) * 100)}% of its load. Real hospitals have their own generators and are restored first.
          {counts.outside_model > 0 &&
            ` ${plural(counts.outside_model, 'hospital', 'hospitals')} in ${regionName} ${counts.outside_model === 1 ? 'is' : 'are'} beyond the model's reach and not counted.`}
        </p>
      </>
    )
  }

  return (
    <section className="hx-list" aria-labelledby={headId}>
      <div className="hx-head">
        <h2 id={headId} className="panel-h">
          Hospitals
        </h2>
        {st.ready && st.counts.total > 0 && <span className="hx-count">{plural(st.counts.in_model, 'in the model', 'in the model')}</span>}
      </div>
      {body}
      <p className="hx-credit">
        Hospitals:{' '}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          © OpenStreetMap contributors
        </a>{' '}
        (ODbL)
      </p>
    </section>
  )
}
