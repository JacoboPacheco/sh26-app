import { fmt } from '../../geo'
import { useTween } from './useShowClock'

// Small animated parts the show's slides share.

// A number that counts to its value (whole numbers by default).
export function Count({ value, ms = 900, delay = 0, active = true, from = 0, ease, format = fmt }) {
  const v = useTween(Number(value) || 0, { ms, delay, active, from, ease })
  return <>{format(v)}</>
}

// How loaded a line is: a bar out to 200%, a limit mark at 100%, red past it. The fill draws in (CSS).
export function Gauge({ pct, delay = 0, max = 200, tone }) {
  const p = Number(pct) || 0
  return (
    <span
      className={`sh-gauge${tone ? ` sh-gauge--${tone}` : p > 100 ? ' sh-gauge--over' : ''}`}
      style={{ '--w': `${Math.min(100, (p / max) * 100)}%`, '--lim': `${(100 / max) * 100}%`, '--d': `${delay}ms` }}
      aria-hidden="true"
    >
      <span className="sh-gauge__fill" />
      <span className="sh-gauge__limit" />
    </span>
  )
}

// a small label above a big figure
export function Kicker({ children, tone }) {
  return (
    <p className={`sh-kicker${tone ? ` sh-kicker--${tone}` : ''}`}>
      <span className="sh-kicker__dot" aria-hidden="true" />
      {children}
    </p>
  )
}
