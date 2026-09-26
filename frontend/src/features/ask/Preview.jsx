// #/preview/ask — Ask Overload on fixed cases (the Fort Myers hero first), or on whatever is on the
// map right now. Mounted for real, AskBox sits in the review stage's drawer and in the workspace.
import { useState } from 'react'
import { useOverload } from '../../store'
import AskBox from './AskBox'
import { caseForCascade } from './askApi'
import './ask.css'

const HERO = { lat: 26.64, lon: -81.87 }
const CASES = [
  { id: 'hero', label: 'Fort Myers · 1,500 MW', body: { region: 'FL', ...HERO, mw: 1500 } },
  { id: 'heat', label: 'Heat wave · 500 MW', body: { region: 'FL', ...HERO, mw: 500, load_factor: 1.04 } },
  { id: 'calm', label: 'Fits · 400 MW', body: { region: 'FL', ...HERO, mw: 400 } },
  { id: 'cat5', label: 'Category 5 crosses Florida', body: { region: 'FL', ...HERO, mw: 1500, preset: 'fl-cat5-statewide' } },
]

export default function Preview() {
  const { cascade, caseBody } = useOverload()
  const [pick, setPick] = useState('hero')
  const mapCase = cascade ? caseForCascade(cascade, caseBody) : caseBody?.lat != null || caseBody?.sites?.length ? caseBody : null
  const body = pick === 'map' ? mapCase : CASES.find((c) => c.id === pick)?.body
  return (
    <div className="ask-preview">
      <div className="ask-preview__cases" role="group" aria-label="Scenario to ask about">
        {CASES.map((c) => (
          <button key={c.id} type="button" className="ask__chip" aria-pressed={pick === c.id} onClick={() => setPick(c.id)}>
            {c.label}
          </button>
        ))}
        <button type="button" className="ask__chip" aria-pressed={pick === 'map'} disabled={!mapCase} onClick={() => setPick('map')}>
          What&apos;s on the map
        </button>
      </div>
      <AskBox key={pick} caseBody={body || null} />
    </div>
  )
}
