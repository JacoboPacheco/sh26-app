import { VERDICTS } from './forecastApi'
import './forecast.css'

// The verdict before you run, as a pill: "Holds" (quiet), "Over limit" (amber), "Blackout" (red).
// Never color alone: each has its own icon and word.
export default function VerdictBadge({ verdict, size = 'md' }) {
  const v = VERDICTS[verdict]
  if (!v) return null
  return (
    <span className={`fc-verdict fc-verdict--${verdict} fc-verdict--${size}`} title={v.hint}>
      <Icon verdict={verdict} />
      {v.label}
    </span>
  )
}

function Icon({ verdict }) {
  if (verdict === 'holds') {
    return (
      <svg className="fc-verdict__icon" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M2.5 6.3 5 8.6 9.6 3.6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  }
  if (verdict === 'over_limit') {
    return (
      <svg className="fc-verdict__icon" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M6 1.4 11 10.4H1Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
        <path d="M6 4.6v2.6M6 8.7v.1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    )
  }
  return (
    <svg className="fc-verdict__icon" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="4.2" fill="currentColor" />
    </svg>
  )
}
