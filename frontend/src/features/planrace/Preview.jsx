// #/preview/planrace — the plan race panel before it has a place on the Strengthen page. "This backend" drives the
// real race (backend/plan_agents.py; the preview falls back to the recording when the backend has no race routes);
// "A recorded race" replays a real run saved in fixture.json. The map callback is shown as text here: mounted on
// Strengthen, onShowPlan puts the plan on the map.
import { useEffect, useState } from 'react'
import { Field } from '../../ui'
import { money } from './money'
import PlanRacePanel from './PlanRacePanel'
import { fixtureClient } from './fixtureClient'
import { liveClient } from './planraceApi'

export default function Preview() {
  const [source, setSource] = useState('live')
  const [mode, setMode] = useState('firm')
  const [mw, setMw] = useState(1000) // the baked Florida studies: 0.5, 1, 2 and 5 GW (the recording is 1 GW)
  const [plan, setPlan] = useState(null)
  const [note, setNote] = useState('')

  // no race routes on this backend (not mounted yet, or no backend at all): replay the recording instead
  useEffect(() => {
    if (source !== 'live') return undefined
    let off = false
    liveClient.knee({ mode, mw }).catch((e) => {
      const msg = String(e?.message || e)
      if (!off && !/Run the Strengthen study|nothing to race|no campuses-at-once/i.test(msg)) {
        setNote(`This backend has no plan race (${msg}): replaying a recorded race.`)
        setSource('recorded')
      }
    })
    return () => {
      off = true
    }
  }, [source, mode, mw])

  return (
    <div className="prc-preview">
      <div className="prc-preview__bar">
        <Field label="Source" as="select" value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="live">This backend</option>
          <option value="recorded">A recorded race</option>
        </Field>
        <Field label="Campus type" as="select" value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="firm">Always on</option>
          <option value="flexible">Flexible</option>
        </Field>
        {source === 'live' && (
          <Field label="Campus size" as="select" value={mw} onChange={(e) => setMw(Number(e.target.value))}>
            {[500, 1000, 2000, 5000].map((v) => (
              <option key={v} value={v}>
                {v.toLocaleString('en-US')} MW
              </option>
            ))}
          </Field>
        )}
        <p className="prc-preview__cb">
          <span className="prc-preview__cb-l">onShowPlan:</span>{' '}
          {plan ? `${plan.name}, ${plan.campuses} campuses, ${plan.projects?.length ?? 0} upgrades, ${money(plan.cost?.high)}${plan.verified ? ' (verified)' : ''}` : 'null (the page’s own plan)'}
        </p>
      </div>
      {note && <p className="prc-preview__note">{note}</p>}
      <PlanRacePanel key={source} client={source === 'live' ? liveClient : fixtureClient} mode={mode} mw={source === 'live' ? mw : 1000} onShowPlan={setPlan} />
    </div>
  )
}
