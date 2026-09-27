import { useEffect, useState } from 'react'
import { api } from '../../api'
import './judges.css'

// "For judges: where each challenge shows up" (Data page, first tab). Five rows, one per challenge: what the project
// does for it as the demo actually shows it, and where to try it. Every link is a hash route on this same site (no
// hosts), so it works locally and deployed. Read-only; nothing here plays audio or asks the server for anything but
// the address of the top Build together pair.

// The Fort Myers hero: 1,500 MW is the demo case. The AI operator visibly helps at 1,000 MW (at 1,500 MW the
// blackout is the same size with or without it), so its link uses the smaller campus.
const HERO = '#/?at=26.6406,-81.8723&mw=1500'
const HERO_1000 = '#/?at=26.6406,-81.8723&mw=1000'

// The address of Build together's top pair, step 3 (the agents' plans and "Hear the negotiation"). Asked once for the
// visit; until it answers, or if it can't, the link opens Build together's ranked list instead.
let topPair = null
function useTopPairHref() {
  const [id, setId] = useState(topPair)
  useEffect(() => {
    if (topPair) return undefined
    let live = true
    api('/api/gridlock/opportunities?limit=1')
      .then((d) => {
        const first = d?.opportunities?.[0]?.id
        if (first) {
          topPair = first
          if (live) setId(first)
        }
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])
  return id ? `#/plans/pair/${encodeURIComponent(id).replace(/%7E/gi, '~')}/plans` : '#/plans'
}

// The "How AI is used" panel lives in the top bar; this opens the same panel (no second copy of it on the page).
function openHowAiIsUsed() {
  document.querySelector('.appbar__ai')?.click()
}

function TryLink({ href, onClick, children, hint }) {
  return (
    <li className="jg__try-item">
      {onClick ? (
        <button type="button" className="jg__link" onClick={onClick}>
          {children}
        </button>
      ) : (
        <a className="jg__link" href={href}>
          {children}
        </a>
      )}
      {hint && <span className="jg__hint">{hint}</span>}
    </li>
  )
}

export default function JudgesGuide() {
  const pairHref = useTopPairHref()

  const rows = [
    {
      id: 'overall',
      name: 'Best Overall',
      what: 'Drop a 1,500 MW AI campus at Fort Myers on a synthetic model of Florida’s grid, run the cascade, see who loses power, present the damage, then see the fix.',
      tries: [{ href: HERO, label: 'Drop 1,500 MW at Fort Myers', hint: 'Then press Run the cascade.' }],
    },
    {
      id: 'sperry',
      name: 'Sperry Tech',
      sub: 'The GridLock Challenge',
      what: 'Build together compares the public construction plans of Dominion Energy South Carolina and Georgia Power (Georgia’s other transmission owners can be switched on) and flags overlaps in place and in time. The data pipeline behind it extracts, cleans and validates the filings, and three AI agents propose ways to build together, with every figure checked against the filings.',
      tries: [
        { href: '#/plans', label: 'Open Build together', hint: 'Pick a pair from the ranked list.' },
        { href: '#/plans/pipeline', label: 'How we built the data', hint: 'The pipeline from PDFs to ranked overlaps.' },
      ],
    },
    {
      id: 'gemini',
      name: 'MLH',
      sub: 'Best Use of Gemini API',
      what: 'Gemini acts as a grid operator, proposes the fixes and drafts plans for each company; the engine or the filings check every number, and a labeled plain version runs without the key.',
      tries: [
        { href: HERO_1000, label: 'Open the 1,000 MW case', hint: 'Run the cascade, then press Let an AI operator try, or Present the damage for the options Gemini’s agents proposed.' },
        { onClick: openHowAiIsUsed, label: 'Open How AI is used', hint: 'Every AI feature, how it is checked and what runs without it.' },
      ],
    },
    {
      id: 'elevenlabs',
      name: 'MLH',
      sub: 'Best Use of ElevenLabs',
      what: 'Two voices narrate the presentation and the stories, and each company’s agent speaks in its own voice when it reads the plans on Build together. Sound is off until you turn it on; if the server has no voice key, the presentation uses the browser’s voice and Build together says the voice isn’t set up.',
      tries: [
        { href: HERO, label: 'Open the Fort Myers case', hint: 'Run the cascade, press Present the damage, then turn Sound on.' },
        { href: pairHref, label: 'Hear the agents’ plans', hint: 'Opens the top pair at step 3: press Turn sound on and hear it.' },
      ],
    },
    {
      id: 'microsoft',
      name: 'Microsoft',
      sub: 'What’s Missing?',
      what: 'Proposed data centers gives a resident the sourced facts about a real proposal, the questions to ask, where to speak and a one-page brief before a county votes, tested on a synthetic model, never a prediction about the real project.',
      tries: [{ href: '#/vote', label: 'Open Proposed data centers', hint: 'Pick one of the proposals.' }],
    },
  ]

  return (
    <section className="jg" aria-labelledby="jg-title" data-testid="judges-guide">
      <h2 className="vw-h2" id="jg-title">
        For judges: where each challenge shows up
      </h2>
      <ul className="jg__list">
        {rows.map((r) => (
          <li key={r.id} className="jg__row" data-challenge={r.id}>
            <div className="jg__who">
              <h3 className="jg__name">{r.name}</h3>
              {r.sub && <p className="jg__sub">{r.sub}</p>}
            </div>
            <p className="jg__what">{r.what}</p>
            <div className="jg__try">
              <p className="jg__try-label">Try it</p>
              <ul className="jg__try-list">
                {r.tries.map((t) => (
                  <TryLink key={t.label} href={t.href} onClick={t.onClick} hint={t.hint}>
                    {t.label}
                  </TryLink>
                ))}
              </ul>
            </div>
          </li>
        ))}
      </ul>
      <p className="jg__note">The grid is a synthetic model (Breakthrough Energy / Texas A&amp;M, CC BY 4.0), never a real utility’s network.</p>
    </section>
  )
}
