// FEATURE: the one small label every AI-made result carries. Says who made it and, when the engine
// re-checked it, that too — so a viewer never has to guess what is measured and what is proposed.
// by: 'gemini' (proposed or written by Gemini) | 'engine' (computed) | 'fallback' (the plain version ran)
import './ai.css'

const TEXT = {
  gemini: 'Gemini',
  engine: 'Engine',
  fallback: 'Plain version (Gemini unavailable)',
}

export default function AiBadge({ by = 'gemini', verified = false, title }) {
  const label = TEXT[by] || TEXT.gemini
  return (
    <span className={`aib aib--${by}`} title={title || (verified ? 'Proposed by Gemini, re-run and checked by the power-flow engine' : undefined)}>
      <span className="aib__dot" aria-hidden="true" />
      {label}
      {verified && by === 'gemini' && <span className="aib__check"> · engine-verified</span>}
    </span>
  )
}
